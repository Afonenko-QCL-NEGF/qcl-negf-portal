"""Native5 retained-point engineering fixtures, never ORM/NFS/science proof."""

import asyncio
import hashlib
import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest
from qcl_negf_api.saved_results import SavedResultError, SavedResults
from qcl_negf_api.service import CloseResult

RUN = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
SEL = dict(run_uuid=RUN, execution_id="ex", attempt=2, calcjob_uuid=JOB)
SHA = "a" * 64
BINDING_UUID = "44444444-4444-4444-8444-444444444444"
COMPUTER = "66666666-6666-4666-8666-666666666666"
BINDING = dict(
    namespace_id="cluster",
    storage_identity="nfs:results",
    root="/run/qcl-negf-saved",
    allowed_computer_uuids=[COMPUTER],
    retention_policy_id="retain",
    retention_owner_id="operator",
    hold_ref="hold:closed",
    access_binding_ref="access:ro",
)


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    from consumer_context_fixture import semantic_fixture
    from qcl_negf_api import saved_nfs, saved_results

    root = tmp_path / "alias"
    data = root / "execution"
    state, observations, _, _, _ = semantic_fixture(data)
    manifest = json.loads((data / state["manifest"]["path"]).read_bytes())
    diagnostics = next(
        {k: v[k] for k in ("path", "bytes", "sha256", "schema", "media_type")}
        for v in manifest["artifacts"]
        if v["role"] == "physics.analysis" and v["path"] != observations["path"]
    )
    source = dict(
        schema="qcl-negf.native-point-source.v1",
        kind="final",
        artifact_root=".",
        relation="current_point_attempt",
        selected_state=state,
        diagnostics=diagnostics,
        observations=observations,
        reason=None,
    )
    selection = {**SEL, "execution_id": "exec"}
    pin = dict(node_uuid=BINDING_UUID, bytes="2", sha256=hashlib.sha256(b"{}").hexdigest())
    local = {**BINDING, "root": str(root)}
    storage = {k: v for k, v in local.items() if k not in ("root", "allowed_computer_uuids")}
    storage.update(
        relative_execution_root="execution",
        computer_uuid=COMPUTER,
        execution_binding_uuid=BINDING_UUID,
    )
    point = dict(
        point_id="point",
        point_attempt=2,
        source=source,
        source_invocation={"source_epoch": "epoch"},
        creator={"calcjob_uuid": JOB, "binding_uuid": None},
        plan_point_id="point",
        coordinates={},
    )
    binding = dict(
        schema="qcl-negf.aiida-native-binding.v1",
        selection=selection,
        storage=storage,
        raw_result={"bytes": "2", "sha256": pin["sha256"]},
        input_plan=pin,
        source_graph=pin,
        points=[point],
        attempt_history=[],
        integrity=dict(
            scope="selected_metadata",
            native_contexts="verified",
            transport_owner="verified",
            full_core_payloads="not_assessed",
            current_access="not_checked",
        ),
    )
    dto = dict(
        status="available",
        point=point,
        binding=binding,
        result_utf8="{}",
        native_result=pin,
        native_binding=pin,
        integrity=dict(
            native_contexts="not_reverified",
            current_access="not_checked",
            scientific_acceptance="not_assessed",
        ),
    )
    query = dict(
        provider="native_nfs",
        point_id="point",
        native_binding_uuid=BINDING_UUID,
        state_manifest_sha256=state["manifest"]["sha256"],
    )
    calls = []

    def getter(run, execution, *, attempt, calcjob_uuid, point_id, binding_uuid=None):
        assert (run, execution, attempt, calcjob_uuid, point_id, binding_uuid) == (
            RUN,
            "exec",
            2,
            JOB,
            "point",
            BINDING_UUID,
        )
        calls.append("retained")
        return deepcopy(dto)

    mount = f"10 1 0:42 / {root} ro - nfs4 synthetic:/results ro\n"
    monkeypatch.setattr(saved_nfs, "ROOT", str(root))
    monkeypatch.setattr(saved_nfs, "_mountinfo", lambda: mount)

    def worker(op, request, spool, cancelled, deadline):
        return saved_results._read_request({"operation": op, **request}, spool)

    adapter = SavedResults(
        SimpleNamespace(get_native_result_source=getter),
        SimpleNamespace(saved_results_enabled=True),
        binding=local,
        worker=worker,
    )
    case = SimpleNamespace(
        root=root,
        data=data,
        state=state,
        observations=observations,
        diagnostics=diagnostics,
        source=source,
        dto=dto,
        selection=selection,
        binding=local,
        query=query,
        adapter=adapter,
        calls=calls,
        mount=mount,
    )
    yield case
    asyncio.run(adapter.close())


def read(case, operation, extra=None):
    async def run():
        return await case.adapter.read(operation, case.selection, {**case.query, **(extra or {})})

    return asyncio.run(run())


def test_manifest_native_view_dictionary_preserves_separate_transport_and_native_identity(
    native_case, monkeypatch
):
    from qcl_negf_results.state import StateReader

    monkeypatch.setattr(
        StateReader,
        "read_array",
        lambda *a, **k: pytest.fail("metadata read opened numerical payload"),
    )
    value = read(native_case, "manifest")
    assert value["source_state"] == native_case.state
    assert (
        value["provider_selection"]["attempt"] == 2
        and value["source_state"]["identity"]["attempt"] == "1"
    )
    assert (
        value["view"]["artifact_ref"] == native_case.observations
        and value["view"]["diagnostics_ref"] == native_case.diagnostics
    )
    assert (
        isinstance(value["view"]["views"], dict)
        and value["view"]["views"]["density"]["availability"] == "available"
    )
    assert (
        value["retained"]["integrity"]["readback"]["current_access"] == "not_checked"
        and value["access"]["status"] == "checked_ro_current_request"
    )
    assert value["retained"]["integrity"]["readback"]["native_contexts"] == "not_reverified"
    assert value["retained"]["integrity"]["original_binding"]["native_contexts"] == "verified"
    assert str(native_case.root) not in json.dumps(value)


def test_slice_actual_native_negative_zero_nonfinite_and_axes(native_case):
    value = read(
        native_case,
        "slice",
        dict(
            source_state=native_case.state,
            view_id="spatial_energy_density",
            selection=[{"start": 0, "stop": 2, "step": 1}, {"start": 0, "stop": 3, "step": 1}],
        ),
    )["view"]
    assert value["values"] == [[-11.0, 12.0, None], [21.0, None, None]]
    assert value["invalid_mask"] == [
        [None, None, "nan"],
        [None, "positive_infinity", "negative_infinity"],
    ]
    assert value["axes"][1]["values"] == [0.0, 2.0, 7.0] and value["units"] == "eV^-1 m^-3"


@pytest.mark.parametrize("operation", ["brief", "sources"])
def test_open_execution_wide_producer_never_uses_legacy_fallback(native_case, operation):
    with pytest.raises(SavedResultError, match="unsupported_contract"):
        read(native_case, operation)
    assert native_case.calls == []


def test_gate_remains_owned_through_cancel_and_uncertain_cleanup(native_case):
    entered = threading.Event()
    release = threading.Event()

    def worker(*args):
        entered.set()
        release.wait(2)
        error = SavedResultError("identity_mismatch")
        error.cleanup_unconfirmed = True
        raise error

    native_case.adapter.worker = worker
    native_case.adapter.cleanup_seconds = 0.02

    async def run():
        task = asyncio.create_task(
            native_case.adapter.read("manifest", native_case.selection, native_case.query)
        )
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert native_case.adapter.closed and native_case.adapter.busy
        with pytest.raises(SavedResultError, match="stopping"):
            await native_case.adapter.read("manifest", native_case.selection, native_case.query)
        release.set()
        for _ in range(100):
            if native_case.adapter.active is None:
                break
            await asyncio.sleep(0.005)
        assert native_case.adapter.lease_cleanup_unconfirmed and native_case.adapter.busy

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_pending_actor_lease_disables_future_admission(native_case):
    native_case.adapter._lease_closed(CloseResult("pending", 1, 1))
    assert native_case.adapter.closed
    with pytest.raises(SavedResultError, match="stopping"):
        read(native_case, "manifest")
