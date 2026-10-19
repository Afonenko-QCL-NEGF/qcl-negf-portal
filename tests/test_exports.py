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

from qcl_negf_api.exports import ExportBusy, ExportStorage, ExportStore
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
CODE = "10dd2a4c-031b-425c-90b8-2f7ea564d313"
RUN = "e7f5313d-22fd-49fa-9be5-85fbe29e1974"


def source_service():
    @contextmanager
    def open_artifact(run, execution, path, *, attempt, calcjob_uuid):
        assert (run, execution, path) == (RUN, "point_1", "result/native.bin")
        yield BytesIO(b"data")

    return SimpleNamespace(
        get_export_selection=lambda run, execution, **kwargs: {
            "execution_id": execution, "attempt": 1, "calcjob_uuid": CODE, "status": "unknown"},
        list_execution_artifacts=lambda run, execution, **kwargs: [
            {"execution_id": "point_1", "attempt": 1, "calcjob_uuid": CODE,
             "path": "result/native.bin", "size": 4}
        ],
        open_artifact=open_artifact,
        get_run=lambda run: {"results": {}},
        get_export_plan=lambda run, execution, **kwargs: {
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
        first = store.create(RUN, "point_1", "science")
        assert wait_terminal(store, first["export_id"])["state"] == "failed"
        second = store.create(RUN, "point_1", "science")
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
    store.create(RUN, "point_1", "science")
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
        entry = store.create(RUN, "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "ready"
        assert calls[0]["plan"] == b'{ "frozen": 1.234567890123456789 }\n'
        assert calls[0]["plan_source"] == "aiida.input.plan"
    finally:
        store.close()


def test_invalid_frozen_plan_refuses_export_before_source_copy(monkeypatch):
    service = source_service()

    def invalid(*args, **kwargs):
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
        entry = store.create(RUN, "point_1", "science")
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
        entry = store.create(RUN, "point_1", "science")
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
    def retrieving(*args, **kwargs):
        # Another filesystem user consumes space after admission, before export.
        monkeypatch.setattr("qcl_negf_api.exports.shutil.disk_usage",
                            lambda path: SimpleNamespace(free=DEFAULT_RESERVE_BYTES + 40))
        with original(*args, **kwargs) as stream:
            yield stream

    service.open_artifact = retrieving
    observed = []

    def exporting(root, destination, **kw):
        observed.append(kw["byte_budget"])
        raise OSError("storage fixture stops before creating a receipt")

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(service, Settings(TOKEN, (CODE,), export_disk_bytes=80))
    try:
        entry = store.create(RUN, "point_1", "science")
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
        entry = store.create(RUN, "point_1", "science")
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
        entry = store.create(RUN, "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "ready"
        with pytest.raises(ExportStorage):
            store.create(RUN, "point_1", "full-state")
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
            store.create(RUN, "point_1", "science")
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
        entry = store.create(RUN, "point_1", "science")
        assert wait_terminal(store, entry["export_id"])["state"] == "failed"
        assert refused == [True]
    finally:
        store.close()


C2 = "20dd2a4c-031b-425c-90b8-2f7ea564d313"


@pytest.fixture
def composite_source(monkeypatch):
    """Real producer binding/plan/reads; synthetic ORM and immutable repositories."""
    from aiida_qcl_negf import service as plugin

    class Outputs(dict):
        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError as exc:
                raise AttributeError(key) from exc

    class File:
        def __init__(self, raw):
            self.raw = raw

        def open(self, mode):
            assert mode == "rb"
            return BytesIO(self.raw)

    plan = json.loads((Path(__file__).parents[2] / "qcl-negf-aiida/tests/fixtures/plan.json").read_bytes())
    execution = plan["executions"][0]["id"]
    plans = [json.dumps(plan, indent=1).encode() + b"\n", json.dumps(plan, indent=2).encode() + b"\n\n"]
    assert plans[0] != plans[1] and json.loads(plans[0]) == json.loads(plans[1])
    events, calls, children = [], [], []
    for attempt, uuid, raw, payload, status in [
        (1, CODE, plans[0], b"AAAA", "completed"),
        (2, C2, plans[1], b"BBBB", "failed"),
    ]:
        files = {"result/same.bin": payload, f"result/only{attempt}.bin": str(attempt).encode()}

        class Repository:
            def __init__(self, uuid, files, raw):
                self.uuid, self.files, self.raw = uuid, files, raw

            def get_object(self, path):
                assert path == "result/scientific_plan.json"
                return object()

            @contextmanager
            def open(self, path, mode):
                assert mode == "rb"
                events.append((self.uuid, path))
                with BytesIO(self.raw if path == "result/scientific_plan.json" else self.files[path]) as handle:
                    yield handle

        inventory = {"complete": True, "files": [{"path": path, "size": len(value)} for path, value in files.items()]}
        children.append(SimpleNamespace(
            uuid=uuid, exit_status=0,
            inputs=SimpleNamespace(execution_id=SimpleNamespace(value=execution), attempt=SimpleNamespace(value=attempt)),
            outputs=Outputs(
                retrieved=SimpleNamespace(base=SimpleNamespace(repository=Repository(uuid, files, raw))),
                inventory=SimpleNamespace(get_dict=lambda inventory=inventory: inventory),
                result=File(json.dumps({"selected_execution_id": execution, "status": status}).encode()),
            ),
        ))
    selection = {"execution_id": execution, "attempt": 2, "calcjob_uuid": C2}
    node = SimpleNamespace(inputs=SimpleNamespace(plan=File(plans[0])),
                           outputs=Outputs(selections={execution: SimpleNamespace(get_dict=lambda: dict(selection))}))
    monkeypatch.setattr(plugin, "_node", lambda identifier: node)
    monkeypatch.setattr(plugin, "_children", lambda node: children)

    def operation(name):
        def call(*args, **kwargs):
            calls.append((name, kwargs))
            return getattr(plugin, name)(*args, **kwargs)
        return call

    facade = SimpleNamespace(**{name: operation(name) for name in (
        "get_export_selection", "list_execution_artifacts", "get_export_plan", "open_artifact")})
    facade.get_run = lambda *args: (_ for _ in ()).throw(AssertionError("whole-run status read"))
    facade.list_artifacts = lambda *args: (_ for _ in ()).throw(AssertionError("all-attempt inventory read"))
    return SimpleNamespace(facade=facade, children=children, node=node, selection=selection,
                           events=events, calls=calls, plans=plans, execution=execution)


@pytest.mark.parametrize("switch_after_resolve", [False, True])
def test_export_pins_inventory_plan_payload_status_and_cache(composite_source, monkeypatch, switch_after_resolve):
    source = composite_source
    exported = []

    def exporting(root, destination, **kwargs):
        files = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        exported.append((files, kwargs))
        return publish_test_archive(destination, kwargs["profile"], files["same.bin"])

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    original = source.facade.get_export_selection

    def resolve(*args, **kwargs):
        binding = original(*args, **kwargs)
        if switch_after_resolve:
            source.selection.update(attempt=1, calcjob_uuid=CODE)
        return binding

    source.facade.get_export_selection = resolve
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        entry = store.create(RUN.upper(), source.execution, "science")
        ready = wait_terminal(store, entry["export_id"])
        assert ready["state"] == "ready", ready
        assert (ready["attempt"], ready["calcjob_uuid"], ready["status"]) == (2, C2, "failed")
        assert exported[0][0] == {"same.bin": b"BBBB", "only2.bin": b"2"}
        assert exported[0][1]["job_status"] == "failed"
        assert exported[0][1]["plan"] == source.plans[1]
        assert exported[0][1]["plan_source"] == "aiida.retrieved:result/scientific_plan.json"
        with store.open(entry["export_id"]) as stream:
            assert stream.read() == b"BBBB"
        assert len([name for name, _ in source.calls if name == "get_export_selection"]) == 1
        assert all(kwargs == {"attempt": 2, "calcjob_uuid": C2}
                   for name, kwargs in source.calls if name != "get_export_selection")
        assert all(uuid == C2 for uuid, path in source.events)
        cached = store.create(RUN, source.execution, "science", attempt=2, calcjob_uuid=C2.upper())
        assert cached["export_id"] == entry["export_id"]
        source.selection.update(attempt=1, calcjob_uuid=CODE)
        other = store.create(RUN, source.execution, "science")
        assert other["export_id"] != entry["export_id"]
        assert wait_terminal(store, other["export_id"])["status"] == "completed"
        assert exported[1][0] == {"same.bin": b"AAAA", "only1.bin": b"1"}
        assert exported[1][1]["plan"] == source.plans[0]
        assert store.get(entry["export_id"])["status"] == "failed"
    finally:
        store.close()


def test_same_child_new_status_preserves_old_export(composite_source, monkeypatch):
    source = composite_source
    child = source.children[1]
    result = child.outputs.pop("result")
    statuses = []

    def exporting(root, destination, **kwargs):
        statuses.append(kwargs["job_status"])
        return publish_test_archive(destination, kwargs["profile"])

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        old = store.create(RUN, source.execution, "science")
        assert wait_terminal(store, old["export_id"])["status"] == "unknown"
        child.outputs["result"] = result
        new = store.create(RUN, source.execution, "science")
        assert new["export_id"] != old["export_id"]
        assert wait_terminal(store, new["export_id"])["status"] == "failed"
        assert store.get(old["export_id"])["status"] == "unknown"
        assert statuses == ["unknown", "failed"]
    finally:
        store.close()


@pytest.mark.parametrize("kwargs,error", [
    ({"attempt": True}, ValueError), ({"attempt": 0}, ValueError),
    ({"attempt": -1}, ValueError), ({"attempt": 1.0}, ValueError),
    ({"attempt": "2"}, ValueError), ({"calcjob_uuid": "bad"}, ValueError),
    ({"attempt": 2, "calcjob_uuid": CODE}, ValueError),
    ({"attempt": 3}, LookupError), ({"calcjob_uuid": RUN}, LookupError),
])
def test_export_invalid_selectors_precede_preparation(composite_source, kwargs, error):
    source = composite_source
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        with pytest.raises(error):
            store.create(RUN, source.execution, "science", **kwargs)
        assert source.events == [] and store._entries == {}
    finally:
        store.close()


@pytest.mark.parametrize("kwargs", [{}, {"attempt": 1}, {"calcjob_uuid": CODE},
                                     {"attempt": 1, "calcjob_uuid": CODE.upper()}])
def test_export_legacy_one_child_without_published_selection(composite_source, monkeypatch, kwargs):
    source = composite_source
    source.children.pop()
    source.node.outputs.clear()
    del source.children[0].inputs.attempt
    monkeypatch.setattr("qcl_negf_results.export.export_snapshot",
                        lambda root, destination, **kw: publish_test_archive(destination, kw["profile"]))
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        entry = store.create(RUN, source.execution, "science", **kwargs)
        ready = wait_terminal(store, entry["export_id"])
        assert (ready["state"], ready["attempt"], ready["calcjob_uuid"], ready["status"]) == (
            "ready", 1, CODE, "completed")
    finally:
        store.close()


@pytest.mark.parametrize("corruption,error", [("foreign_row", ValueError), ("bad_selection", ValueError),
    ("incomplete", ValueError), ("ambiguous", ValueError), ("empty", LookupError),
    ("missing_operation", RuntimeError)])
def test_export_metadata_failures_never_copy_or_schedule(composite_source, corruption, error):
    source = composite_source
    if corruption == "foreign_row":
        source.facade.list_execution_artifacts = lambda *args, **kwargs: [
            {"execution_id": source.execution, "attempt": 1, "calcjob_uuid": CODE,
             "path": "result/same.bin", "size": 4}]
    elif corruption == "bad_selection":
        source.facade.get_export_selection = lambda *args, **kwargs: {
            "execution_id": source.execution, "attempt": True, "calcjob_uuid": C2, "status": "failed"}
    elif corruption == "missing_operation":
        del source.facade.get_export_selection
    elif corruption == "incomplete":
        source.children[1].outputs.inventory.get_dict()["complete"] = False
    elif corruption == "empty":
        source.children[1].outputs.inventory.get_dict()["files"].clear()
    else:
        source.node.outputs.clear()
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        with pytest.raises(error):
            store.create(RUN, source.execution, "science")
        assert source.events == [] and store._entries == {}
    finally:
        store.close()



def test_changed_status_while_preparing_is_busy_without_mutating_entry(composite_source, monkeypatch):
    source = composite_source
    started, release = Event(), Event()
    result = source.children[1].outputs.pop("result")

    def exporting(root, destination, **kwargs):
        started.set()
        assert release.wait(3)
        return publish_test_archive(destination, kwargs["profile"])

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", exporting)
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        old = store.create(RUN, source.execution, "science")
        assert started.wait(3)
        source.children[1].outputs["result"] = result
        with pytest.raises(ExportBusy):
            store.create(RUN, source.execution, "science")
        assert store.get(old["export_id"])["status"] == "unknown"
        assert len(store._entries) == 1
        release.set()
        assert wait_terminal(store, old["export_id"])["state"] == "ready"
    finally:
        release.set()
        store.close()


def test_expired_exact_export_is_removed_and_prepared_again(composite_source, monkeypatch):
    source = composite_source
    monkeypatch.setattr("qcl_negf_results.export.export_snapshot",
                        lambda root, destination, **kw: publish_test_archive(destination, kw["profile"]))
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        old = store.create(RUN, source.execution, "science")
        assert wait_terminal(store, old["export_id"])["state"] == "ready"
        store._entries[old["export_id"]]["expires_unix"] = time.time() - 1
        with pytest.raises(LookupError):
            store.get(old["export_id"])
        # Expiry marks metadata; the sole worker confirms actual deletion.
        store._executor.submit(lambda: None).result(timeout=2)
        assert not (store._root / old["export_id"]).exists()
        new = store.create(RUN, source.execution, "science")
        assert new["export_id"] != old["export_id"]
        assert wait_terminal(store, new["export_id"])["state"] == "ready"
    finally:
        store.close()


@pytest.mark.parametrize("change", [
    {"path": "../outside"}, {"path": "."}, {"path": "a//b"}, {"path": "bad\x00path"},
    {"size": True}, {"size": -1}, {"attempt": True}, {"calcjob_uuid": CODE},
])
def test_malformed_producer_inventory_row_is_rejected_before_payload(composite_source, change):
    source = composite_source
    row = {"execution_id": source.execution, "attempt": 2, "calcjob_uuid": C2,
           "path": "result/same.bin", "size": 4, **change}
    source.facade.list_execution_artifacts = lambda *args, **kwargs: [row]
    store = ExportStore(source.facade, Settings(TOKEN, (CODE,)))
    try:
        with pytest.raises(ValueError, match="Export selection metadata is inconsistent"):
            store.create(RUN, source.execution, "science")
        assert source.events == [] and store._entries == {}
    finally:
        store.close()
