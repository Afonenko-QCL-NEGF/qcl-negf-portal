"""Exact repository selection and bounded local transport to owning Results.

No scientific formula, YAML resolution, export or native data inference lives here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
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
        from .saved_nfs import catalogue, perform, provider

        if operation == "sources":
            if query:
                raise SavedResultError("selection_required")
            result = catalogue(self, selection)
            check()
            return result
        source, query = provider(query)
        if operation not in ("brief", "manifest", "slice"):
            raise SavedResultError("unsupported_contract")
        allowed = (
            {"cursor"}
            if operation == "brief"
            else {"point_id", "source_commit_sha256", "metadata_sha256", "cursor"}
        )
        if operation == "slice":
            allowed |= {"view_id", "selection"}
        if set(query) - allowed:
            raise SavedResultError("selection_required")
        if query.get("cursor") is not None and (
            not isinstance(query["cursor"], str) or len(query["cursor"]) > 96
        ):
            raise SavedResultError("selection_required")
        if operation != "brief":
            if (
                not isinstance(query.get("point_id"), str)
                or not 1 <= len(query["point_id"]) <= 128
                or any(
                    not isinstance(query.get(k), str) or not HASH.fullmatch(query[k])
                    for k in ("source_commit_sha256", "metadata_sha256")
                )
            ):
                raise SavedResultError("selection_required")
        if operation == "slice":
            native = query.get("selection")
            if (
                not isinstance(query.get("view_id"), str)
                or not re.fullmatch(r"[A-Za-z0-9_]{1,128}", query["view_id"])
                or not isinstance(native, list)
                or not 1 <= len(native) <= 2
            ):
                raise SavedResultError("selection_required")
            for item in native:
                if type(item) is int and item >= 0:
                    continue
                if (
                    not isinstance(item, dict)
                    or set(item) != {"start", "stop", "step"}
                    or any(type(v) is not int for v in item.values())
                    or not 0 <= item["start"] < item["stop"]
                    or item["step"] < 1
                ):
                    raise SavedResultError("selection_required")
        if source["provider"] == "nfs_registered":
            return perform(self, operation, selection, source, query, cancelled, deadline, check)
        selectors = {k: selection[k] for k in ("attempt", "calcjob_uuid")}
        args = (selection["run_uuid"], selection["execution_id"])
        bound = self.service.get_export_selection(*args, **selectors)
        if any(
            type(bound.get(k)) is not type(selection[k]) or bound.get(k) != selection[k]
            for k in ("execution_id", "attempt", "calcjob_uuid")
        ):
            raise SavedResultError("identity_mismatch")
        check()
        frozen = self.service.get_export_plan(*args, **selectors)
        plan_raw = frozen.get("plan")
        if not isinstance(plan_raw, bytes):
            raise SavedResultError("corrupt_result")
        if len(plan_raw) > METADATA:
            raise SavedResultError("budget_exceeded")
        plan = _json(plan_raw)
        check()
        inventory = self.service.list_execution_artifacts(*args, **selectors)
        if not isinstance(inventory, list) or len(inventory) > 10000:
            raise SavedResultError("budget_exceeded")
        if len(_bytes(inventory)) > METADATA:
            raise SavedResultError("budget_exceeded")
        files = {}
        for row in inventory:
            if any(
                type(row.get(k)) is not type(selection[k]) or row.get(k) != selection[k]
                for k in ("execution_id", "attempt", "calcjob_uuid")
            ):
                raise SavedResultError("identity_mismatch")
            name = _path(row.get("path"))
            size = row.get("size")
            if name in files or type(size) is not int or size < 0:
                raise SavedResultError("corrupt_result")
            files[name] = size
        used_meta = len(plan_raw)
        copied = 0

        def copy(name, maximum, destination=None, expected=None):
            nonlocal used_meta, copied
            check()
            name = _path(name)
            if name not in files:
                raise SavedResultError("unavailable_storage")
            size = files[name]
            if size > maximum or copied + size > TEMPORARY:
                raise SavedResultError("budget_exceeded")
            if destination is None and used_meta + size > METADATA:
                raise SavedResultError("budget_exceeded")
            chunks = [] if destination is None else None
            digest = hashlib.sha256()
            count = 0
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
            with self.service.open_artifact(
                *args, name, **selectors, close_observer=self._lease_closed
            ) as stream:
                handle = destination.open("xb") if destination is not None else None
                try:
                    while True:
                        check()
                        data = stream.read(min(65536, size - count + 1))
                        if not data:
                            break
                        count += len(data)
                        if count > size:
                            raise SavedResultError("corrupt_result")
                        digest.update(data)
                        if handle is not None:
                            handle.write(data)
                        else:
                            chunks.append(data)
                finally:
                    if handle is not None:
                        handle.close()
            if count != size or (expected is not None and digest.hexdigest() != expected):
                raise SavedResultError("corrupt_result")
            copied += count
            if destination is None:
                used_meta += count
            return b"".join(chunks) if chunks is not None else None

        series_raw = copy("result/series_result.json", METADATA)
        series = _json(series_raw)
        digest = hashlib.sha256(
            len(plan_raw).to_bytes(8, "big") + plan_raw + series_raw + _bytes(selection)
        ).hexdigest()
        cursor = query.get("cursor")
        offset = 0
        if cursor:
            try:
                pin, number = cursor.split(":")
                offset = int(number)
                if not HASH.fullmatch(pin) or pin != digest or str(offset) != number or offset < 0:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError) as exc:
                raise SavedResultError("identity_mismatch") from exc
        if query.get("metadata_sha256", digest) != digest:
            raise SavedResultError("identity_mismatch")
        metadata = {
            "plan": plan,
            "series": series,
            "plan_source": frozen.get("source"),
            "source": {
                "kind": "aiida_retrieved",
                "locator_id": selection["calcjob_uuid"],
                "calcjob_uuid": selection["calcjob_uuid"],
            },
        }
        check()
        brief = self.brief(metadata, selection=selection, maximum_bytes=RESPONSE)
        check()
        common = {
            "provider_selection": selection,
            "provider_source": source,
            "metadata_sha256": digest,
            "provider_limits": {
                "metadata_bytes": METADATA,
                "owner_bytes": OWNER,
                "temporary_bytes": TEMPORARY,
                "response_bytes": RESPONSE,
                "wall_seconds": WALL,
                "analysis_tasks": 1,
                "child_cpu_count": 1,
                "child_address_space_bytes": 512 * 1024 * 1024,
            },
            "provider_measurements": {"kernel_io": "not_measured", "rss": "not_measured"},
        }
        if operation == "brief":
            points = brief["points"]
            total = len(points)
            if offset > total:
                raise SavedResultError("selection_required")
            page = [dict(p) for p in points[offset : offset + 256]]
            ids = {p["point_id"] for p in page}
            rows = {
                row["id"]: row
                for row in series.get("points", [])
                if row.get("execution_id") == selection["execution_id"]
            }
            for point in page:
                if point.get("source_commit_sha256") is not None:
                    point["source_commit_binding"] = "series_recorded"
                    continue
                reference = (rows.get(point["point_id"], {}).get("data") or {}).get("result_commit")
                if reference is None:
                    point["native_view_unavailable"] = "missing_dataset"
                    continue
                remote = "result/" + _path(reference)
                if remote not in files:
                    point["native_view_unavailable"] = "unavailable_storage"
                    continue
                captured = copy(remote, METADATA)
                point["source_commit_sha256"] = hashlib.sha256(captured).hexdigest()
                point["source_commit_binding"] = "provider_captured_sha256"
            brief = {
                **brief,
                "points": page,
                "current_segments": [
                    [p for p in segment if p in ids]
                    for segment in brief.get("current_segments", [])
                    if any(p in ids for p in segment)
                ],
            }
            return {
                **brief,
                **common,
                "repository_copied_bytes": copied,
                "total": total,
                "complete_page": offset == 0 and len(page) == total,
                "next_cursor": f"{digest}:{offset + len(page)}"
                if offset + len(page) < total
                else None,
            }
        if not all(query.get(k) for k in ("point_id", "source_commit_sha256", "metadata_sha256")):
            raise SavedResultError("selection_required")
        if not HASH.fullmatch(query["source_commit_sha256"]):
            raise SavedResultError("selection_required")
        rows = [
            r
            for r in series.get("points", [])
            if r.get("id") == query["point_id"]
            and r.get("execution_id") == selection["execution_id"]
        ]
        if len(rows) != 1:
            raise SavedResultError("missing_dataset")
        row = rows[0]
        data = row.get("data") or {}
        relative = _path(data.get("result_commit"))
        commit_raw = copy("result/" + relative, METADATA)
        commit_sha = hashlib.sha256(commit_raw).hexdigest()
        if (
            commit_sha != query["source_commit_sha256"]
            or data.get("source_commit_sha256", commit_sha) != commit_sha
        ):
            raise SavedResultError("identity_mismatch")
        commit = _json(commit_raw)
        from qcl_negf_contracts.artifacts import validate_commit

        declarations = validate_commit(commit)
        owners = [d for d in declarations if d.role == "physics.analysis"]
        if len(owners) != 1:
            raise SavedResultError("unsupported_contract")
        owner = owners[0]
        if owner.size > OWNER:
            raise SavedResultError("budget_exceeded")
        source_attempt = data.get("checkpoint_source_attempt", row.get("attempt"))
        if type(source_attempt) is not int or not 1 <= source_attempt <= selection["attempt"]:
            raise SavedResultError("identity_mismatch")
        expected = {
            "run_uuid": selection["run_uuid"],
            "execution_id": selection["execution_id"],
            "calcjob_uuid": selection["calcjob_uuid"],
            "attempt": source_attempt,
            "point_id": query["point_id"],
            "plan_fingerprint": plan["fingerprint"],
            "source_commit_sha256": commit_sha,
        }
        for key, wanted in expected.items():
            if key == "source_commit_sha256":
                continue
            if key in commit["identity"] or key in {
                "point_id",
                "execution_id",
                "attempt",
                "plan_fingerprint",
            }:
                actual = commit["identity"].get(key)
                if type(actual) is not type(wanted) or actual != wanted:
                    raise SavedResultError("identity_mismatch")
        bypath = {d.path: d for d in declarations}
        closure = {owner.path}
        pending = list(owner.dependencies)
        while pending:
            path = pending.pop()
            if path in closure:
                continue
            dep = bypath[path]
            if dep.media_type != "application/json":
                raise SavedResultError("unsupported_contract")
            used_meta += dep.size
            if used_meta > METADATA:
                raise SavedResultError("budget_exceeded")
            closure.add(path)
            pending.extend(dep.dependencies)
        # Refuse all required file sizes before opening any scientific payload.
        parent = PurePosixPath(relative).parent
        for path in closure:
            remote = _path(str(PurePosixPath("result") / parent / _path(path)))
            if remote not in files:
                raise SavedResultError("unavailable_storage")
            if files[remote] != bypath[path].size:
                raise SavedResultError("corrupt_result")
        if copied + sum(bypath[path].size for path in closure) > TEMPORARY:
            raise SavedResultError("budget_exceeded")
        temporary = tempfile.TemporaryDirectory(prefix="qcl-saved-")
        try:
            root = Path(temporary.name)
            local = root / relative
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(commit_raw)
            for path in sorted(closure):
                declaration = bypath[path]
                copy(
                    str(PurePosixPath("result") / parent / path),
                    OWNER if path == owner.path else METADATA,
                    root / parent / path,
                    declaration.sha256,
                )
            request = {"commit_path": relative, "expected_identity": expected}
            if operation == "slice":
                if not isinstance(query.get("view_id"), str) or not isinstance(
                    query.get("selection"), list
                ):
                    raise SavedResultError("selection_required")
                request.update(view_id=query["view_id"], selection=query["selection"])
            check()
            result = self.worker(operation, request, root, cancelled, deadline)
            check()
        except BaseException as exc:
            if getattr(exc, "cleanup_unconfirmed", False):
                self.closed = self.lease_cleanup_unconfirmed = True
                self.pending_spools.append(temporary)
                temporary = None
            raise
        finally:
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OSError as exc:
                    self.closed = True
                    raise SavedResultError("unavailable_storage") from exc
        return {
            **result,
            **common,
            "repository_copied_bytes": copied,
            "source_attempt": source_attempt,
            "point_id": query["point_id"],
            "total": len(result.get("views", [])),
            "complete_page": True,
            "next_cursor": None,
        }


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
            error = SavedResultError("unavailable_storage")
            error.cleanup_unconfirmed = True
            raise error from exc


def _read_request(request, spool):
    from qcl_negf_results.saved_views import describe_saved_views, read_saved_view

    if "data_root" in request or "provider_source" in request:
        from .saved_nfs import child_access

        root = child_access(request)
    else:
        root = spool
    commit = root / _path(request["commit_path"])
    if request["operation"] == "manifest":
        return describe_saved_views(commit, expected_identity=request["expected_identity"])
    if request["operation"] == "slice":
        return read_saved_view(
            commit,
            request["view_id"],
            request["selection"],
            expected_identity=request["expected_identity"],
        )
    raise SavedResultError("unsupported_contract")


def _main():
    import resource

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (RESPONSE, RESPONSE))
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
        request = _json(Path(sys.argv[1]).read_bytes())
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
