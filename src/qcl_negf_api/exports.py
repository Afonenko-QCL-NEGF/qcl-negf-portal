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
from uuid import UUID, uuid4

from qcl_negf_contracts.artifacts import validate_export_receipt
from qcl_negf_results.export_budget import DEFAULT_RESERVE_BYTES

from .service import ActorUnavailable, CloseResult
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
        # The retained store, never GC, decides when leases permit root cleanup.
        self._temporary._finalizer.detach()
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._lifecycle = threading.Condition()
        self._close_requested = False
        self._terminal = None
        self._maintenance_scheduled = False
        self._maintenance_running = False
        self._maintenance_dirty = False
        self._preparation = None
        self._lease_changed = threading.Event()
        self._expiry_pending = threading.Event()
        self._root_claimed = False
        self._cleanup_failure = None
        self._entries: dict[str, dict[str, Any]] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qcl-negf-export")

    def _cleanup(self) -> None:
        now = time.time()
        for identifier, entry in list(self._entries.items()):
            if (entry["state"] != "preparing" and entry["expires_unix"] <= now
                    and not entry["readers"]):
                entry["delete_pending"] = True
                self._expiry_pending.set()

    @staticmethod
    def _public(entry: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in entry.items()
                if key not in {"readers", "reserved", "source_bytes", "delete_pending",
                               "delete_claimed", "delete_error", "directory_deleted"}}

    @staticmethod
    def _stored_bytes(directory: Path) -> int:
        return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())

    def _storage(self, identifier: str, owned: int, additional: int = 0) -> int:
        """Observe remaining logical bytes/free space; this is not an OS quota."""
        with self._lock:
            entry = self._entries[identifier]
            other = sum(value["reserved"] for key, value in self._entries.items()
                        if key != identifier)
            remaining = min(entry["reserved"], self.settings.export_disk_bytes - other) - owned
            free = shutil.disk_usage(self._root).free - DEFAULT_RESERVE_BYTES
            if additional > remaining or additional > free:
                raise ExportStorage("Preparation exceeds reserved export storage or free space")
            return min(remaining, free)

    def get(self, identifier: str) -> dict[str, Any]:
        try:
            with self._lock:
                self._cleanup()
                entry = self._entries.get(identifier)
                if (entry is None or entry["expires_unix"] <= time.time()
                        or entry.get("delete_pending", False)):
                    raise LookupError("Export is unavailable or expired; prepare it again")
                return self._public(entry)
        finally:
            self._request_maintenance()

    def _selection_call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self.service, operation)(*args, **kwargs)
        except (ActorUnavailable, LookupError, ValueError):
            raise
        except Exception as exc:
            raise RuntimeError("Exact export selection service is unavailable") from exc

    def create(self, run: str, execution_id: str, profile: str, *,
               attempt=None, calcjob_uuid=None) -> dict[str, Any]:
        if self._cancelled.is_set():
            raise ExportBusy("Export service is stopping")
        try:
            return self._create(run, execution_id, profile,
                                attempt=attempt, calcjob_uuid=calcjob_uuid)
        finally:
            self._request_maintenance()

    def _create(self, run: str, execution_id: str, profile: str, *,
                attempt=None, calcjob_uuid=None) -> dict[str, Any]:
        try:
            if not isinstance(run, str):
                raise ValueError
            run = str(UUID(run))
        except (ValueError, AttributeError) as exc:
            raise ValueError("A full process UUID is required") from exc
        selection = self._selection_call(
            "get_export_selection", run, execution_id, attempt=attempt, calcjob_uuid=calcjob_uuid,
        )
        invalid = "Export selection metadata is inconsistent"
        if (not isinstance(selection, dict)
                or set(selection) != {"execution_id", "attempt", "calcjob_uuid", "status"}
                or not isinstance(selection["execution_id"], str)
                or selection["execution_id"] != execution_id
                or type(selection["attempt"]) is not int or selection["attempt"] < 1
                or not isinstance(selection["calcjob_uuid"], str)
                or not isinstance(selection["status"], str)
                or selection["status"] not in {
                    "running", "completed", "completed_with_warnings", "failed",
                    "cancelled", "paused", "unknown"}):
            raise ValueError(invalid)
        try:
            if str(UUID(selection["calcjob_uuid"])) != selection["calcjob_uuid"]:
                raise ValueError
        except ValueError as exc:
            raise ValueError(invalid) from exc
        # Detach before subsequent actor calls or cache access.
        binding = dict(selection)
        selectors = {key: binding[key] for key in ("attempt", "calcjob_uuid")}
        rows = self._selection_call(
            "list_execution_artifacts", run, execution_id, **selectors,
        )
        if not isinstance(rows, list) or len(rows) > 10000:
            raise ValueError(invalid)
        entries, seen = [], set()
        for item in rows:
            if (not isinstance(item, dict)
                    or set(item) != {"execution_id", "attempt", "calcjob_uuid", "path", "size"}
                    or any(item[key] != binding[key] for key in
                           ("execution_id", "attempt", "calcjob_uuid"))
                    or type(item["attempt"]) is not int
                    or not isinstance(item["path"], str)):
                raise ValueError(invalid)
            path, size = item["path"], item["size"]
            relative = PurePosixPath(path)
            if (not path or path == "." or "\x00" in path or relative.is_absolute()
                    or ".." in relative.parts or "\\" in path
                    or str(relative) != path or path in seen or type(size) is not int or size < 0):
                raise ValueError(invalid)
            seen.add(path)
            entries.append(dict(item))
        if not entries:
            raise LookupError("Retrieved execution artifacts are not available")
        source_bytes = sum(item["size"] for item in entries)
        # Include source copy, pin spool, derived objects and finalized archive.
        # This conservative reservation is admission, not a filesystem quota.
        reservation = 4 * source_bytes + HEADROOM
        with self._lock:
            self._cleanup()
            if self._cancelled.is_set():
                raise ExportBusy("Export service is stopping")
            for identifier, entry in list(self._entries.items()):
                if ((entry["run_uuid"], entry["execution_id"], entry["attempt"],
                     entry["calcjob_uuid"], entry["profile"])
                        == (run, execution_id, binding["attempt"], binding["calcjob_uuid"], profile)
                        and entry["expires_unix"] > time.time()
                        and not entry.get("delete_pending", False)):
                    if entry["state"] in {"preparing", "ready"}:
                        if entry["status"] == binding["status"]:
                            return self._public(entry)
                    elif entry.get("directory_deleted", False):
                        del self._entries[identifier]
                    else:
                        entry["delete_pending"] = True
                        self._expiry_pending.set()
            with self._lifecycle:
                maintenance = self._maintenance_scheduled
            if maintenance or any(
                entry["state"] == "preparing" for entry in self._entries.values()
            ):
                raise ExportBusy("Another archive is being prepared; try again after it finishes")
            used = sum(entry["reserved"] for entry in self._entries.values())
            if (len(self._entries) >= 128 or used + reservation > self.settings.export_disk_bytes
                    or reservation + DEFAULT_RESERVE_BYTES > shutil.disk_usage(self._root).free):
                raise ExportStorage("Complete retrieved snapshot exceeds available export storage")
            # disk_usage may stall while close independently installs its latch.
            with self._lifecycle:
                if self._close_requested or self._maintenance_scheduled:
                    raise ExportBusy("Export service is stopping or cleaning temporary output")
            identifier = str(uuid4())
            entry = {
                "export_id": identifier, "run_uuid": run, **binding,
                "profile": profile, "state": "preparing", "progress": {"phase": "retrieving"},
                "expires_unix": time.time() + self.settings.export_ttl_seconds,
                "reserved": reservation, "readers": 0, "source_bytes": source_bytes,
            }
            self._entries[identifier] = entry
            # Registration is completed before releasing the store lock. A close
            # task queued first observes and cancels this exact queued preparation.
            future = self._executor.submit(self._prepare, identifier, entries, source_bytes)
            with self._lifecycle:
                self._preparation = future
            return self._public(entry)

    def _progress(self, identifier: str, progress: dict[str, Any]) -> None:
        if self._cancelled.is_set():
            raise CancelledError("Archive preparation cancelled by service shutdown")
        with self._lock:
            entry = self._entries[identifier]
            entry["progress"] = dict(progress)
            # Results temporary_bytes already includes its compressed archive.
            temporary = max(progress.get("temporary_bytes", 0), progress.get("archive_bytes", 0))
            self._storage(identifier, entry["source_bytes"] + temporary)

    def _prepare(self, identifier: str, inventory: list[dict[str, Any]], total: int) -> None:
        with self._lock:
            entry = self._entries[identifier]
        directory = self._root / identifier
        source, output = directory / "retrieved", directory / "archive"
        try:
            from qcl_negf_results.export import export_snapshot

            if self._cancelled.is_set():
                raise CancelledError("Archive preparation cancelled by service shutdown")
            try:
                frozen = self.service.get_export_plan(
                    entry["run_uuid"], entry["execution_id"],
                    attempt=entry["attempt"], calcjob_uuid=entry["calcjob_uuid"],
                )
            except (LookupError, ValueError, OSError) as exception:
                raise ExportPlan(
                    "Frozen plan is unavailable or invalid; export refused"
                ) from exception

            source.mkdir(parents=True)
            output.mkdir()
            self._progress(identifier, {"phase": "retrieving"})
            count = 0
            for item in inventory:
                if self._cancelled.is_set():
                    raise CancelledError("Archive preparation cancelled by service shutdown")
                target = source / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                copied = 0
                with self.service.open_artifact(
                    entry["run_uuid"], entry["execution_id"], item["path"],
                    attempt=entry["attempt"], calcjob_uuid=entry["calcjob_uuid"],
                ) as stream, target.open("xb") as handle:
                    while True:
                        if self._cancelled.is_set():
                            raise CancelledError(
                                "Archive preparation cancelled by service shutdown"
                            )
                        data = stream.read(BLOCK)
                        if not data:
                            break
                        copied += len(data)
                        if copied > item["size"]:
                            raise ValueError("Retrieved artifact size differs from inventory")
                        self._storage(identifier, count, len(data))
                        handle.write(data)
                        count += len(data)
                        self._progress(identifier, {
                            "phase": "retrieving", "completed_bytes": count, "total_bytes": total,
                        })
                if copied != item["size"]:
                    raise ValueError("Retrieved artifact is truncated")
            byte_budget = self._storage(identifier, total)
            if byte_budget <= 0:
                raise ExportStorage("No export spool budget remains after the retrieved copy")
            receipt = export_snapshot(
                source / "result", output, profile=entry["profile"],
                job_id=entry["execution_id"], job_status=entry["status"],
                plan=frozen["plan"], plan_source=frozen["source"],
                byte_budget=byte_budget, reserve_bytes=DEFAULT_RESERVE_BYTES,
                progress=lambda progress: self._progress(identifier, progress),
            )
            validate_export_receipt(receipt)
            archive = output / receipt["archive"]
            if archive.stat().st_size != receipt["bytes"]:
                raise ValueError("Archive size differs from receipt")
            # Reconcile receipt/metadata tails and any remaining spool before ready.
            self._storage(identifier, self._stored_bytes(directory))
            try:
                shutil.rmtree(source)
            except FileNotFoundError:
                pass
            except OSError:
                # A failed source-removal claim cannot be retried indirectly
                # through its parent directory or a later root cleanup.
                with self._lock:
                    entry["delete_claimed"] = True
                    entry["delete_error"] = self._cleanup_failure = "store_cleanup_failed"
                raise
            retained = self._stored_bytes(directory)
            with self._lock:
                self._storage(identifier, retained)
                with self._lifecycle:
                    if self._close_requested:
                        raise CancelledError("Archive preparation cancelled by service shutdown")
                    entry.update(state="ready", receipt=receipt, reserved=retained,
                                 expires_unix=time.time() + self.settings.export_ttl_seconds)
        except Exception as exc:
            if isinstance(exc, ActorUnavailable):
                LOG.info("Archive actor unavailable operation=%s outcome=%s",
                         exc.receipt.operation_id, exc.receipt.outcome)
            elif isinstance(exc, CancelledError):
                LOG.info("Archive preparation cancelled for export %s", identifier)
            else:
                LOG.exception("Archive preparation failed for export %s", identifier)
            removed = self._remove_directory(identifier, retain_metadata=True)
            error = (
                "Frozen plan is unavailable or invalid; inspect the control host service log"
                if isinstance(exc, ExportPlan) else
                "Archive preparation failed; inspect the control host service log"
            )
            with self._lock:
                entry.update(
                    state="failed", reserved=0 if removed else entry["reserved"],
                    error=error,
                )
        finally:
            if self._cancelled.is_set():
                self._request_maintenance()

    @contextmanager
    def open(self, identifier: str):
        if self._cancelled.is_set():
            raise ExportBusy("Export service is stopping")
        try:
            with self._lock:
                if self._cancelled.is_set():
                    raise ExportBusy("Export service is stopping")
                self._cleanup()
                original = self._entries.get(identifier)
                if (original is None or original["expires_unix"] <= time.time()
                        or original.get("delete_pending", False)):
                    raise LookupError("Export is unavailable or expired; prepare it again")
                if original["state"] != "ready":
                    raise ExportBusy("The archive is not ready for download")
                original["readers"] += 1
                path = self._root / identifier / "archive" / original["receipt"]["archive"]
            try:
                with path.open("rb") as stream:
                    yield stream
            finally:
                with self._lock:
                    original["readers"] -= 1
                self._lease_changed.set()
        finally:
            self._request_maintenance()

    def _request_maintenance(self):
        # No store-lock acquisition or Future callbacks under lifecycle control.
        if not self._cancelled.is_set() and not self._expiry_pending.is_set():
            return
        with self._lifecycle:
            if self._terminal is not None:
                return
            self._maintenance_dirty = True
            if self._maintenance_scheduled:
                return
            self._maintenance_scheduled = True
        try:
            self._executor.submit(self._maintain)
        except RuntimeError:
            with self._lifecycle:
                self._terminal = CloseResult("failed", None, None, "store_cleanup_failed")
                self._lifecycle.notify_all()

    def _remove_directory(self, identifier, *, retain_metadata=False):
        with self._lock:
            entry = self._entries[identifier]
            if entry.get("directory_deleted", False):
                if not retain_metadata:
                    del self._entries[identifier]
                return True
            if entry.get("delete_claimed", False) or entry["readers"]:
                return False
            entry["delete_claimed"] = True
        try:
            shutil.rmtree(self._root / identifier)
        except FileNotFoundError:
            pass
        except OSError:
            with self._lock:
                entry["delete_error"] = "store_cleanup_failed"
                self._cleanup_failure = "store_cleanup_failed"
            return False
        with self._lock:
            entry["reserved"] = 0
            entry["directory_deleted"] = True
            if not retain_metadata:
                del self._entries[identifier]
        return True

    def _counts(self):
        operations = int(self._preparation is not None and not self._preparation.done())
        readers = sum(entry["readers"] for entry in self._entries.values())
        return operations, readers

    def _maintain(self):
        with self._lifecycle:
            self._maintenance_running = True
        try:
            while True:
                with self._lifecycle:
                    self._maintenance_dirty = False
                    closing = self._close_requested
                self._lease_changed.clear()
                with self._lock:
                    # A close queued before a late create submit must cancel its
                    # preparation before treating the directory as writer-free.
                    preparation = self._preparation
                if closing and preparation is not None:
                    preparation.cancel()  # eager callbacks run outside both locks
                with self._lock:
                    operations, readers = self._counts()
                    self._expiry_pending.clear()
                    if closing and not operations:
                        for entry in self._entries.values():
                            entry["delete_pending"] = True
                    deletions = [identifier for identifier, entry in self._entries.items()
                                 if entry.get("delete_pending", False) and not entry["readers"]
                                 and not entry.get("delete_claimed", False)
                                 and (entry["state"] != "preparing" or not operations)]
                for identifier in deletions:
                    self._remove_directory(identifier)
                with self._lock:
                    operations, readers = self._counts()
                    failure = self._cleanup_failure
                if closing and failure is not None:
                    result = CloseResult("failed", operations, readers, failure)
                    self._finish(result)
                    return
                if closing and not operations and not readers:
                    with self._lifecycle:
                        if not self._root_claimed:
                            self._root_claimed = True
                            cleanup = True
                        else:
                            cleanup = False
                    if cleanup:
                        try:
                            self._temporary.cleanup()
                        except OSError:
                            self._finish(CloseResult("failed", 0, 0, "store_cleanup_failed"))
                            return
                    self._finish(CloseResult("completed", 0, 0))
                    return
                if closing:
                    self._lease_changed.wait()
                    continue
                with self._lifecycle:
                    if self._maintenance_dirty or self._close_requested:
                        continue
                    self._maintenance_running = self._maintenance_scheduled = False
                    return
        except BaseException:
            self._finish(CloseResult("failed", None, None, "store_cleanup_failed"))

    def _finish(self, result):
        with self._lifecycle:
            self._terminal = result
            self._maintenance_running = self._maintenance_scheduled = False
            self._lifecycle.notify_all()
        self._executor.shutdown(wait=False)

    def close(self, *, deadline: float | None = None) -> CloseResult:
        local = time.monotonic() + self.settings.actor_close_timeout_seconds
        deadline = local if deadline is None else min(local, deadline)
        with self._lifecycle:
            if self._terminal is not None:
                return self._terminal
            self._close_requested = True
            self._cancelled.set()
            preparation = self._preparation
        if preparation is not None:
            preparation.cancel()  # callbacks must never run under lifecycle control
        self._lease_changed.set()
        self._request_maintenance()
        remaining = max(0, deadline - time.monotonic())
        acquired = (self._lock.acquire(timeout=remaining) if remaining
                    else self._lock.acquire(blocking=False))
        operations = readers = None
        if acquired:
            try:
                operations, readers = self._counts()
            finally:
                self._lock.release()
        with self._lifecycle:
            while self._terminal is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return CloseResult("stopping", operations, readers,
                                       None if acquired else "store_metadata_pending")
                self._lifecycle.wait(remaining)
            return self._terminal
