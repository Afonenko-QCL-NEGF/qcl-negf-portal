"""Exact repository selection and bounded local transport to owning Results.

No scientific formula, YAML resolution, export or native data inference lives here.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from uuid import UUID

from .service import ActorUnavailable, CloseResult

METADATA = 1024 * 1024
OWNER = 64 * 1024 * 1024
TEMPORARY = 128 * 1024 * 1024
RESPONSE = 2 * 1024 * 1024
WALL = 10.0
HASH = re.compile(r"[0-9a-f]{64}")
ERRORS = {
    "selection_required",
    "identity_mismatch",
    "unsupported_contract",
    "missing_dataset",
    "unavailable_storage",
    "budget_exceeded",
    "corrupt_result",
    "busy",
    "stopping",
    "source_changed",
    "capability_unavailable",
}


class SavedResultError(Exception):
    def __init__(self, code):
        code = {
            "source_identity_mismatch": "identity_mismatch",
            "request_conflict": "identity_mismatch",
            "source_selection_required": "selection_required",
            "source_unavailable": "unavailable_storage",
            "source_not_quiescent": "unavailable_storage",
            "invalid_binding": "unavailable_storage",
        }.get(code, code)
        self.code = code if code in ERRORS else "corrupt_result"
        super().__init__(self.code)


def _pairs(items):
    result = {}
    for k, v in items:
        if k in result:
            raise SavedResultError("corrupt_result")
        result[k] = v
    return result


def _json(raw):
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        todo, count = [(value, 0)], 0
        while todo:
            item, depth = todo.pop()
            count += 1
            if count > 100000 or depth > 64:
                raise ValueError()
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError()
            if isinstance(item, str):
                item.encode("utf-8")
            if isinstance(item, dict):
                todo.extend((v, depth + 1) for v in (*item.keys(), *item.values()))
            elif isinstance(item, list):
                todo.extend((v, depth + 1) for v in item)
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError) as exc:
        raise SavedResultError("corrupt_result") from exc


def _bytes(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, UnicodeError) as exc:
        raise SavedResultError("corrupt_result") from exc
    if len(raw) > RESPONSE:
        raise SavedResultError("budget_exceeded")
    return raw


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise SavedResultError("corrupt_result")
    p = PurePosixPath(value)
    if (
        p.is_absolute()
        or str(p) != value
        or ".." in p.parts
        or value == "."
        or any(c in value for c in ("\\", "\x00", ":"))
    ):
        raise SavedResultError("corrupt_result")
    return value


def _selection(value):
    if set(value) != {"run_uuid", "execution_id", "attempt", "calcjob_uuid"}:
        raise SavedResultError("selection_required")
    try:
        for key in ("run_uuid", "calcjob_uuid"):
            if str(UUID(value[key])) != value[key]:
                raise ValueError()
        if type(value["attempt"]) is not int or value["attempt"] < 1:
            raise ValueError()
        if not isinstance(value["execution_id"], str) or not 1 <= len(value["execution_id"]) <= 128:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise SavedResultError("selection_required") from exc
    return dict(value)


def _diagnostic_artifacts(commit):
    """Validate only the selected history closure; leave unrelated owners unopened."""
    from qcl_negf_contracts.artifacts import Artifact, validate_commit

    validate_commit({**commit, "artifacts": []})
    rows = commit.get("artifacts")
    if not isinstance(rows, list) or len(rows) > 4096:
        raise SavedResultError("budget_exceeded")
    bypath = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SavedResultError("corrupt_result")
        name = _path(row.get("path"))
        if name in bypath:
            raise SavedResultError("corrupt_result")
        bypath[name] = row
    owners = [row for row in rows if row.get("role") == "science.history"]
    if len(owners) != 1:
        return ()
    owner = owners[0]
    if (
        owner.get("schema") != "qcl-negf-scientific-history-v4"
        or owner.get("media_type") != "application/x-hdf5"
    ):
        raise SavedResultError("unsupported_contract")
    selected = {}
    pending = [owner["path"]]
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in bypath:
            raise SavedResultError("corrupt_result")
        artifact = Artifact.parse(bypath[name])
        if name != owner["path"] and artifact.media_type != "application/json":
            raise SavedResultError("unsupported_contract")
        selected[name] = bypath[name]
        pending.extend(artifact.dependencies)
    return validate_commit({**commit, "artifacts": list(selected.values())})


def _history_selection(phase, selection):
    if phase not in ("scba", "outer") or not isinstance(selection, dict):
        raise SavedResultError("selection_required")
    if set(selection) != {"start", "stop", "step", "columns"}:
        raise SavedResultError("selection_required")
    if (
        any(type(selection[k]) is not int for k in ("start", "stop", "step"))
        or not 0 <= selection["start"] < selection["stop"]
        or selection["step"] < 1
    ):
        raise SavedResultError("selection_required")
    if (selection["stop"] - selection["start"] + selection["step"] - 1) // selection["step"] > 512:
        raise SavedResultError("budget_exceeded")
    columns = selection["columns"]
    if (
        not isinstance(columns, list)
        or not 1 <= len(columns) <= 16
        or any(
            not isinstance(v, str)
            or not 1 <= len(v) <= 128
            or any(c in v for c in ("/", "\\", "\x00"))
            or any(ord(c) < 32 for c in v)
            or v in {"sequence", "iteration", "outer_iteration", "source_attempt"}
            for v in columns
        )
        or len(set(columns)) != len(columns)
    ):
        raise SavedResultError("selection_required")


def _brief(metadata, **kwargs):
    from qcl_negf_results.saved_views import brief_result

    return brief_result(metadata, **kwargs)


class SavedResults:
    """One request/cleanup at a time, including detached actor leases and temp files."""

    def __init__(
        self, service, settings, *, brief=None, worker=None, cleanup_seconds=1.0, binding=None
    ):
        from .saved_nfs import load_binding, validate_binding

        self.binding = (
            load_binding(getattr(settings, "saved_results_binding_file", None))
            if binding is None
            else validate_binding(binding)
        )
        self.service, self.settings = service, settings
        self.brief = brief or _brief
        self.worker = worker or _worker
        self.busy = False
        self.closed = False
        self.lease_cleanup_unconfirmed = False
        self.pending_spools = []
        self.active = None
        self.cancelled = None
        self.cleanup_seconds = cleanup_seconds
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qcl-saved")

    def _lease_closed(self, result):
        # A completed wrapper/future cannot establish that the owner has closed
        # its real lease. Pending/failed/unknown cleanup permanently disables
        # this optional consumer; the actor still owns exact-once physical exit.
        if (
            not isinstance(result, CloseResult)
            or result.state != "completed"
            or result.pending_leases != 0
            or result.pending_operations != 0
        ):
            self.lease_cleanup_unconfirmed = True
            self.closed = True

    async def close(self):
        self.closed = True
        future, cancelled = self.active, self.cancelled
        if cancelled is not None:
            cancelled.set()
        if future is not None:
            await self._cleanup_wait(asyncio.wrap_future(future))
        self.executor.shutdown(wait=False, cancel_futures=True)

    async def _cleanup_wait(self, task):
        task.add_done_callback(
            lambda actual: actual.exception() if not actual.cancelled() else None
        )
        guard = asyncio.create_task(asyncio.wait_for(asyncio.shield(task), self.cleanup_seconds))
        await _settle(guard)

    async def read(self, operation, selection, query, disconnect=None):
        selection = _selection(selection)
        if self.closed:
            raise SavedResultError("stopping")
        if not self.settings.saved_results_enabled:
            raise SavedResultError("unavailable_storage")
        if self.busy:
            raise SavedResultError("busy")
        self.busy = True
        cancelled = self.cancelled = threading.Event()
        deadline = time.monotonic() + WALL
        future = self.executor.submit(
            self._perform, operation, selection, query, cancelled, deadline
        )
        self.active = future

        def completed(actual):
            # The concurrent future describes actual thread completion even if
            # ASGI/event-loop tasks are cancelled. No live temp/lease is released
            # merely because its asyncio waiter stopped waiting.
            if self.active is actual:
                self.active = self.cancelled = None
                if not actual.cancelled():
                    error = actual.exception()
                    if getattr(error, "cleanup_unconfirmed", False) or (
                        isinstance(error, ActorUnavailable) and error.receipt.outcome == "unknown"
                    ):
                        self.lease_cleanup_unconfirmed = self.closed = True
                self.busy = self.lease_cleanup_unconfirmed

        future.add_done_callback(completed)
        task = asyncio.wrap_future(future)

        async def watch():
            while True:
                if time.monotonic() >= deadline:
                    raise SavedResultError("budget_exceeded")
                if disconnect is not None and await disconnect():
                    raise SavedResultError("unavailable_storage")
                await asyncio.sleep(0.025)

        watcher = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait([task, watcher], return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
            result = await asyncio.shield(task)
            _bytes(result)
            return result
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except Exception as exc:
            cancelled.set()
            if isinstance(exc, SavedResultError):
                raise
            code = (
                "unavailable_storage"
                if isinstance(exc, ActorUnavailable)
                else getattr(exc, "code", "unavailable_storage")
            )
            raise SavedResultError(code) from exc
        finally:
            cancelled.set()
            watcher.cancel()
            # Bound caller cleanup latency. A stalled owner keeps the real
            # future/gate and its temporary inputs; further reads fail closed.
            await self._cleanup_wait(task)
            await _settle(watcher)
            if not future.done():
                self.closed = True

    def _perform(self, operation, selection, query, cancelled, deadline):
        def check():
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise SavedResultError("budget_exceeded")

        check()
        if operation in {"live-source", "live-history", "performance"}:
            from .live_nfs import perform

            return perform(self, operation, selection, query, cancelled, deadline, check)
        if operation in {"sources", "brief"}:
            # R1 requires its owning native execution-wide producer.
            raise SavedResultError("unsupported_contract")
        # point-source shares the actual future/gate but returns before any RO child.
        from .saved_nfs import native_perform

        return native_perform(self, operation, selection, query, cancelled, deadline, check)


async def _settle(task):
    while True:
        try:
            await asyncio.shield(task)
            return
        except asyncio.CancelledError:
            if task.done():
                return
        except Exception:
            return


def _worker(operation, request, root, cancelled, deadline):
    if cancelled.is_set() or time.monotonic() >= deadline:
        raise SavedResultError("budget_exceeded")
    payload = root / "request.json"
    payload.write_bytes(_bytes({"operation": operation, **request}))
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")}
    env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    from importlib import import_module

    env["PYTHONPATH"] = os.pathsep.join(
        dict.fromkeys(
            str(Path(import_module(name).__file__).resolve().parent.parent)
            for name in ("qcl_negf_api", "qcl_negf_results", "qcl_negf_contracts")
        )
    )
    child = subprocess.Popen(
        [sys.executable, "-m", "qcl_negf_api.saved_results", str(payload)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
        env=env,
    )
    output = bytearray()
    errors = 0
    primary = None
    try:
        with selectors.DefaultSelector() as poll:
            poll.register(child.stdout, selectors.EVENT_READ, "stdout")
            poll.register(child.stderr, selectors.EVENT_READ, "stderr")
            while poll.get_map():
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise SavedResultError("budget_exceeded")
                for key, _ in poll.select(0.025):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        poll.unregister(key.fileobj)
                        continue
                    if key.data == "stdout":
                        if len(output) + len(chunk) > RESPONSE:
                            raise SavedResultError("budget_exceeded")
                        output.extend(chunk)
                    else:
                        errors += len(chunk)
                        if errors > 4096:
                            raise SavedResultError("budget_exceeded")
            try:
                child.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise SavedResultError("budget_exceeded") from exc
        value = _json(output)
        if child.returncode or "error" in value:
            raise SavedResultError(value.get("error", "unsupported_contract"))
        return value
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            child.stdout.close()
            child.stderr.close()
        except BaseException as exc:
            if primary is not None:
                primary.cleanup_unconfirmed = True
            else:
                error = SavedResultError("unavailable_storage")
                error.cleanup_unconfirmed = True
                raise error from exc


def _read_request(request, spool):
    if request.get("operation") in {"live-source", "live-history", "performance"}:
        from .live_nfs import read_request

        return read_request(request, spool)
    from .saved_nfs import native_read_request

    return native_read_request(request)


def _main():
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
        request = _json(Path(sys.argv[1]).read_bytes())
        # JSON remains capped at 2 MiB; only live selected leaves need 64 MiB.
        file_limit = OWNER if request.get("operation") == "live-history" else RESPONSE
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        result = _read_request(request, Path(sys.argv[1]).parent)
        sys.stdout.buffer.write(_bytes(result))
    except Exception as exc:
        sys.stdout.buffer.write(
            json.dumps({"error": getattr(exc, "code", "unsupported_contract")}).encode()
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main())
