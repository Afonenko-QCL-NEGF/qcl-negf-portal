import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

from qcl_negf_api.service import AiidaService


def test_aiida_profile_operations_and_artifact_handles_have_one_thread_owner(monkeypatch):
    import aiida
    import aiida.manage

    events = []

    def record(name):
        events.append((name, threading.get_ident(), asyncio.get_event_loop()))

    def load_profile(profile):
        assert profile == "example"
        record("load")

    def list_runs(**kwargs):
        record("list")
        return []

    class Stream(BytesIO):
        def read(self, size):
            record("read")
            return super().read(size)

    @contextmanager
    def open_artifact(*args):
        record("open")
        try:
            yield Stream(b"data")
        finally:
            record("close")

    plugin = SimpleNamespace(list_runs=list_runs, open_artifact=open_artifact)
    monkeypatch.setattr(aiida, "load_profile", load_profile)
    monkeypatch.setattr("qcl_negf_api.service.import_module", lambda name: plugin)
    monkeypatch.setattr(
        aiida.manage,
        "get_manager",
        lambda: SimpleNamespace(unload_profile=lambda: record("unload")),
    )

    actor = AiidaService("example")
    try:
        with ThreadPoolExecutor(max_workers=4) as clients:
            futures = [clients.submit(actor.list_runs, limit=10) for _ in range(8)]
            assert all(future.result() == [] for future in futures)
        with actor.open_artifact("run", "execution", "result") as stream:
            assert stream.read(4) == b"data"
    finally:
        actor.close()

    assert [event[0] for event in events] == [
        "load",
        *(["list"] * 8),
        "open",
        "read",
        "close",
        "unload",
    ]
    assert len({event[1] for event in events}) == 1
    assert events[0][1] != threading.get_ident()
    assert all(event[2] is events[0][2] for event in events)
    assert events[0][2].is_closed()
