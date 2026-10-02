"""One bounded preparation worker over immutable retrieved repositories.

Scientific selection, validation and archive identity belong to results. This
adapter owns temporary storage, admission, progress and download lifetimes.
"""

import logging
import shutil
import tempfile
import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from qcl_negf_contracts.artifacts import validate_export_receipt
from qcl_negf_contracts.messages import TERMINAL

from .settings import Settings

LOG = logging.getLogger(__name__)
BLOCK = 65536
HEADROOM = 256 * 1024**2


class ExportBusy(ValueError):
    """The single preparation worker is occupied."""


class ExportStorage(ValueError):
    """The source snapshot cannot fit the configured storage admission budget."""


class ExportPlan(RuntimeError):
    """A new archive cannot silently omit its frozen input provenance."""


class ExportStore:
    def __init__(self, service: Any, settings: Settings) -> None:
        self.service, self.settings = service, settings
        self._temporary = tempfile.TemporaryDirectory(prefix="qcl-negf-exports-")
        self._root = Path(self._temporary.name)
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._entries: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qcl-negf-export")

    def _cleanup(self) -> None:
        now = time.time()
        for identifier, entry in list(self._entries.items()):
            if (entry["state"] != "preparing" and entry["expires_unix"] <= now
                    and not entry["readers"]):
                shutil.rmtree(self._root / identifier, ignore_errors=True)
                del self._entries[identifier]

    @staticmethod
    def _public(entry: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in entry.items() if key not in {"readers", "reserved"}}

    def get(self, identifier: str) -> dict[str, Any]:
        with self._lock:
            self._cleanup()
            entry = self._entries.get(identifier)
            if entry is None or entry["expires_unix"] <= time.time():
                raise LookupError("Export is unavailable or expired; prepare it again")
            return self._public(entry)

    def create(self, run: str, execution_id: str, profile: str) -> dict[str, Any]:
        entries = [item for item in self.service.list_artifacts(run)
                   if item["execution_id"] == execution_id]
        if not entries:
            raise LookupError("Retrieved execution artifacts are not available")
        seen: set[str] = set()
        for item in entries:
            path, size = item["path"], item["size"]
            relative = PurePosixPath(path)
            if (not path or relative.is_absolute() or ".." in relative.parts or "\\" in path
                    or str(relative) != path or path in seen or type(size) is not int or size < 0):
                raise ValueError("Retrieved inventory is invalid")
            seen.add(path)
        source_bytes = sum(item["size"] for item in entries)
        # Include source copy, pin spool, derived objects and finalized archive.
        # This conservative reservation is admission, not a filesystem quota.
        reservation = 4 * source_bytes + HEADROOM
        with self._lock:
            self._cleanup()
            if self._cancelled.is_set():
                raise RuntimeError("Export service is stopping")
            for identifier, entry in list(self._entries.items()):
                if ((entry["run_uuid"], entry["execution_id"], entry["profile"])
                        == (run, execution_id, profile) and entry["expires_unix"] > time.time()):
                    if entry["state"] in {"preparing", "ready"}:
                        return self._public(entry)
                    del self._entries[identifier]
            if any(entry["state"] == "preparing" for entry in self._entries.values()):
                raise ExportBusy("Another archive is being prepared; try again after it finishes")
            used = sum(entry["reserved"] for entry in self._entries.values())
            if (len(self._entries) >= 128 or used + reservation > self.settings.export_disk_bytes
                    or reservation > shutil.disk_usage(self._root).free):
                raise ExportStorage("Complete retrieved snapshot exceeds available export storage")
            identifier = str(uuid4())
            entry = {
                "export_id": identifier, "run_uuid": run, "execution_id": execution_id,
                "profile": profile, "state": "preparing", "progress": {"phase": "retrieving"},
                "expires_unix": time.time() + self.settings.export_ttl_seconds,
                "reserved": reservation, "readers": 0,
            }
            self._entries[identifier] = entry
            self._executor.submit(self._prepare, identifier, entries, source_bytes)
            return self._public(entry)

    def _progress(self, identifier: str, progress: dict[str, Any]) -> None:
        if self._cancelled.is_set():
            raise CancelledError("Archive preparation cancelled by service shutdown")
        with self._lock:
            entry = self._entries[identifier]
            entry["progress"] = dict(progress)
            # Actual compression bytes are reported at bounded write intervals.
            if progress.get("archive_bytes", 0) > entry["reserved"]:
                raise ExportStorage("Archive exceeds reserved export storage")

    def _prepare(self, identifier: str, inventory: list[dict[str, Any]], total: int) -> None:
        with self._lock:
            entry = self._entries[identifier]
        directory = self._root / identifier
        source, output = directory / "retrieved", directory / "archive"
        try:
            from qcl_negf_results.export import export_snapshot

            try:
                frozen = self.service.get_export_plan(entry["run_uuid"], entry["execution_id"])
            except (LookupError, ValueError, OSError) as exception:
                raise ExportPlan(
                    "Frozen plan is unavailable or invalid; export refused"
                ) from exception

            source.mkdir(parents=True)
            output.mkdir()
            self._progress(identifier, {"phase": "retrieving"})
            count = 0
            for item in inventory:
                target = source / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                with self.service.open_artifact(
                    entry["run_uuid"], entry["execution_id"], item["path"]
                ) as stream, target.open("xb") as handle:
                    while data := stream.read(BLOCK):
                        copied += len(data)
                        if copied > item["size"]:
                            raise ValueError("Retrieved artifact size differs from inventory")
                        handle.write(data)
                        count += len(data)
                        self._progress(identifier, {
                            "phase": "retrieving", "completed_bytes": count, "total_bytes": total,
                        })
                if copied != item["size"]:
                    raise ValueError("Retrieved artifact is truncated")
            result = self.service.get_run(entry["run_uuid"]).get("results", {}).get(
                entry["execution_id"], {}
            )
            status = result.get("status", "running") if isinstance(result, dict) else "running"
            # Process exit status is not evidence of scientific completion.
            status = status if status in TERMINAL else "running"
            receipt = export_snapshot(
                source / "result", output, profile=entry["profile"],
                job_id=entry["execution_id"], job_status=status,
                plan=frozen["plan"], plan_source=frozen["source"],
                progress=lambda progress: self._progress(identifier, progress),
            )
            validate_export_receipt(receipt)
            archive = output / receipt["archive"]
            if (archive.stat().st_size != receipt["bytes"]
                    or receipt["bytes"] > entry["reserved"]):
                raise ValueError("Archive size differs from receipt or storage reservation")
            shutil.rmtree(source)
            with self._lock:
                entry.update(state="ready", receipt=receipt, reserved=receipt["bytes"],
                             expires_unix=time.time() + self.settings.export_ttl_seconds)
        except Exception as exc:
            if isinstance(exc, CancelledError):
                LOG.info("Archive preparation cancelled for export %s", identifier)
            else:
                LOG.exception("Archive preparation failed for export %s", identifier)
            shutil.rmtree(directory, ignore_errors=True)
            error = (
                "Frozen plan is unavailable or invalid; inspect the control host service log"
                if isinstance(exc, ExportPlan) else
                "Archive preparation failed; inspect the control host service log"
            )
            with self._lock:
                entry.update(
                    state="failed", reserved=0,
                    error=error,
                )

    @contextmanager
    def open(self, identifier: str):
        with self._lock:
            entry = self.get(identifier)
            if entry["state"] != "ready":
                raise ExportBusy("The archive is not ready for download")
            self._entries[identifier]["readers"] += 1
            path = self._root / identifier / "archive" / entry["receipt"]["archive"]
        try:
            with path.open("rb") as stream:
                yield stream
        finally:
            with self._lock:
                self._entries[identifier]["readers"] -= 1

    def close(self) -> None:
        self._cancelled.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._temporary.cleanup()
