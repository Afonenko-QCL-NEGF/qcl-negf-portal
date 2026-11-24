import hashlib
import json
import tempfile
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from qcl_negf_api.exports import ExportStorage, ExportStore
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


def publish_test_archive(destination, profile, payload=b"complete archive", tail=0):
    """Storage fixture only; scientific selection/validation is not exercised."""
    digest = hashlib.sha256(payload).hexdigest()
    receipt = {
        "schema": "qcl-negf.science-export.v3", "contract_set": "qcl-negf.results.v1",
        "transport_schema": "qcl-negf.export-archive.v1", "profile": profile,
        "snapshot_identity": "b" * 64, "sha256": digest, "bytes": len(payload),
        "filename": "test-export.tar.xz", "archive": f"{digest}.tar.xz",
    }
    (destination / receipt["archive"]).write_bytes(payload)
    (destination / ("b" * 64 + ".json")).write_text(json.dumps(receipt) + " " * tail)
    return receipt


def test_derived_spool_expansion_uses_the_admitted_budget(monkeypatch):
    from qcl_negf_results.export_budget import (
        DEFAULT_BYTE_BUDGET,
        DEFAULT_RESERVE_BYTES,
        ExportBudget,
    )

    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 64)
    observed = {}

    def exporting(root, destination, **kwargs):
        # The same actual write guard as results; compressed output is tiny,
        # but a derived object exceeds the 80-byte reservation less source4.
        with tempfile.TemporaryDirectory(dir=destination) as directory:
            budget = ExportBudget(
                directory, byte_budget=kwargs.get("byte_budget", DEFAULT_BYTE_BUDGET),
                reserve_bytes=kwargs.get("reserve_bytes", DEFAULT_RESERVE_BYTES),
            )
            observed["byte_budget"] = budget.byte_budget
            observed["reserve_bytes"] = budget.reserve_bytes
            with budget.open(Path(directory) / "derived.bin", "xb") as stream:
                stream.write(b"x" * 77)
            return publish_test_archive(destination, kwargs["profile"], b"small")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        entry = store.create("run", "point_1", "science")
        result = wait_terminal(store, entry["export_id"])
        assert result["state"] == "failed"
        assert observed["byte_budget"] == 76
        assert observed["reserve_bytes"] == DEFAULT_RESERVE_BYTES
        assert not (store._root / entry["export_id"]).exists()
    finally:
        store.close()


def test_spool_budget_uses_free_space_after_source_retrieval(monkeypatch):
    from qcl_negf_results.export_budget import DEFAULT_RESERVE_BYTES

    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 64)
    service = source_service()
    original = service.open_artifact

    @contextmanager
    def retrieving(*args):
        # Another filesystem user consumes space after admission, before export.
        monkeypatch.setattr("qcl_negf_api.exports.shutil.disk_usage",
                            lambda path: SimpleNamespace(free=DEFAULT_RESERVE_BYTES + 40))
        with original(*args) as stream:
            yield stream

    service.open_artifact = retrieving
    observed = []

    def exporting(root, destination, **kw):
        observed.append(kw["byte_budget"])
        raise OSError("storage fixture stops before creating a receipt")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(service, Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        entry = store.create("run", "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "failed"
        assert observed == [40]
    finally:
        store.close()


def test_receipt_tail_cannot_publish_ready_beyond_reservation(monkeypatch):
    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 64)
    monkeypatch.setattr("qcl_negf_results.export.export_snapshot",
                        lambda root, destination, **kw:
                        publish_test_archive(destination, kw["profile"], b"small"))
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        entry = store.create("run", "point_1", "science")
        result = wait_terminal(store, entry["export_id"])
        assert result["state"] == "failed"
        assert "receipt" not in result
        assert not (store._root / entry["export_id"]).exists()
    finally:
        store.close()


def test_retained_receipt_counts_against_next_export_admission(monkeypatch):
    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 2048)
    monkeypatch.setattr("qcl_negf_results.export.export_snapshot",
                        lambda root, destination, **kw:
                        publish_test_archive(destination, kw["profile"]))
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,), export_disk_bytes=2120))
    try:
        entry = store.create("run", "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "ready"
        with pytest.raises(ExportStorage):
            store.create("run", "point_1", "full-state")
    finally:
        store.close()


def test_admission_leaves_the_results_filesystem_reserve(monkeypatch):
    from qcl_negf_results.export_budget import DEFAULT_RESERVE_BYTES

    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 64)
    monkeypatch.setattr("qcl_negf_api.exports.shutil.disk_usage",
                        lambda path: SimpleNamespace(free=80 + DEFAULT_RESERVE_BYTES - 1))
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        with pytest.raises(ExportStorage):
            store.create("run", "point_1", "science")
    finally:
        store.close()


def test_progress_checks_source_and_spool_not_only_compressed_bytes(monkeypatch):
    monkeypatch.setattr("qcl_negf_api.exports.HEADROOM", 64)
    refused = []

    def exporting(root, destination, **kw):
        (destination / "derived.bin").write_bytes(b"x" * 77)
        try:
            kw["progress"]({"phase": "compressing", "temporary_bytes": 77, "archive_bytes": 1})
        except ExportStorage:
            refused.append(True)
            raise
        return publish_test_archive(destination, kw["profile"], b"small")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source_service(), Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        entry = store.create("run", "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "failed"
        assert refused == [True]
    finally:
        store.close()
