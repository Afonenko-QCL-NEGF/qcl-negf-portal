import hashlib
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
        get_export_plan=lambda run, execution: {
            "plan": b'{ "frozen": 1.234567890123456789 }\n',
            "source": "aiida.input.plan",
        },
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


def test_export_forwards_exact_frozen_plan_bytes_and_source(monkeypatch):
    payload = b"small complete archive"
    digest = hashlib.sha256(payload).hexdigest()
    calls = []

    def exporting(root, destination, **kwargs):
        calls.append(kwargs)
        (destination / f"{digest}.tar.xz").write_bytes(payload)
        return {
            "schema": "qcl-negf.science-export.v3",
            "contract_set": "qcl-negf.results.v1",
            "transport_schema": "qcl-negf.export-archive.v1",
            "profile": "science",
            "snapshot_identity": "b" * 64,
            "sha256": digest,
            "bytes": len(payload),
            "filename": "test-science.tar.xz",
            "archive": f"{digest}.tar.xz",
        }

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,)))
    try:
        entry = store.create("run", "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "ready"
        assert calls[0]["plan"] == b'{ "frozen": 1.234567890123456789 }\n'
        assert calls[0]["plan_source"] == "aiida.input.plan"
    finally:
        store.close()


def test_invalid_frozen_plan_refuses_export_before_source_copy(monkeypatch):
    service = source_service()

    def invalid(*args):
        raise ValueError("Invalid retrieved frozen plan")

    service.get_export_plan = invalid
    service.open_artifact = lambda *args: (_ for _ in ()).throw(
        AssertionError("copied source before validating plan")
    )
    monkeypatch.setattr(
        "qcl_negf_results.export.export_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("exported missing plan")),
    )
    store = ExportStore(service, Settings(TOKEN, (CODE,)))
    try:
        entry = store.create("run", "point_1", "science")
        result = wait_terminal(store, entry["export_id"])
        assert result["state"] == "failed"
        assert "Frozen plan" in result["error"]
    finally:
        store.close()
