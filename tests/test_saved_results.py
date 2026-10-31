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


@pytest.fixture
def catalog_case(native_case, monkeypatch):
    """NEW independent tiny owners; original metadata is a manufactured owner reply."""
    import result_v2_fixture
    from consumer_context_fixture import fixture

    def make(finals=2, gaps=True, declared_count=None, huge=False):
        c = native_case
        declared, points, rows = [], [], []
        count = finals + (3 if gaps else 0) if declared_count is None else declared_count
        for i in range(count):
            name = f"p{i}"
            coords = dict(
                temperature_K=200.0,
                voltage_per_period_V=-0.0 if i == 0 else 0.05,
                branch="reverse" if i % 2 else "forward",
                order=i + 1,
            )
            if huge:
                coords.update(
                    temperature_K=10**2000, voltage_per_period_V=10**2000, order=10**2000 + i
                )
            declared.append(dict(id=name, execution_id="exec", **coords))
            if i >= finals and (not gaps or i == finals + 2):
                continue
            if i < finals:
                monkeypatch.setitem(result_v2_fixture.IDENTITY, "point_id", name)
                root = c.data / name
                state, observations, _, _, _ = fixture(root, nz=2)
                source = dict(
                    c.source, selected_state=state, observations=observations, artifact_root=name
                )
                manifest = json.loads((root / state["manifest"]["path"]).read_bytes())
                source["diagnostics"] = next(
                    {k: v[k] for k in ("path", "bytes", "sha256", "schema", "media_type")}
                    for v in manifest["artifacts"]
                    if v["role"] == "physics.analysis" and v["path"] != observations["path"]
                )
            elif i == finals:
                source = dict(
                    c.source,
                    kind="absent",
                    selected_state=None,
                    diagnostics=None,
                    observations=None,
                    relation=None,
                    reason="not_recorded",
                )
            else:
                state = deepcopy(c.state)
                state["identity"]["point_id"] = name
                source = dict(
                    c.source,
                    kind="checkpoint",
                    selected_state=state,
                    diagnostics=None,
                    observations=None,
                    reason="interrupted",
                )
            point = dict(
                point_id=name,
                point_attempt=9,
                plan_point_id=name,
                coordinates=coords,
                source=source,
                source_invocation=None if source["kind"] == "absent" else {"source_epoch": "epoch"},
                creator=None
                if source["kind"] == "absent"
                else {"calcjob_uuid": c.selection["calcjob_uuid"], "binding_uuid": None},
            )
            points.append(point)
            rows.append(
                dict(
                    id=name,
                    attempt=9,
                    coordinates=coords,
                    data={"native_source": source},
                    status="failed",
                    quality="invalid",
                    converged=False,
                )
            )
        raw = json.dumps({"points": rows}, ensure_ascii=False, allow_nan=False).encode()
        pin = dict(
            node_uuid=BINDING_UUID, bytes=str(len(raw)), sha256=hashlib.sha256(raw).hexdigest()
        )
        binding = {
            **c.dto["binding"],
            "points": points,
            "attempt_history": [],
            "input_plan": deepcopy(pin),
            "source_graph": deepcopy(pin),
            "raw_result": {k: pin[k] for k in ("bytes", "sha256")},
        }
        c.catalog = dict(
            status="available",
            point=None,
            binding=binding,
            result_utf8=raw.decode(),
            native_result=pin,
            native_binding=deepcopy(pin),
            integrity=c.dto["integrity"],
            declared_points=declared,
        )
        c.catalog_calls = []

        def getter(*args, **kwargs):
            assert kwargs["point_id"] is None
            assert kwargs["binding_uuid"] in (None, BINDING_UUID)
            c.catalog_calls.append(kwargs)
            return deepcopy(c.catalog)

        c.adapter.service.get_native_result_source = getter
        return c

    return make


def catalog_read(case, operation="sources", query=None):
    return asyncio.run(case.adapter.read(operation, case.selection, query or {}))


def test_R1_catalog_one_owner_preserves_declared_gaps_and_finite_page(catalog_case):
    c = catalog_case()
    c.adapter.worker = lambda *a: pytest.fail("metadata opened child")
    value = catalog_read(c, query={"limit": 3})
    assert len(c.catalog_calls) == 1 and value["schema"] == "qcl-negf.portal-native-sources.v1"
    assert [p["point_id"] for p in value["points"]] == ["p0", "p1", "p2"]
    assert (
        value["points"][0]["declared_coordinates"]["voltage_per_period_V"]["bits"]
        == "8000000000000000"
    )
    tail = catalog_read(c, query={"cursor": value["pagination"]["next_cursor"]})
    assert [p["point_id"] for p in tail["points"]] == ["p3", "p4"]
    assert tail["points"][-1]["row_status"] == "not_recorded"
    assert (
        tail["points"][-1]["source"] is None
        and tail["points"][-1]["missing_reason"] == "result_row_absent"
    )


def test_R1_page_actual_public_briefs_have_assigned_epochs_and_gap_states(catalog_case):
    c = catalog_case()
    page = catalog_read(c)
    value = catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})
    assert len(c.catalog_calls) == 2
    assert [p["read"]["status"] for p in value["points"]] == [
        "available",
        "available",
        "absent",
        "checkpoint_only",
        "not_recorded",
    ]
    assert (
        value["credits"]["stage_share"] == 33554432
        and value["credits"]["reserved_bytes"] == 134217728
    )
    for p in value["points"][:2]:
        assert p["view"]["source_state"] == p["source_state"]
        assert p["view"]["limits"]["maximum_io_bytes"] == 33554432
        assert (
            p["view"]["scalar_observables"]["current_density_A_per_m2"]["bits"]
            == "8000000000000000"
        )
    assert all(p["view"] is None and p["access"] is None for p in value["points"][2:])


def test_R1_huge_uncomputed_declarations_are_exact_before_json(catalog_case):
    c = catalog_case(finals=0, gaps=False, declared_count=260, huge=True)
    c.adapter.binding = None
    c.adapter.worker = lambda *a: pytest.fail("uncomputed page opened child")
    page = catalog_read(c)
    coordinate = page["points"][0]["declared_coordinates"]
    assert coordinate["temperature_K"] == {"kind": "json_integer", "decimal": str(10**2000)}
    assert coordinate["order"] == str(10**2000)
    brief = catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})
    assert brief["credits"]["final_sources"] == 0 and all(
        p["view"] is None for p in brief["points"]
    )


@pytest.mark.parametrize("n", [0, 1, 2, 8])
def test_R1_page_public_reader_counts_close_and_numeric_payload(
    catalog_case, monkeypatch, n, record_property
):
    import os

    import h5py
    from qcl_negf_results.state import StateReader

    c = catalog_case(finals=n, gaps=False)
    active = set()
    maximum = 0
    original_init = StateReader.__init__
    original_close = StateReader.close

    def init(self, *args, **kwargs):
        nonlocal maximum
        original_init(self, *args, **kwargs)
        active.add(id(self))
        maximum = max(maximum, len(active))

    def close(self):
        try:
            return original_close(self)
        finally:
            active.discard(id(self))

    monkeypatch.setattr(StateReader, "__init__", init)
    monkeypatch.setattr(StateReader, "close", close)
    original_get = h5py.Dataset.__getitem__

    def get(dataset, key):
        assert dataset.dtype.kind not in "biufc", dataset.name
        return original_get(dataset, key)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", get)
    page = catalog_read(c)
    before = len(os.listdir("/proc/self/fd"))
    value = catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})
    assert len(c.catalog_calls) == 2 and not active and maximum == (1 if n else 0)
    assert before == len(os.listdir("/proc/self/fd"))
    credits = value["credits"]
    assert credits["final_sources"] == n
    assert credits["reserved_bytes"] <= 128 * 1024 * 1024
    assert credits["actual_delivered_bytes"] == sum(
        e["initialization"]["bytes_read_total"] + e["brief_and_close"]["bytes_read_total"]
        for e in credits["epochs"]
    )
    assert all(
        e["initialization"]["maximum_io_bytes"]
        == e["brief_and_close"]["maximum_io_bytes"]
        == credits["stage_share"]
        for e in credits["epochs"]
    )
    record_property("r1_page_epochs", json.dumps(credits))


def _catalog_repin(case):
    """Independent raw selected-row mirror for malformed-source controls."""
    rows = []
    for p in case.catalog["binding"]["points"]:
        rows.append(
            dict(
                id=p["point_id"],
                attempt=p["point_attempt"],
                coordinates=p["coordinates"],
                data={"native_source": p["source"]},
                status="failed",
                quality="invalid",
                converged=False,
            )
        )
    raw = json.dumps({"points": rows}, allow_nan=False).encode()
    case.catalog["result_utf8"] = raw.decode()
    pin = {"bytes": str(len(raw)), "sha256": hashlib.sha256(raw).hexdigest()}
    case.catalog["binding"]["raw_result"] = pin
    case.catalog["native_result"].update(pin)


@pytest.mark.parametrize(
    "phase", ["initialization", "brief", "close", "second_source", "domain_after_close"]
)
def test_R1_admitted_page_failure_has_no_partial_answer(catalog_case, monkeypatch, phase):
    from dataclasses import replace

    from qcl_negf_contracts.messages import ContractError
    from qcl_negf_results.saved_views import AnalysisReader
    from qcl_negf_results.state import StateReader

    from qcl_negf_api import saved_nfs

    c = catalog_case(finals=2, gaps=False)
    if phase == "second_source":
        c.catalog["binding"]["points"][1]["source"]["observations"]["sha256"] = "0" * 64
        _catalog_repin(c)
    page = catalog_read(c)
    if phase == "initialization":
        monkeypatch.setattr(saved_nfs, "PAGE_IO", 4)
    if phase == "brief":
        original = AnalysisReader.brief

        def low(self):
            self.limits = replace(self.limits, maximum_io_bytes=1)
            return original(self)

        monkeypatch.setattr(AnalysisReader, "brief", low)
    if phase == "close":
        original = StateReader.close

        def failed_close(self):
            original(self)
            raise ContractError("synthetic close credit failure", "budget_exceeded")

        monkeypatch.setattr(StateReader, "close", failed_close)
    if phase == "domain_after_close":
        original = StateReader.close

        def changed(self):
            original(self)
            old = c.data / "p0"
            if old.exists():
                old.rename(c.data / "old-p0")
                old.mkdir()

        monkeypatch.setattr(StateReader, "close", changed)
    with pytest.raises((SavedResultError, ContractError)):
        catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})


@pytest.mark.parametrize("field", ["native_result", "native_binding", "input_plan", "source_graph"])
def test_R1_changed_original_page_pin_refuses_before_spool(catalog_case, field):
    c = catalog_case()
    page = catalog_read(c)
    pin = c.catalog[field] if field in c.catalog else c.catalog["binding"][field]
    pin["node_uuid"] = "77777777-7777-4777-8777-777777777777"
    c.adapter.worker = lambda *a: pytest.fail("changed pin opened child")
    with pytest.raises(SavedResultError, match="source_changed"):
        catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})


def test_R1_all_original_creators_and_coordinates_checked_before_page_child(catalog_case):
    c = catalog_case()
    page = catalog_read(c, query={"limit": 1})
    c.catalog["binding"]["points"][-1]["creator"]["calcjob_uuid"] = "foreign"
    c.adapter.worker = lambda *a: pytest.fail("foreign creator outside page opened child")
    with pytest.raises(SavedResultError):
        catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": True},
        {"limit": 9},
        {"cursor": "x"},
        {"limit": 2, "cursor": "x"},
        {"point_id": "p0"},
        {"cursor": "x" * 8193},
    ],
)
def test_R1_invalid_page_query_has_zero_owner_calls(catalog_case, query):
    c = catalog_case(finals=0, gaps=False)
    with pytest.raises(SavedResultError, match="selection_required"):
        catalog_read(c, query=query)
    assert c.catalog_calls == []


def test_R1_native_row_u64_counter_is_exact_and_separate(catalog_case):
    c = catalog_case(finals=0, gaps=True)
    c.catalog["binding"]["points"][0]["point_attempt"] = 2**64 - 1
    _catalog_repin(c)
    page = catalog_read(c)
    assert page["points"][0]["point_attempt"] == "18446744073709551615"
    assert page["provider_selection"]["attempt"] == 2


def test_R1_declared_integer_and_recorded_float_keep_own_binary64_join(catalog_case):
    c = catalog_case(finals=0, gaps=True)
    c.catalog["declared_points"][0]["temperature_K"] = 200
    page = catalog_read(c)
    assert page["points"][0]["declared_coordinates"]["temperature_K"] == {
        "kind": "json_integer",
        "decimal": "200",
    }
    c.catalog["declared_points"][0]["voltage_per_period_V"] = 0
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        catalog_read(c)


def test_R1_actual_actor_forwards_None_once_for_each_page_request(catalog_case, monkeypatch):
    import aiida
    import aiida.manage
    import aiida_qcl_negf.service as owner

    from qcl_negf_api.service import AiidaService

    c = catalog_case(finals=0, gaps=False)
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
        assert kwargs["point_id"] is None
        assert kwargs["binding_uuid"] in (None, BINDING_UUID)
        events.append(("getter", threading.get_ident()))
        return deepcopy(c.catalog)

    monkeypatch.setattr(owner, "get_native_result_source", getter)
    actor = AiidaService("synthetic-R1")
    c.adapter.service = actor
    try:
        page = catalog_read(c)
        catalog_read(c, "brief", {"page_ref": page["pagination"]["page_ref"]})
    finally:
        actor.close()
    assert [e[0] for e in events] == ["load", "getter", "getter", "unload"]
    assert len({e[1] for e in events}) == 1 and events[0][1] != threading.get_ident()


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
def test_execution_page_rejects_single_point_query_without_legacy_fallback(native_case, operation):
    with pytest.raises(SavedResultError, match="selection_required"):
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


@pytest.mark.parametrize("operation", ["point-source", "sources"])
@pytest.mark.parametrize("stop", ["cancel", "disconnect", "deadline"])
def test_metadata_cancel_keeps_actual_pending_getter_and_quarantines(
    native_case, catalog_case, monkeypatch, stop, operation
):
    if operation == "sources":
        native_case = catalog_case(finals=0, gaps=False)
    query = {"point_id": "point"} if operation == "point-source" else {}
    entered, release = threading.Event(), threading.Event()

    def getter(*args, **kwargs):
        assert kwargs["binding_uuid"] is None
        entered.set()
        release.wait(2)
        return deepcopy(native_case.dto if operation == "point-source" else native_case.catalog)

    native_case.adapter.service.get_native_result_source = getter
    native_case.adapter.cleanup_seconds = 0.02
    if stop == "deadline":
        monkeypatch.setattr("qcl_negf_api.saved_results.WALL", 0.05)

    async def disconnected():
        return stop == "disconnect" and entered.is_set()

    async def run():
        task = asyncio.create_task(
            native_case.adapter.read(operation, native_case.selection, query, disconnected)
        )
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        with pytest.raises(SavedResultError, match="busy"):
            await native_case.adapter.read(operation, native_case.selection, query)
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
            await native_case.adapter.read(operation, native_case.selection, query)
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

    import os

    source_root = Path(os.environ.get(
        "QCL_NEGF_TEST_AIIDA_SOURCE_ROOT",
        Path(__file__).resolve().parents[2] / "qcl-negf-aiida" / "src",
    )).resolve()
    owner_file = Path(owner.__file__).resolve()
    assert owner_file == source_root / "aiida_qcl_negf" / "service.py"
    owner_source_sha256 = hashlib.sha256(owner_file.read_bytes()).hexdigest()
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
    assert hashlib.sha256(owner_file.read_bytes()).hexdigest() == owner_source_sha256


@pytest.mark.parametrize("kind", [LookupError, ValueError])
def test_metadata_owner_failure_never_becomes_available_absent(native_case, kind):
    def getter(*args, **kwargs):
        raise kind("private owner detail")

    native_case.adapter.service.get_native_result_source = getter
    with pytest.raises(SavedResultError, match="unavailable_storage") as caught:
        metadata_read(native_case)
    assert "private" not in str(caught.value)


@pytest.fixture
def history_case(catalog_case, monkeypatch):
    """NEW immutable history sources; signed values are an independent literal oracle."""
    import result_v2_fixture as f

    c = catalog_case(finals=0, gaps=False, declared_count=2)
    points = []
    for i, d in enumerate(c.catalog["declared_points"]):
        monkeypatch.setitem(f.IDENTITY, "point_id", d["id"])
        rows = [
            f.record(1, 7, "4008000000000000"),
            f.record(2, 9, "c010000000000000"),
            f.record(3, 11, "7ff8000000001234"),
            f.record(4, 13, "8000000000000000"),
            f.record(5, 15, "7ff0000000000000"),
        ]
        update = deepcopy(rows[0])
        update["stream_ordinal"] = "6"
        update["core"]["fields"]["sequence"]["decimal"] = "17"
        update["core"]["fields"]["action"] = f.symbol("witness_update")
        update["target"] = dict(
            scope="same_leaf",
            source_identity=update["identity"],
            source_epoch="epoch",
            append_ordinal="1",
            leaf=None,
            row_offset="0",
            anchor_state=None,
            anchor_history=None,
        )
        rows.append(update)
        state = f.source(c.data / d["id"], rows)
        source = dict(
            c.source,
            kind="checkpoint",
            artifact_root=d["id"],
            selected_state=state,
            observations=None,
            diagnostics=None,
            reason="interrupted",
        )
        points.append(
            dict(
                point_id=d["id"],
                point_attempt=2**64 - 1,
                plan_point_id=d["id"],
                coordinates={
                    k: d[k] for k in ("temperature_K", "voltage_per_period_V", "branch", "order")
                },
                source=source,
                source_invocation={"source_epoch": "epoch"},
                creator={"calcjob_uuid": JOB, "binding_uuid": None},
            )
        )
    c.catalog["binding"]["points"] = points
    _catalog_repin(c)
    return c


def history_selection(c, i=0, **reduction):
    p = c.catalog["binding"]["points"][i]
    b = c.catalog["binding"]
    return dict(
        **c.selection,
        provider="native_nfs",
        point_id=p["point_id"],
        retained={
            k: deepcopy(c.catalog[k] if k in c.catalog else b[k])
            for k in ("native_result", "native_binding", "input_plan", "source_graph")
        },
        source_state=deepcopy(p["source"]["selected_state"]),
        source_invocation=deepcopy(p["source_invocation"]),
        reduction=dict(stage="scba", field="J", first_record="2", last_record="6", **reduction),
    )


def history_read(c, operation="derive-history", payload=None):
    selected = history_selection(c)
    query = {k: v for k, v in selected.items() if k not in c.selection}
    return asyncio.run(
        c.adapter.read(operation, c.selection, query if payload is None else payload)
    )


def test_P10_public_derive_preserves_signed_ieee_and_all_three_epochs(history_case):
    c = history_case
    value = history_read(c)
    result = value["view"]["result"]
    assert result["minimum"]["value"] == {"kind": "ieee", "type": "f64", "bits": "c010000000000000"}
    assert result["maximum"]["value"]["bits"] == "8000000000000000"
    assert [
        result[k]
        for k in ("observations", "updates", "finite_count", "nonfinite_count", "missing_count")
    ] == ["4", "1", "2", "2", "0"]
    assert result["units"] == "A/m^2"
    assert value["view"]["history_cutoff"]["last_record"] == "6"
    credits = value["view"]["credits"]
    assert credits["stage_share"] == 44739242 and credits["reserved_bytes"] == 134217726
    assert [e["phase"] for e in credits["epochs"]] == ["construction", "import", "derive_and_close"]
    assert credits["actual_delivered_bytes"] == sum(
        e["counters"]["bytes_read_total"] for e in credits["epochs"]
    )
    assert credits["metadata_readback_actual"] is None
    assert len(c.catalog_calls) == 1 and c.catalog_calls[0]["point_id"] is None
    assert value["view"]["scientific_acceptance"] == "not_assessed"


@pytest.mark.parametrize("assessment", ["unknown", "different", "comparable"])
def test_P10_actual_compare_one_owner_no_scientific_verdict(history_case, assessment):
    c = history_case
    left, right = history_selection(c), history_selection(c, 1)
    comparison = dict.fromkeys(("model", "discretization", "protocol", "source"), assessment)
    comparison.update(evidence=["NEW fixture only"], reason="Explicit author assessment")
    value = history_read(
        c, "compare-history", dict(left=left, right=right, comparability=comparison)
    )
    assert value["schema"] == "qcl-negf.portal-native-history-comparison.v1"
    assert value["view"]["scientific_acceptance"] == "not_assessed"
    assert value["assessment_origin"] == "author_declared"
    assert value["left"]["point_attempt"] == "18446744073709551615"
    assert value["left"]["source_state"] != value["right"]["source_state"]
    assert value["view"]["differences"] == (
        None
        if assessment != "comparable"
        else {
            "minimum": {"kind": "ieee", "type": "f64", "bits": "0000000000000000"},
            "maximum": {"kind": "ieee", "type": "f64", "bits": "0000000000000000"},
        }
    )
    assert value["credits"]["stage_share"] == 22369621
    assert len(value["credits"]["epochs"]) == 6 and len(c.catalog_calls) == 1


@pytest.mark.parametrize(
    "change", ["model", "state", "manifest_media", "epoch", "pin", "absent", "missing"]
)
def test_P10_foreign_source_refuses_before_child(history_case, change):
    c = history_case
    v = history_selection(c)
    if change == "model":
        v["source_state"]["identity"]["model_sha256"] = "0" * 64
    elif change == "state":
        v["source_state"]["state_sequence"] = "2"
    elif change == "manifest_media":
        v["source_state"]["manifest"]["media_type"] = "application/foreign"
    elif change == "epoch":
        v["source_invocation"]["source_epoch"] = "foreign"
    elif change == "pin":
        v["retained"]["input_plan"]["sha256"] = "0" * 64
    elif change == "missing":
        v["point_id"] = "missing"
        v["source_state"]["identity"]["point_id"] = "missing"
    else:
        p = c.catalog["binding"]["points"][0]
        p["source"].update(
            kind="absent",
            selected_state=None,
            observations=None,
            diagnostics=None,
            relation=None,
            reason="not_recorded",
        )
        p["creator"] = p["source_invocation"] = None
        _catalog_repin(c)
        v["retained"] = history_selection(c, 1)["retained"]
    c.adapter.worker = lambda *a: pytest.fail("foreign source opened child")
    with pytest.raises(SavedResultError):
        history_read(c, payload={k: x for k, x in v.items() if k not in c.selection})


def test_P10_late_narrow_interval_reads_whole_prefix_and_batch_overshoot(history_case, monkeypatch):
    from qcl_negf_results.state import StateReader

    c = history_case
    seen = []
    original = StateReader.history_batch

    def observed(self, *args, **kwargs):
        batch = original(self, *args, **kwargs)
        seen.append([r["stream_ordinal"] for r in batch.records])
        return batch

    monkeypatch.setattr(StateReader, "history_batch", observed)
    v = history_selection(c)
    v["reduction"].update(first_record="4", last_record="4")
    result = history_read(c, payload={k: x for k, x in v.items() if k not in c.selection})
    assert seen == [["1", "2", "3", "4", "5", "6"]]
    assert result["view"]["result"]["observations"] == "1"
    assert result["view"]["result"]["minimum"]["value"]["bits"] == "8000000000000000"
    v["reduction"]["last_record"] = "7"
    with pytest.raises(SavedResultError):
        history_read(c, payload={k: x for k, x in v.items() if k not in c.selection})


@pytest.mark.parametrize(
    "phase",
    ["copy", "copy_manifest", "io", "second_source", "reader_close", "domain_close", "response"],
)
def test_P10_pair_failure_never_publishes_partial_result(history_case, monkeypatch, phase):
    from qcl_negf_results import live_views
    from qcl_negf_results.state import StateReader

    from qcl_negf_api import saved_nfs

    c = history_case
    left, right = history_selection(c), history_selection(c, 1)
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="Test only")
    pair = dict(left=left, right=right, comparability=assessment)
    if phase == "copy":
        monkeypatch.setattr(saved_nfs, "HISTORY_COPY", 1)
        c.adapter.worker = lambda *a: pytest.fail("copy refused after child")
    elif phase == "copy_manifest":
        for point in c.catalog["binding"]["points"]:
            point["source"]["selected_state"]["manifest"]["bytes"] = "10485760"
        _catalog_repin(c)
        pair.update(left=history_selection(c), right=history_selection(c, 1))
        c.adapter.worker = lambda *a: pytest.fail("manifest copy forecast refused after child")
    elif phase == "io":
        monkeypatch.setattr(saved_nfs, "PAGE_IO", 18)
    elif phase == "second_source":
        (c.data / "p1" / "manifest.json").write_bytes(b"{}")
    elif phase == "reader_close":
        original = StateReader.close

        def close(self):
            original(self)
            if self.root.name == "p1":
                raise OSError("NEW close failure")

        monkeypatch.setattr(StateReader, "close", close)
    elif phase == "domain_close":
        original = saved_nfs._NativeDomain.close

        def close(self):
            original(self)
            if self.root.name == "p1":
                raise OSError("NEW domain close failure")

        monkeypatch.setattr(saved_nfs._NativeDomain, "close", close)
    else:
        original = live_views.compare_history

        def oversized(*a, **k):
            result = original(*a, **k)
            result["left"]["units"] = "x" * (2 * 1024 * 1024)
            return result

        monkeypatch.setattr(live_views, "compare_history", oversized)
    with pytest.raises(SavedResultError):
        history_read(c, "compare-history", pair)


def test_P10_two_distinct_owners_are_read_exactly_once(history_case):
    c = history_case
    left, right = history_selection(c), history_selection(c, 1)
    right["run_uuid"] = "77777777-7777-4777-8777-777777777777"
    calls = []

    def getter(run, execution, **kwargs):
        calls.append((run, kwargs["point_id"]))
        dto = deepcopy(c.catalog)
        dto["binding"]["selection"]["run_uuid"] = run
        return dto

    c.adapter.service.get_native_result_source = getter
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="Test only")
    result = history_read(
        c, "compare-history", dict(left=left, right=right, comparability=assessment)
    )
    assert calls == [(left["run_uuid"], None), (right["run_uuid"], None)]
    assert result["credits"]["metadata_readback_reserved"]["metadata_bytes"] == 671088640


def test_P10_all_nonfinite_has_null_extrema(history_case, monkeypatch):
    import result_v2_fixture as f

    c = history_case
    monkeypatch.setitem(f.IDENTITY, "point_id", "p0")
    root = c.data / "fresh"
    state = f.source(root, [f.record(1, 7, "7ff8000000000042"), f.record(2, 9, "fff0000000000000")])
    p = c.catalog["binding"]["points"][0]
    p["source"].update(selected_state=state, artifact_root="fresh")
    _catalog_repin(c)
    v = history_selection(c)
    v["reduction"].update(first_record="1", last_record="2")
    result = history_read(c, payload={k: x for k, x in v.items() if k not in c.selection})["view"][
        "result"
    ]
    assert result["minimum"] is None and result["maximum"] is None
    assert result["finite_count"] == "0" and result["nonfinite_count"] == "2"


@pytest.mark.parametrize("mismatch", ["units", "field"])
def test_P10_actual_public_comparison_mismatch_keeps_null_differences(
    history_case, monkeypatch, mismatch
):
    import result_v2_fixture as f

    c = history_case
    monkeypatch.setitem(f.IDENTITY, "point_id", "p1")
    row = f.record(1, 7, "4000000000000000")
    if mismatch == "units":
        row["core"]["fields"]["units"][0]["fields"]["second"] = "other_stored_unit"
    else:
        unit = deepcopy(row["core"]["fields"]["units"][0])
        unit["fields"]["first"] = f.symbol("r_D")
        unit["fields"]["second"] = "1"
        row["core"]["fields"]["units"].append(unit)
    state = f.source(c.data / "mismatch", [row])
    c.catalog["binding"]["points"][1]["source"].update(
        selected_state=state, artifact_root="mismatch"
    )
    _catalog_repin(c)
    left, right = history_selection(c), history_selection(c, 1)
    left["reduction"].update(first_record="1", last_record="1")
    right["reduction"].update(
        first_record="1", last_record="1", field="J" if mismatch == "units" else "r_D"
    )
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "comparable")
    assessment.update(evidence=["NEW fixture"], reason="Author declaration only")
    result = history_read(
        c, "compare-history", dict(left=left, right=right, comparability=assessment)
    )
    assert result["view"]["differences"] is None


def test_P10_cancelled_pair_holds_actual_gate_and_quarantines_unknown_cleanup(history_case):

    c = history_case
    entered, release = threading.Event(), threading.Event()

    def worker(*args):
        entered.set()
        release.wait(2)
        error = SavedResultError("unavailable_storage")
        error.cleanup_unconfirmed = True
        raise error

    c.adapter.worker = worker
    c.adapter.cleanup_seconds = 0.02
    left, right = history_selection(c), history_selection(c, 1)
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="NEW cancellation control")
    pair = dict(left=left, right=right, comparability=assessment)

    async def scenario():
        task = asyncio.create_task(c.adapter.read("compare-history", c.selection, pair))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert c.adapter.busy and c.adapter.active is not None and c.adapter.closed
        with pytest.raises(SavedResultError, match="stopping"):
            await c.adapter.read("compare-history", c.selection, pair)
        release.set()
        for _ in range(100):
            if c.adapter.active is None:
                break
            await asyncio.sleep(0.005)
        assert c.adapter.active is None and c.adapter.lease_cleanup_unconfirmed and c.adapter.busy
        assert c.adapter.pending_spools
        # This spool belongs to this NEW test and actual worker is now finished.
        for spool in c.adapter.pending_spools:
            spool.cleanup()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
