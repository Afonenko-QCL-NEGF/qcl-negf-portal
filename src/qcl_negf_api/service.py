"""Own AiiDA's manager, event loop and repository handles in one thread."""

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from importlib import import_module
from typing import Any

OPERATIONS = frozenset(
    {
        "list_runs",
        "get_run",
        "get_export_plan",
        "submit_plan",
        "get_run_report",
        "kill_run",
        "list_artifacts",
    }
)


class _ArtifactReader:
    def __init__(self, actor: AiidaService, handle: Any) -> None:
        self._actor, self._handle = actor, handle

    def read(self, size: int) -> bytes:
        if not 0 <= size <= 65536:
            raise ValueError("Artifact reads must use chunks of at most 65536 bytes")
        return self._actor._executor.submit(self._handle.read, size).result()

    def seek(self, offset: int) -> int:
        if type(offset) is not int or offset < 0:
            raise ValueError("Artifact seek requires a nonnegative byte offset")
        return self._actor._executor.submit(self._handle.seek, offset).result()


class AiidaService:
    """Serialize calls to the process-global AiiDA manager behind the HTTP layer.

    HTTP workers never own AiiDA state. The dedicated worker also gets an explicit
    asyncio loop, required by synchronous AiiDA operations on Python 3.14.
    """

    def __init__(self, profile: str) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qcl-negf-aiida")
        try:
            self._executor.submit(self._initialize, profile).result()
        except BaseException:
            self._executor.shutdown(wait=True, cancel_futures=True)
            raise

    def _initialize(self, profile: str) -> None:
        from aiida import load_profile

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            load_profile(profile)
            self._service = import_module("aiida_qcl_negf.service")
        except BaseException:
            self._loop.close()
            raise

    def __getattr__(self, name: str):
        if name not in OPERATIONS:
            raise AttributeError(name)
        return partial(self._call, name)

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self._executor.submit(getattr(self._service, name), *args, **kwargs).result()

    @contextmanager
    def open_artifact(self, identifier: str, execution_id: str, path: str):
        def enter():
            context = self._service.open_artifact(identifier, execution_id, path)
            return context, context.__enter__()

        context, handle = self._executor.submit(enter).result()
        try:
            yield _ArtifactReader(self, handle)
        finally:
            self._executor.submit(context.__exit__, *sys.exc_info()).result()

    def close(self) -> None:
        def shutdown():
            from aiida.manage import get_manager

            try:
                get_manager().unload_profile()
            finally:
                if not self._loop.is_closed():
                    self._loop.run_until_complete(self._loop.shutdown_asyncgens())
                    self._loop.close()
                asyncio.set_event_loop(None)

        try:
            self._executor.submit(shutdown).result()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=True)
