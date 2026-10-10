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

    def get_export_plan(*args):
        record("plan")
        return {"plan": b"frozen bytes", "source": "aiida.input.plan"}

    class Stream(BytesIO):
        def read(self, size):
            record("read")
            return super().read(size)

        def seek(self, offset, whence=0):
            record("seek")
            return super().seek(offset, whence)

    def metadata(*args, **kwargs):
        assert args == ("run", "execution", "result")
        assert kwargs == {"attempt": 2, "calcjob_uuid": "exact-child"}
        record("metadata")
        return {"size": 4}

    @contextmanager
    def open_artifact(*args, **kwargs):
        assert kwargs == {"attempt": 2, "calcjob_uuid": "exact-child"}
        record("open")
        try:
            yield Stream(b"data")
        finally:
            record("close")

    def save_agent_report(run, raw):
        assert (run, raw) == ("run", b"exact agent report")
        record("agent_save")
        return {"uuid": "report", "filename": "agent-report.json", "bytes": len(raw)}

    def list_agent_reports(run, *, limit, offset):
        assert (run, limit, offset) == ("run", 7, 3)
        record("agent_list")
        return [{"uuid": "report"}]

    def read_agent_report(run, report):
        assert (run, report) == ("run", "report")
        record("agent_repository_open")
        try:
            with Stream(b"exact agent report") as handle:
                return handle.read(262145)
        finally:
            record("agent_repository_close")

    plugin = SimpleNamespace(
        save_agent_report=save_agent_report,
        list_agent_reports=list_agent_reports,
        read_agent_report=read_agent_report,
        list_runs=list_runs,
        open_artifact=open_artifact,
        get_export_plan=get_export_plan,
        get_artifact_metadata=metadata,
    )
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
        assert actor.get_export_plan("run", "execution")["plan"] == b"frozen bytes"
        assert actor.get_artifact_metadata(
            "run", "execution", "result", attempt=2, calcjob_uuid="exact-child"
        ) == {"size": 4}
        assert actor.save_agent_report("run", b"exact agent report") == {
            "uuid": "report", "filename": "agent-report.json", "bytes": 18,
        }
        assert actor.list_agent_reports("run", limit=7, offset=3) == [{"uuid": "report"}]
        assert actor.read_agent_report("run", "report") == b"exact agent report"
        with actor.open_artifact(
            "run", "execution", "result", attempt=2, calcjob_uuid="exact-child"
        ) as stream:
            stream.seek(1)
            assert stream.read(2) == b"at"
            stream.seek(0)
            assert stream.read(4) == b"data"
    finally:
        actor.close()

    assert [event[0] for event in events] == [
        "load",
        *(["list"] * 8),
        "plan",
        "metadata",
        "agent_save",
        "agent_list",
        "agent_repository_open",
        "read",
        "agent_repository_close",
        "open",
        "seek",
        "read",
        "seek",
        "read",
        "close",
        "unload",
    ]
    assert len({event[1] for event in events}) == 1
    assert events[0][1] != threading.get_ident()
    assert all(event[2] is events[0][2] for event in events)
    assert events[0][2].is_closed()
