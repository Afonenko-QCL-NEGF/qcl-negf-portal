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
            binding_uuid,
        )
        assert binding_uuid in (None, BINDING_UUID)
        calls.append("metadata" if binding_uuid is None else "retained")
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


def test_exact_point_metadata_no_binding_and_zero_child_or_root_access(native_case, monkeypatch):
    from qcl_negf_api import saved_nfs

    c = native_case
    calls = []

    def getter(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs["binding_uuid"] is None
        return deepcopy(c.dto)

    c.adapter.service.get_native_result_source = getter
    c.adapter.binding = None
    c.adapter.worker = lambda *args: pytest.fail("metadata launched data reader")
    monkeypatch.setattr(saved_nfs, "_mountinfo", lambda: pytest.fail("metadata read mountinfo"))
    monkeypatch.setattr(
        saved_nfs.tempfile,
        "TemporaryDirectory",
        lambda **kw: pytest.fail("metadata allocated spool"),
    )

    async def run():
        return await c.adapter.read("point-source", c.selection, {"point_id": "point"})

    result = asyncio.run(run())
    assert len(calls) == 1
    assert result["schema"] == "qcl-negf.portal-point-source.v1"
    assert result["provider_selection"] == c.selection
    assert result["source_state"] == c.state
    assert result["retained"]["native_binding"] == c.dto["native_binding"]
    assert result["scope"] == "original_owner_metadata"
    assert not set(result) & {"access", "binding", "result_utf8", "artifact_root"}


def metadata_read(case):
    async def run():
        return await case.adapter.read("point-source", case.selection, {"point_id": "point"})

    return asyncio.run(run())


@pytest.mark.parametrize("kind", ["absent", "checkpoint", "final", "retained"])
def test_metadata_source_variants_keep_original_state_creator_and_reason(native_case, kind):
    c = native_case
    if kind == "absent":
        c.source.update(
            kind="absent",
            relation=None,
            selected_state=None,
            observations=None,
            diagnostics=None,
            reason="not_recorded",
        )
        c.dto["point"].update(creator=None, source_invocation=None)
    elif kind == "checkpoint":
        c.source.update(
            kind="checkpoint", observations=None, diagnostics=None, reason="interrupted"
        )
    elif kind == "retained":
        c.source.update(relation="retained_source", reason="failed_new_attempt")
        c.dto["point"]["creator"] = dict(
            calcjob_uuid="55555555-5555-4555-8555-555555555555",
            binding_uuid="77777777-7777-4777-8777-777777777777",
        )
    c.adapter.worker = lambda *args: pytest.fail("metadata opened reader")
    value = metadata_read(c)
    assert c.calls == ["metadata"] and value["status"] == "available"
    assert value["source"] == {k: c.source[k] for k in ("kind", "relation", "reason")}
    assert value["source_state"] == c.source["selected_state"]
    assert value["retained"]["creator"] == c.dto["point"]["creator"]
    assert value["retained"]["integrity"]["readback"]["native_contexts"] == "not_reverified"
    assert value["retained"]["integrity"]["original_binding"]["native_contexts"] == "verified"


@pytest.mark.parametrize(
    "change",
    [
        "selection",
        "point",
        "ambiguous",
        "plan_point",
        "attempt",
        "pin_count",
        "pin_extra",
        "creator",
        "source_state",
        "source_path",
        "nonfinite",
        "oversize",
    ],
)
def test_metadata_rejects_foreign_malformed_and_unbounded_owner_dto(native_case, change):
    c = native_case
    if change == "selection":
        c.dto["binding"]["selection"] = {**c.selection, "run_uuid": JOB}
    elif change == "point":
        c.dto["point"]["point_id"] = "foreign"
    elif change == "ambiguous":
        c.dto["binding"]["points"].append(deepcopy(c.dto["point"]))
    elif change == "plan_point":
        c.dto["point"]["plan_point_id"] = "foreign"
    elif change == "attempt":
        c.dto["point"]["point_attempt"] = 9007199254740992
    elif change == "pin_count":
        c.dto["native_binding"]["bytes"] = "18446744073709551616"
    elif change == "pin_extra":
        c.dto["native_binding"]["private"] = "/private"
    elif change == "creator":
        c.dto["point"]["creator"]["extra"] = "untrusted"
    elif change == "source_state":
        c.state["identity"]["execution_id"] = "foreign"
    elif change == "source_path":
        c.state["manifest"]["path"] = "/private/manifest.json"
    elif change == "nonfinite":
        c.dto["point"]["coordinates"] = {"V": float("nan")}
    else:
        c.dto["result_utf8"] = "x" * (2 * 1024 * 1024)
    c.adapter.worker = lambda *args: pytest.fail("metadata opened reader")
    with pytest.raises(SavedResultError) as caught:
        metadata_read(c)
    if change == "oversize":
        assert caught.value.code == "budget_exceeded"


@pytest.mark.parametrize("stop", ["cancel", "disconnect", "deadline"])
def test_metadata_cancel_keeps_actual_pending_getter_and_quarantines(
    native_case, monkeypatch, stop
):
    entered, release = threading.Event(), threading.Event()

    def getter(*args, **kwargs):
        assert kwargs["binding_uuid"] is None
        entered.set()
        release.wait(2)
        return deepcopy(native_case.dto)

    native_case.adapter.service.get_native_result_source = getter
    native_case.adapter.cleanup_seconds = 0.02
    if stop == "deadline":
        monkeypatch.setattr("qcl_negf_api.saved_results.WALL", 0.05)

    async def disconnected():
        return stop == "disconnect" and entered.is_set()

    async def run():
        task = asyncio.create_task(
            native_case.adapter.read(
                "point-source", native_case.selection, {"point_id": "point"}, disconnected
            )
        )
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        with pytest.raises(SavedResultError, match="busy"):
            await native_case.adapter.read(
                "point-source", native_case.selection, {"point_id": "point"}
            )
        if stop == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(
                SavedResultError,
                match="unavailable_storage" if stop == "disconnect" else "budget_exceeded",
            ):
                await task
        assert native_case.adapter.closed and native_case.adapter.busy
        with pytest.raises(SavedResultError, match="stopping"):
            await native_case.adapter.read(
                "point-source", native_case.selection, {"point_id": "point"}
            )
        release.set()
        for _ in range(100):
            if native_case.adapter.active is None:
                break
            await asyncio.sleep(0.005)
        assert native_case.adapter.active is None and native_case.adapter.closed

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_metadata_refresh_uses_actual_actor_owner_thread_with_none_expectation(
    native_case, monkeypatch
):
    from pathlib import Path

    import aiida
    import aiida.manage
    import aiida_qcl_negf.service as owner
    from qcl_negf_api.service import AiidaService

    assert (
        Path(owner.__file__).resolve()
        == Path(__file__).resolve().parents[2] / "p07-b-credit-aiida/src/aiida_qcl_negf/service.py"
    )
    events = []
    monkeypatch.setattr(
        aiida, "load_profile", lambda profile: events.append(("load", threading.get_ident()))
    )
    monkeypatch.setattr(
        aiida.manage,
        "get_manager",
        lambda: SimpleNamespace(
            unload_profile=lambda: events.append(("unload", threading.get_ident()))
        ),
    )

    def getter(run, execution, **kwargs):
        assert (run, execution) == (RUN, "exec")
        assert kwargs == dict(attempt=2, calcjob_uuid=JOB, point_id="point", binding_uuid=None)
        events.append(("getter", threading.get_ident()))
        return deepcopy(native_case.dto)

    monkeypatch.setattr(owner, "get_native_result_source", getter)
    actor = AiidaService("synthetic-P10")
    native_case.adapter.service = actor
    native_case.adapter.binding = None
    try:
        assert metadata_read(native_case)["source_state"] == native_case.state
    finally:
        actor.close()
    assert [e[0] for e in events] == ["load", "getter", "unload"]
    assert len({e[1] for e in events}) == 1 and events[0][1] != threading.get_ident()


@pytest.mark.parametrize("kind", [LookupError, ValueError])
def test_metadata_owner_failure_never_becomes_available_absent(native_case, kind):
    def getter(*args, **kwargs):
        raise kind("private owner detail")

    native_case.adapter.service.get_native_result_source = getter
    with pytest.raises(SavedResultError, match="unavailable_storage") as caught:
        metadata_read(native_case)
    assert "private" not in str(caught.value)
