import time
from contextlib import contextmanager
from io import BytesIO
from threading import Event
from types import SimpleNamespace

from qcl_negf_api.exports import ExportStore
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
CODE = "10dd2a4c-031b-425c-90b8-2f7ea564d313"


def source_service():
    @contextmanager
    def open_artifact(run, execution, path):
        assert (run, execution, path) == ("run", "point_1", "result/native.bin")
        yield BytesIO(b"data")

    return SimpleNamespace(
        list_artifacts=lambda run: [
            {"execution_id": "point_1", "path": "result/native.bin", "size": 4}
        ],
        open_artifact=open_artifact,
        get_run=lambda run: {"results": {}},
    )


def wait_terminal(store, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = store.get(identifier)
        if value["state"] != "preparing":
            return value
        time.sleep(0.01)
    raise AssertionError("Export preparation did not finish")


def test_failed_preparation_can_be_retried_explicitly(monkeypatch):
    def failed(*args, **kwargs):
        raise OSError("source validation failed")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", failed)
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,)))
    try:
        first = store.create("run", "point_1", "science")
        assert wait_terminal(store, first["export_id"])["state"] == "failed"
        second = store.create("run", "point_1", "science")
        assert second["export_id"] != first["export_id"]
        assert wait_terminal(store, second["export_id"])["state"] == "failed"
    finally:
        store.close()


def test_shutdown_cancels_preparation_at_the_next_progress_callback(monkeypatch):
    started = Event()

    def exporting(*args, **kwargs):
        started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            kwargs["progress"]({"phase": "compressing", "archive_bytes": 0})
            time.sleep(0.01)
        raise AssertionError("Preparation continued after shutdown")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,)))
    store.create("run", "point_1", "science")
    assert started.wait(2)
    before = time.monotonic()
    store.close()
    assert time.monotonic() - before < 1
