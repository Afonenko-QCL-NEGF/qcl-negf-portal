"""Bounded gateway to Platform's immutable, nonprivileged cache-only client."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import signal
import stat

STDOUT_BYTES = 1048577  # one MiB payload plus its required LF
STDERR_BYTES = 4096
DEADLINE_SECONDS = 6.0
CLEANUP_SECONDS = 1.0
MAX_INFLIGHT = 4
ENVIRONMENT = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"}
CLIENT = re.compile(r"/nix/store/[A-Za-z0-9][A-Za-z0-9+._?-]*/bin/qcl-negf-cluster-cache-status\Z")
ROOT_KEYS = {
    "schema",
    "read_at",
    "status",
    "registry_generation",
    "hosts",
    "complete",
    "next_cursor",
    "coverage",
    "current_context",
}


class ClusterStatusError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _immutable_client(value):
    """Inspect only the supplied immutable executable's namespace at startup."""
    if not isinstance(value, str) or not CLIENT.fullmatch(value):
        raise ValueError("invalid client binding")
    parts = value.split("/")[1:]
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            directory = index < len(parts) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
            info = os.fstat(descriptor)
            valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if info.st_uid != 0 or not valid_type:
                raise ValueError("invalid client binding")
            # Native /nix/store alone may be sticky and group writable.
            store = index == 1
            if info.st_mode & 0o002 or (
                info.st_mode & 0o020 and not (store and info.st_mode & stat.S_ISVTX)
            ):
                raise ValueError("invalid client binding")
            if not directory and not info.st_mode & 0o111:
                raise ValueError("invalid client binding")
    finally:
        os.close(descriptor)
    return value


def query_token(*, cursor=None, limit=64) -> str:
    if (
        type(limit) is not int
        or not 1 <= limit <= 128
        or cursor is not None
        and (not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,8192}", cursor))
    ):
        raise ClusterStatusError("cluster_cache_query_invalid")
    value = {"schema": "qcl.cluster-query.v1", "request_id": None, "cursor": cursor, "limit": limit}
    token = (
        base64.urlsafe_b64encode(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
        )
        .decode("ascii")
        .rstrip("=")
    )
    if len(token) > 8192:
        raise ClusterStatusError("cluster_cache_query_invalid")
    return token


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("nonfinite")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=nonfinite)


def _response(stdout):
    try:
        if not stdout.endswith(b"\n") or stdout.count(b"\n") != 1:
            raise ValueError("invalid framing")
        value = _json(stdout[:-1])
        if (
            not isinstance(value, dict)
            or set(value) != ROOT_KEYS
            or value["schema"] != "qcl.cluster-projection.v2"
            or value["status"] not in ("ok", "partial", "unknown")
            or type(value["read_at"]) is not int
            or value["read_at"] < 0
            or not isinstance(value["registry_generation"], str)
            or not 1 <= len(value["registry_generation"]) <= 128
            or not isinstance(value["hosts"], list)
            or len(value["hosts"]) > 128
            or type(value["complete"]) is not bool
            or value["next_cursor"] is not None
            and (not isinstance(value["next_cursor"], str) or len(value["next_cursor"]) > 8192)
            or not isinstance(value["coverage"], dict)
            or value["current_context"] is not None
            and not isinstance(value["current_context"], dict)
        ):
            raise ValueError("invalid response")
        # Platform's immutable client validates the full owning schema. Preserve
        # it; only enforce bounded JSON and exact browser number transport here.
        stack, count = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            count += 1
            if depth > 64 or count > 100000:
                raise ValueError("JSON tree bound")
            if type(item) is float and not math.isfinite(item):
                raise ValueError("nonfinite")
            integer_value = type(item) is int or type(item) is float and item.is_integer()
            if integer_value and abs(item) > 9007199254740991:
                raise ClusterStatusError("cluster_value_out_of_browser_range")
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                stack.extend((x, depth + 1) for x in (*item.keys(), *item.values()))
            elif isinstance(item, list):
                stack.extend((x, depth + 1) for x in item)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError) as error:
        raise ClusterStatusError("cluster_cache_invalid") from error


async def _read(stream, cap):
    raw = bytearray()
    while chunk := await stream.read(min(4096, cap + 1 - len(raw))):
        raw.extend(chunk)
        if len(raw) > cap:
            raise ClusterStatusError("cluster_cache_invalid")
    return bytes(raw)


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


class ClusterStatus:
    def __init__(self, settings):
        self._executable = None
        try:
            self._executable = _immutable_client(settings.cluster_cache_executable)
        except OSError, ValueError, AttributeError:
            pass  # Optional missing/untrusted capability remains unavailable.
        self._active = set()
        self._cleanups = set()
        self._closed = False

    async def _cleanup(self, launch, process, readers):
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                if process is None and launch is not None:
                    try:
                        process = await asyncio.shield(launch)
                    except OSError, ValueError:
                        pass
                if process is not None:
                    _signal_group(process, signal.SIGTERM)
                    if process.returncode is None:
                        try:
                            await asyncio.wait_for(asyncio.shield(process.wait()), 0.2)
                        except TimeoutError:
                            pass
                    # A completed parent can still leave a pipe-holding child.
                    _signal_group(process, signal.SIGKILL)
                    await process.wait()
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
            return True
        except OSError, TimeoutError:
            if launch is not None and not launch.done():
                launch.cancel()
            if process is not None:
                try:
                    _signal_group(process, signal.SIGKILL)
                except OSError:
                    pass  # Unknown cleanup cannot reopen admission.
            for task in readers:
                task.cancel()
            return False

    async def read(self, *, cursor=None, limit=64) -> dict:
        token = query_token(cursor=cursor, limit=limit)
        if self._closed or self._executable is None:
            raise ClusterStatusError("cluster_cache_unavailable")
        if len(self._active) >= MAX_INFLIGHT:
            raise ClusterStatusError("cluster_cache_busy")
        current = asyncio.current_task()
        self._active.add(current)
        launch, process, readers = None, None, []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + DEADLINE_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                launch = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        self._executable,
                        "--query-base64",
                        token,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=ENVIRONMENT,
                        start_new_session=True,
                        close_fds=True,
                        limit=8192,
                    )
                )
                process = await asyncio.shield(launch)
                readers = [
                    asyncio.create_task(_read(process.stdout, STDOUT_BYTES)),
                    asyncio.create_task(_read(process.stderr, STDERR_BYTES)),
                ]
                stdout, stderr, _ = await asyncio.gather(*readers, process.wait())
                if process.returncode != 0:
                    if process.returncode == 1 and not stdout:
                        try:
                            error = _json(stderr)
                            if (
                                isinstance(error, dict)
                                and set(error) == {"schema", "reason"}
                                and error
                                == {"schema": "qcl.monitoring-error.v1", "reason": "stale_cursor"}
                            ):
                                raise ClusterStatusError("stale_cursor")
                        except ValueError, TypeError, UnicodeError, RecursionError:
                            pass
                    raise ClusterStatusError("cluster_cache_unavailable")
                if stderr:
                    raise ClusterStatusError("cluster_cache_invalid")
                result = _response(stdout)
                if loop.time() >= deadline:
                    raise TimeoutError
                return result
        except TimeoutError as error:
            raise ClusterStatusError("cluster_cache_timeout") from error
        except (OSError, ValueError) as error:
            raise ClusterStatusError("cluster_cache_unavailable") from error
        finally:
            cleanup = asyncio.create_task(self._cleanup(launch, process, readers))
            self._cleanups.add(cleanup)

            def finished(task):
                self._cleanups.discard(task)
                self._active.discard(current)
                if task.cancelled() or task.exception() is not None or not task.result():
                    self._closed = True

            cleanup.add_done_callback(finished)
            if not await asyncio.shield(cleanup):
                self._closed = True
                raise ClusterStatusError("cluster_cache_unavailable")

    async def close(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        active = [task for task in self._active if task is not current]
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        if self._cleanups:
            await asyncio.gather(*tuple(self._cleanups), return_exceptions=True)
