"""Literal source-bound transport controls; no production NFS or solver."""

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_saved_results import JOB, SEL, SHA, real_brief_fixture

from qcl_negf_api.saved_results import SavedResultError, SavedResults

A = "44444444-4444-4444-8444-444444444444"
B = "55555555-5555-4555-8555-555555555555"
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


def source_fixture():
    from qcl_negf_api.saved_nfs import canonical

    service, files, opened, series = real_brief_fixture()
    plan = service.get_export_plan()["plan"].decode()
    commit_path = "archive/p0/commit.json"
    commit_raw = files["result/" + commit_path]
    commit = json.loads(commit_raw)
    metadata = {
        "series_result.json": json.dumps(series),
        "scientific_plan.json": plan,
        commit_path: commit_raw.decode(),
        "_aiida/selection.json": '{"retained":true}',
    }
    descriptors = [
        {
            "path": p,
            "bytes": len(v.encode()),
            "sha256": hashlib.sha256(v.encode()).hexdigest(),
            "retained_node_uuid": A,
        }
        for p, v in metadata.items()
    ]
    point = dict(
        point_id="p0",
        commit_path=commit_path,
        commit_sha256=hashlib.sha256(commit_raw).hexdigest(),
        native_identity=commit["identity"],
        source_attempt=1,
        row_attempt=1,
        source_calcjob_uuid=None,
        receipt_sha256="c" * 64,
        storage_class="archive",
        state_sequence=1,
        declared_analysis=dict(
            path="archive/p0/analysis.h5",
            bytes=29,
            sha256="d" * 64,
            schema="qcl-negf-physics-analysis-v4",
        ),
    )
    # Actual owner declaration is authoritative; no fabricated HDF in metadata path.
    owner = commit["artifacts"][0]
    point["declared_analysis"].update(bytes=owner["bytes"], sha256=owner["sha256"])
    record = dict(
        schema="qcl-negf.aiida-saved-source.v1",
        selection=SEL,
        plan=dict(
            sha256=hashlib.sha256(plan.encode()).hexdigest(),
            plan_fingerprint=SHA,
            input_node_uuid=A,
            scientific_fingerprint=None,
        ),
        storage={
            **{k: v for k, v in BINDING.items() if k not in ("root", "allowed_computer_uuids")},
            "computer_uuid": COMPUTER,
            "remote_folder_uuid": JOB,
            "relative_result_root": "job/result",
        },
        snapshot=dict(
            result_sha256=hashlib.sha256(metadata["series_result.json"].encode()).hexdigest(),
            result_bytes=len(metadata["series_result.json"].encode()),
            metadata_set_sha256=hashlib.sha256(canonical(descriptors)).hexdigest(),
            complete=False,
            publication_state="closed_registered",
        ),
        points=[point],
        metadata=descriptors,
        integrity=dict(metadata="verified", payload="not_measured", payload_receipt_ref=None),
        copy_status={"actual": "retained_metadata"},
        request_uuid=A,
        request_sha256="e" * 64,
        registration_source_id="admin",
    )
    dto = dict(
        status="available",
        source_uuid=A,
        source_sha256=hashlib.sha256(canonical(record)).hexdigest(),
        selection=SEL,
        record=record,
        metadata=metadata,
        plan=plan,
        metadata_status="verified",
        copy_status=record["copy_status"],
        access_status="not_checked",
    )
    calls = []

    def get_source(*args, **kwargs):
        calls.append(kwargs)
        return deepcopy(dto)

    service.get_saved_source = get_source
    service.get_saved_sources = lambda *a, **k: dict(
        selection=SEL,
        complete=True,
        sources=[
            {
                k: dto[k]
                for k in (
                    "status",
                    "source_uuid",
                    "source_sha256",
                    "selection",
                    "metadata_status",
                    "copy_status",
                )
            }
            | {"status": "available"}
        ],
    )

    def forbidden(*a, **k):
        pytest.fail("NFS branch used repository/path/HDF probe")

    service.get_export_plan = service.get_export_selection = service.list_execution_artifacts = (
        service.open_artifact
    ) = forbidden
    query = dict(provider="nfs_registered", source_uuid=A, source_sha256=dto["source_sha256"])
    return service, dto, query, calls


def read(adapter, operation, query):
    async def go():
        try:
            return await adapter.read(operation, SEL, query)
        finally:
            await adapter.close()

    return asyncio.run(go())


def test_real_brief_incomplete_registration_zero_nfs_and_source_uuid_domain(monkeypatch):
    from qcl_negf_api import saved_nfs

    service, dto, query, calls = source_fixture()
    monkeypatch.setattr(saved_nfs, "_access", lambda *a, **k: pytest.fail("brief touched NFS"))
    from pathlib import Path

    import h5py
    import qcl_negf_results.saved_views as owning_views

    assert callable(owning_views.brief_result)  # Load before installing no-I/O traps.

    def forbidden(*args, **kwargs):
        pytest.fail("retained brief inspected a filesystem/HDF path")

    with monkeypatch.context() as trap:
        for name in ("stat", "lstat", "resolve", "open"):
            trap.setattr(Path, name, forbidden)
        trap.setattr(h5py, "File", forbidden)
        page = read(
            SavedResults(service, SimpleNamespace(saved_results_enabled=True), binding=BINDING),
            "brief",
            query,
        )
    assert [p["point_id"] for p in page["points"]] == ["p0", "p1", "p2"]
    assert [p["analysis_source_status"] for p in page["points"]] == [
        "registered",
        "unregistered",
        "unregistered",
    ]
    assert [p["source_commit_sha256"] is None for p in page["points"]] == [False, True, True]
    assert page["snapshot_complete"] is False and page["current_segments"] == [["p0", "p1"]]
    assert (
        page["points"][0]["source_native_identity"] == dto["record"]["points"][0]["native_identity"]
    )
    assert page["points"][0]["source_calcjob_uuid"] is None
    assert (
        calls[0]["source_uuid"] == A
        and calls[0]["expected_source_sha256"] == query["source_sha256"]
    )
    dto["source_uuid"] = B
    query_b = {**query, "source_uuid": B}
    other = read(
        SavedResults(service, SimpleNamespace(saved_results_enabled=True), binding=BINDING),
        "brief",
        query_b,
    )
    assert other["metadata_sha256"] != page["metadata_sha256"]
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        read(
            SavedResults(service, SimpleNamespace(saved_results_enabled=True), binding=BINDING),
            "brief",
            {**query_b, "cursor": page["metadata_sha256"] + ":0"},
        )


@pytest.mark.parametrize("change", ["source", "sha", "selection", "length", "reserved"])
def test_wrong_source_metadata_and_reserved_path_never_dispatch_payload(change):
    service, dto, query, calls = source_fixture()
    if change == "source":
        dto["source_uuid"] = B
    elif change == "sha":
        dto["source_sha256"] = "f" * 64
    elif change == "selection":
        dto["selection"] = {**SEL, "attempt": 1}
    elif change == "length":
        dto["metadata"]["series_result.json"] += " "
    else:
        dto["record"]["points"][0]["commit_path"] = "_aiida/commit.json"
        from qcl_negf_api.saved_nfs import canonical

        dto["source_sha256"] = query["source_sha256"] = hashlib.sha256(
            canonical(dto["record"])
        ).hexdigest()
    with pytest.raises(SavedResultError):
        read(
            SavedResults(
                service,
                SimpleNamespace(saved_results_enabled=True),
                binding=BINDING,
                worker=lambda *a: pytest.fail("invalid source reached worker"),
            ),
            "brief",
            query,
        )


@pytest.mark.parametrize(
    "key",
    [
        "namespace_id",
        "storage_identity",
        "retention_policy_id",
        "retention_owner_id",
        "hold_ref",
        "access_binding_ref",
        "allowed_computer_uuids",
    ],
)
def test_each_binding_identity_mismatch_refuses_before_child(key):
    service, dto, query, calls = source_fixture()
    binding = {**BINDING, key: [B] if key == "allowed_computer_uuids" else "foreign"}
    page = read(
        SavedResults(service, SimpleNamespace(saved_results_enabled=True), binding=binding),
        "brief",
        query,
    )
    with pytest.raises(SavedResultError, match="unavailable_storage"):
        read(
            SavedResults(
                service,
                SimpleNamespace(saved_results_enabled=True),
                binding=binding,
                worker=lambda *a: pytest.fail("wrong binding reached child"),
            ),
            "manifest",
            {
                **query,
                "point_id": "p0",
                "source_commit_sha256": page["points"][0]["source_commit_sha256"],
                "metadata_sha256": page["metadata_sha256"],
            },
        )


def test_catalogue_complete_limits_and_no_binding_required():
    service, dto, query, calls = source_fixture()
    rows = service.get_saved_sources()["sources"]
    for count in (0, 1, 2, 257):
        service.get_saved_sources = lambda *a, **k: dict(
            selection=SEL,
            complete=True,
            sources=[{**rows[0], "source_uuid": A if i == 0 else B} for i in range(count)],
        )
        if count == 257:
            with pytest.raises(SavedResultError):
                read(
                    SavedResults(service, SimpleNamespace(saved_results_enabled=True)),
                    "sources",
                    {},
                )
        else:
            result = read(
                SavedResults(service, SimpleNamespace(saved_results_enabled=True)), "sources", {}
            )
            assert len(result["sources"]) == count and result["complete"] is True
            assert result["storage_binding_status"] == "unavailable"
            assert all(s["access_status"] == "not_checked" for s in result["sources"])


@pytest.mark.parametrize(
    "mount", ["", "nfs4 server:/data rw", "ext4 /dev/x ro", "nfs4 foreign:/data ro"]
)
def test_child_access_requires_exact_ro_nfs_alias(tmp_path, mount):
    from qcl_negf_api.saved_nfs import _access

    root = tmp_path / "alias"
    root.mkdir()
    (root / "job").mkdir()
    (root / "job" / "result").mkdir()
    entries = ""
    if mount:
        fs, source, mode = mount.split()
        entries = f"10 1 0:9 / {root} {mode} - {fs} {source} {mode}\n"
    # Foreign source only meaningful with a nested mapping differing from alias.
    if "foreign" in mount:
        entries = (
            f"10 1 0:9 / {root} ro - nfs4 server:/data ro\n"
            f"11 10 0:10 / {root}/job ro - nfs4 foreign:/data ro\n"
        )
    with pytest.raises(SavedResultError):
        _access(str(root), "job/result", ["commit.json"], mountinfo=entries)


def test_direct_request_uses_local_spool_separate_root_and_native_identity(tmp_path):
    service, dto, query, calls = source_fixture()
    expected = dto["record"]["points"][0]["native_identity"]
    seen = []
    spools = []

    def worker(op, request, spool, cancelled, deadline):
        seen.append(request)
        spools.append(spool)
        assert request["data_root"] == "/run/qcl-negf-saved/job/result"
        assert str(spool).startswith("/tmp/") and not str(spool).startswith("/run/qcl-negf-saved")
        assert request["expected_identity"] == {
            **expected,
            "source_commit_sha256": dto["record"]["points"][0]["commit_sha256"],
        }
        assert (
            "run_uuid" not in request["expected_identity"]
            and "calcjob_uuid" not in request["expected_identity"]
        )
        return dict(
            views=[],
            files=[],
            source_commit_sha256=request["expected_identity"]["source_commit_sha256"],
            integrity={},
        )

    def adapter():
        return SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), binding=BINDING, worker=worker
        )

    page = read(adapter(), "brief", query)
    result = read(
        adapter(),
        "manifest",
        {
            **query,
            "point_id": "p0",
            "source_commit_sha256": page["points"][0]["source_commit_sha256"],
            "metadata_sha256": page["metadata_sha256"],
        },
    )
    assert result["repository_copied_bytes"] == result["payload_temp_copied_bytes"] == 0
    assert result["source_calcjob_uuid"] is None and result["provider_selection"] == SEL
    assert spools and all(not spool.exists() for spool in spools)


def test_direct_real_analysis_reader_same_declared_owner_no_repository_copy(tmp_path, monkeypatch):
    import h5py
    import numpy as np
    from qcl_negf_results.commits import artifact_row

    from qcl_negf_api import saved_nfs, saved_results

    service, dto, query, calls = source_fixture()
    record = dto["record"]
    pin = record["points"][0]
    native = tmp_path / "native"
    directory = native / "archive" / "p0"
    directory.mkdir(parents=True)
    identity = {
        **pin["native_identity"],
        "run_uuid": B,
    }  # Explicit imported producer, no CalcJob UUID.
    owner = directory / "analysis.h5"
    with h5py.File(owner, "w") as h:
        metadata = h.create_group("metadata")
        for k, v in dict(
            schema="qcl-negf-physics-analysis-v4",
            schema_version="4.0",
            artifact_role="physics.analysis",
            contract_set="qcl-negf.results.v1",
            current_convention="positive electron flow toward +z",
            energy_reference="saved reference",
        ).items():
            metadata.attrs[k] = np.bytes_(v)
        metadata["identity_json"] = np.bytes_(json.dumps(identity))
        h["axes/z_nm"] = [0.0, 2.0, 7.0]
        h["axes/z_nm"].attrs["units"] = np.bytes_("nm")
        h["axes/z_nm"].attrs["logical_axis_order"] = np.bytes_("z")
        h["observables/density_per_m3"] = [0.0, -2.0, 8.0]
        density = h["observables/density_per_m3"]
        density.attrs["units"] = np.bytes_("m^-3")
        density.attrs["logical_axis_order"] = np.bytes_("z")
        density.attrs["axis_coordinate_paths_json"] = np.bytes_('["axes/z_nm"]')
    commit = json.loads(dto["metadata"][pin["commit_path"]])
    commit["identity"] = identity
    commit["artifacts"] = [
        artifact_row(
            owner,
            relative="analysis.h5",
            role="physics.analysis",
            media_type="application/x-hdf5",
            schema="qcl-negf-physics-analysis-v4",
        )
    ]
    raw = json.dumps(commit)
    (directory / "commit.json").write_text(raw)
    pin.update(native_identity=identity, commit_sha256=hashlib.sha256(raw.encode()).hexdigest())
    pin["declared_analysis"].update(
        bytes=owner.stat().st_size, sha256=hashlib.sha256(owner.read_bytes()).hexdigest()
    )
    dto["metadata"][pin["commit_path"]] = raw
    series = json.loads(dto["metadata"]["series_result.json"])
    series["points"][0]["data"]["source_commit_sha256"] = pin["commit_sha256"]
    dto["metadata"]["series_result.json"] = json.dumps(series)
    for descriptor in record["metadata"]:
        raw_meta = dto["metadata"][descriptor["path"]].encode()
        descriptor.update(bytes=len(raw_meta), sha256=hashlib.sha256(raw_meta).hexdigest())
    series_raw = dto["metadata"]["series_result.json"].encode()
    record["snapshot"].update(
        result_bytes=len(series_raw),
        result_sha256=hashlib.sha256(series_raw).hexdigest(),
        metadata_set_sha256=hashlib.sha256(saved_nfs.canonical(record["metadata"])).hexdigest(),
    )
    dto["source_sha256"] = query["source_sha256"] = hashlib.sha256(
        saved_nfs.canonical(record)
    ).hexdigest()
    roots = []
    spools = []

    def access(alias, relative, paths):
        assert alias == "/run/qcl-negf-saved" and relative == "job/result"
        assert set(paths) == {"archive/p0/commit.json", "archive/p0/analysis.h5"}
        roots.append(alias)
        return native

    monkeypatch.setattr(saved_nfs, "_access", access)

    def worker(op, request, spool, cancelled, deadline):
        assert not list(spool.iterdir())  # No owner/metadata dataset copy.
        spools.append(spool)
        return saved_results._read_request({"operation": op, **request}, spool)

    def adapter():
        return SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), binding=BINDING, worker=worker
        )

    page = read(adapter(), "brief", query)
    pinned = {
        **query,
        "point_id": "p0",
        "source_commit_sha256": pin["commit_sha256"],
        "metadata_sha256": page["metadata_sha256"],
    }
    manifest = read(adapter(), "manifest", pinned)
    assert manifest["integrity"]["owner_sha_verified"] is False
    result = read(
        adapter(),
        "slice",
        {**pinned, "view_id": "density", "selection": [{"start": 0, "stop": 3, "step": 1}]},
    )
    assert result["values"] == [0.0, -2.0, 8.0] and result["axes"][0]["values"] == [0.0, 2.0, 7.0]
    assert result["selection"] == dict(run_uuid=B, execution_id="ex", attempt=1, calcjob_uuid=None)
    assert result["provider_selection"] == SEL and result["source_calcjob_uuid"] is None
    assert result["integrity"]["owner_sha_verified"] is True
    assert result["repository_copied_bytes"] == result["payload_temp_copied_bytes"] == 0
    assert roots == ["/run/qcl-negf-saved"] * 2 and all(not p.exists() for p in spools)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**BINDING, "schema": "invented"},
        {**BINDING, "root": "/tmp/alternate"},
        {**BINDING, "allowed_computer_uuids": [True]},
    ],
)
def test_binding_exact_eight_keys_and_mutable_file_unavailable(tmp_path, bad):
    from qcl_negf_api.saved_nfs import load_binding, validate_binding

    with pytest.raises(SavedResultError):
        validate_binding(bad)
    config = tmp_path / "binding.json"
    config.write_text(json.dumps(BINDING))
    assert load_binding(str(config)) is None  # Not an immutable Nix config.


def test_actor_discovers_actual_missing_producer_without_new_admin_dispatch():
    from qcl_negf_api.service import OPERATIONS, AiidaService

    owner = object.__new__(AiidaService)
    owner._leases = {}
    owner._service = SimpleNamespace(get_export_selection=lambda: "retrieved still works")
    assert callable(owner.get_saved_source)  # Proxy alone cannot discover support.
    for name in ("get_saved_sources", "get_saved_source"):
        with pytest.raises(SavedResultError, match="unsupported_contract") as error:
            owner._execute(SimpleNamespace(name=name, args=(), kwargs={}, lease_id=None))
        assert error.value.code == "unsupported_contract"
    assert (
        owner._execute(
            SimpleNamespace(name="get_export_selection", args=(), kwargs={}, lease_id=None)
        )
        == "retrieved still works"
    )
    assert not {"register_saved_source", "configure_saved_sources"} & OPERATIONS


def test_source_catalogue_shares_gate_and_cancelled_waiter_cannot_replace_owner():
    import threading

    service, dto, query, calls = source_fixture()
    entered, release = threading.Event(), threading.Event()
    catalog = service.get_saved_sources
    catalogue_calls = []

    def held(*a, **k):
        catalogue_calls.append((a, k))
        entered.set()
        assert release.wait(3)
        return catalog(*a, **k)

    service.get_saved_sources = held

    async def case():
        adapter = SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), cleanup_seconds=0.01
        )
        task = asyncio.create_task(adapter.read("sources", SEL, {}))
        owner = None
        try:
            while not entered.is_set():
                await asyncio.sleep(0.001)
            owner = adapter.active
            assert owner is not None and not owner.done()
            with pytest.raises(SavedResultError, match="busy") as busy:
                await adapter.read("brief", SEL, query)
            assert busy.value.code == "busy"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert adapter.closed and adapter.busy and adapter.active is owner and not owner.done()
            with pytest.raises(SavedResultError, match="stopping") as stopped:
                await adapter.read("sources", SEL, {})
            assert stopped.value.code == "stopping"
            assert len(catalogue_calls) == 1 and calls == []
        finally:
            release.set()
            try:
                if owner is not None:
                    # The real owner notices the cancelled read only after held metadata returns.
                    # Assert that precise terminal outcome; do not swallow other cleanup failures.
                    with pytest.raises(SavedResultError) as completed:
                        await asyncio.wrap_future(owner)
                    assert completed.value.code == "budget_exceeded"
                    assert owner.done() and not owner.cancelled()
                    assert adapter.closed and adapter.active is None
                    with pytest.raises(SavedResultError, match="stopping") as stopped:
                        await adapter.read("sources", SEL, {})
                    assert stopped.value.code == "stopping"
                    assert len(catalogue_calls) == 1 and calls == []
            finally:
                await adapter.close()

    asyncio.run(case())


def test_unconfirmed_child_reap_retains_local_spool_and_permanent_gate():
    service, dto, query, calls = source_fixture()

    def worker(*args):
        error = SavedResultError("unavailable_storage")
        error.cleanup_unconfirmed = True
        raise error

    async def case():
        adapter = SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), binding=BINDING, worker=worker
        )
        try:
            page = await adapter.read("brief", SEL, query)
            with pytest.raises(SavedResultError):
                await adapter.read(
                    "manifest",
                    SEL,
                    {
                        **query,
                        "point_id": "p0",
                        "source_commit_sha256": page["points"][0]["source_commit_sha256"],
                        "metadata_sha256": page["metadata_sha256"],
                    },
                )
            assert adapter.closed and adapter.busy and len(adapter.pending_spools) == 1
            from pathlib import Path

            assert Path(adapter.pending_spools[0].name).is_dir()
            with pytest.raises(SavedResultError):
                await adapter.read("sources", SEL, {})
        finally:
            await adapter.close()
            # Synthetic child never exists; only this fixture may discard its test spool.
            for spool in adapter.pending_spools:
                spool.cleanup()

    asyncio.run(case())


def test_metadata_digest_literal_frame_oracle_and_original_plan_whitespace():
    service, dto, query, calls = source_fixture()
    source = {k: query[k] for k in ("provider", "source_uuid", "source_sha256")}

    def c(value):
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()

    payloads = [
        c(SEL),
        c(source),
        dto["plan"].encode(),
        dto["metadata"]["series_result.json"].encode(),
    ]
    expected = hashlib.sha256(
        b"qcl-negf.portal-saved-nfs.v1\x00"
        + b"".join(len(raw).to_bytes(8, "big") + raw for raw in payloads)
    ).hexdigest()
    result = read(
        SavedResults(service, SimpleNamespace(saved_results_enabled=True)), "brief", query
    )
    assert (
        result["metadata_sha256"] == expected
        and result["storage_binding_status"] == "binding_unavailable"
    )
    # AiiDA retained input bytes remain authority even if captured plan uses different whitespace.
    from qcl_negf_api.saved_nfs import canonical

    dto["metadata"]["scientific_plan.json"] = json.dumps(json.loads(dto["plan"]), indent=2)
    for descriptor in dto["record"]["metadata"]:
        if descriptor["path"] == "scientific_plan.json":
            raw = dto["metadata"]["scientific_plan.json"].encode()
            descriptor.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    dto["record"]["snapshot"]["metadata_set_sha256"] = hashlib.sha256(
        canonical(dto["record"]["metadata"])
    ).hexdigest()
    dto["source_sha256"] = query["source_sha256"] = hashlib.sha256(
        canonical(dto["record"])
    ).hexdigest()
    page = read(SavedResults(service, SimpleNamespace(saved_results_enabled=True)), "brief", query)
    assert page["identity"]["plan_fingerprint"] == SHA
    assert dto["record"]["plan"]["sha256"] == hashlib.sha256(dto["plan"].encode()).hexdigest()


def test_unknown_actor_metadata_completion_cannot_reenable_gate():
    from qcl_negf_api.service import ActorDeadline, ActorReceipt

    service, dto, query, calls = source_fixture()
    receipt = ActorReceipt("operation", "get_saved_sources", "unknown", "ready")

    def unknown(*a, **k):
        raise ActorDeadline("operation_deadline", receipt)

    service.get_saved_sources = unknown

    async def case():
        adapter = SavedResults(service, SimpleNamespace(saved_results_enabled=True))
        try:
            with pytest.raises(SavedResultError, match="unavailable_storage"):
                await adapter.read("sources", SEL, {})
            assert adapter.closed and adapter.busy and adapter.active is None
            with pytest.raises(SavedResultError):
                await adapter.read("brief", SEL, query)
        finally:
            await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "error_code", ["source_identity_mismatch", "source_unavailable", "corrupt_result"]
)
def test_private_source_error_detail_never_enters_http(error_code):
    from fastapi.testclient import TestClient

    from qcl_negf_api.api import create_app
    from qcl_negf_api.settings import Settings

    service, dto, query, calls = source_fixture()

    class SourceError(Exception):
        code = error_code

    def broken(*a, **k):
        raise SourceError("/private/nfs/secret-registration-tree")

    service.get_saved_source = broken
    settings = Settings("0123456789abcdef" * 4, ("33333333-3333-4333-8333-333333333333",))
    adapter = SavedResults(service, settings)
    with TestClient(create_app(settings, SimpleNamespace(), saved_results=adapter)) as client:
        response = client.get(
            f"/api/v1/runs/{SEL['run_uuid']}/saved-results/brief",
            params={**{k: SEL[k] for k in ("execution_id", "attempt", "calcjob_uuid")}, **query},
            headers={"Authorization": "Bearer " + settings.token},
        )
        assert response.status_code in (409, 422, 503)
        assert response.headers["cache-control"] == "no-store"
        assert (
            b"/private" not in response.content and b"secret-registration" not in response.content
        )


@pytest.mark.parametrize(
    "query",
    [
        {"provider": "nfs_registered"},
        {"provider": "nfs_registered", "source_uuid": A},
        {"provider": "nfs_registered", "source_uuid": "not-uuid", "source_sha256": "a" * 64},
        {"provider": "aiida_retrieved", "source_uuid": A, "source_sha256": "a" * 64},
        {"provider": "latest"},
        {"provider": "nfs_registered", "source_uuid": A, "source_sha256": None},
    ],
)
def test_selector_refusal_precedes_actor_and_no_fallback(query):
    service, dto, pins, calls = source_fixture()
    with pytest.raises(SavedResultError, match="selection_required"):
        read(SavedResults(service, SimpleNamespace(saved_results_enabled=True)), "brief", query)
    assert calls == []


def test_old_and_explicit_retrieved_keep_literal_digest_and_repository_flow():
    from test_saved_results import fixture

    service, files, opened, raw, brief = fixture()
    plan = service.get_export_plan()["plan"]
    expected = hashlib.sha256(
        len(plan).to_bytes(8, "big")
        + plan
        + files["result/series_result.json"]
        + json.dumps(SEL, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    implicit = read(
        SavedResults(service, SimpleNamespace(saved_results_enabled=True), brief=brief), "brief", {}
    )
    explicit = read(
        SavedResults(service, SimpleNamespace(saved_results_enabled=True), brief=brief),
        "brief",
        {"provider": "aiida_retrieved"},
    )
    assert implicit == explicit and implicit["metadata_sha256"] == expected
    assert implicit["provider_source"] == dict(
        provider="aiida_retrieved", source_uuid=None, source_sha256=None
    )
    assert opened == ["result/series_result.json", "closed:result/series_result.json"] * 2


@pytest.mark.parametrize("kind", ["response", "plan", "points", "descriptors", "metadata"])
def test_literal_dto_limits_fail_before_any_data_open(kind):
    from qcl_negf_api.saved_nfs import canonical

    service, dto, query, calls = source_fixture()
    if kind == "response":
        dto["oversized"] = "x" * 2097153
    elif kind == "plan":
        dto["plan"] = " " * 1048577
    elif kind == "points":
        dto["record"]["points"] = [{}] * 257
    elif kind == "descriptors":
        dto["record"]["metadata"] = [{}] * 1025
    else:
        raw = '"' + "x" * 1048575 + '"'
        dto["metadata"]["oversized.json"] = raw
        dto["record"]["metadata"].append(
            dict(
                path="oversized.json",
                bytes=1048577,
                sha256=hashlib.sha256(raw.encode()).hexdigest(),
                retained_node_uuid=A,
            )
        )
    if kind not in ("response", "plan"):
        dto["source_sha256"] = query["source_sha256"] = hashlib.sha256(
            canonical(dto["record"])
        ).hexdigest()
    with pytest.raises(SavedResultError, match="budget_exceeded"):
        read(
            SavedResults(
                service,
                SimpleNamespace(saved_results_enabled=True),
                worker=lambda *a: pytest.fail("oversized DTO reached child"),
            ),
            "brief",
            query,
        )


def test_declared_owner_literal_64_mib_plus_one_refused_without_open():
    from qcl_negf_api.saved_nfs import declarations

    service, dto, query, calls = source_fixture()
    commit = json.loads(dto["metadata"]["archive/p0/commit.json"])
    commit["artifacts"][0]["bytes"] = 67108865
    with pytest.raises(SavedResultError, match="budget_exceeded"):
        declarations(commit, "archive/p0/commit.json")
