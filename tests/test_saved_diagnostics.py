"""Portal operation routing controls; tiny declared histories, no scientific run."""

import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_saved_nfs import BINDING, source_fixture
from test_saved_results import SEL, fixture

from qcl_negf_api.saved_results import SavedResultError, SavedResults, _read_request


def run_read(adapter, operation, query):
    async def case():
        try:
            return await adapter.read(operation, SEL, query)
        finally:
            await adapter.close()

    return asyncio.run(case())


def repository_history_fixture(with_analysis=False):
    service, files, opened, raw, brief = fixture()
    commit = json.loads(raw)
    history = b"only selected history"
    commit["artifacts"] = [
        dict(
            path="history.h5",
            role="science.history",
            schema="qcl-negf-scientific-history-v4",
            media_type="application/x-hdf5",
            profile="science",
            bytes=len(history),
            sha256=hashlib.sha256(history).hexdigest(),
            dependencies=[],
        )
    ]
    if with_analysis:
        commit["artifacts"].append(
            dict(
                path="analysis.h5",
                role="physics.analysis",
                schema="unsupported-unselected-analysis",
                media_type="application/x-hdf5",
                profile="science",
                bytes=67108865,
                sha256="f" * 64,
                dependencies=[],
            )
        )
    commit["performance"] = dict(
        checkpoint_payload_seconds=2.0, checkpoint_payload_bytes=9007199254740993
    )
    raw = json.dumps(commit).encode()
    files["result/archive/p/commit.json"] = raw
    files["result/archive/p/history.h5"] = history
    series = json.loads(files["result/series_result.json"])
    series["points"][0]["data"]["source_commit_sha256"] = hashlib.sha256(raw).hexdigest()
    files["result/series_result.json"] = json.dumps(series).encode()
    return service, files, opened, brief


@pytest.mark.parametrize("with_analysis", [False, True])
def test_retrieved_diagnostics_selects_only_history(with_analysis):
    service, files, opened, brief = repository_history_fixture(with_analysis)
    calls = []

    def worker(operation, request, root, cancelled, deadline):
        calls.append((operation, request))
        assert sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) == [
            "archive/p/commit.json",
            "archive/p/history.h5",
        ]
        return {
            "schema": "qcl-negf.saved-diagnostics.v1",
            "source_commit_sha256": request["expected_identity"]["source_commit_sha256"],
        }

    def adapter():
        return SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), brief=brief, worker=worker
        )

    page = run_read(adapter(), "brief", {})
    query = dict(
        point_id="p",
        source_commit_sha256=page["points"][0]["source_commit_sha256"],
        metadata_sha256=page["metadata_sha256"],
    )
    result = run_read(adapter(), "diagnostics", query)
    assert result["provider_selection"] == SEL and calls[0][0] == "diagnostics"
    assert "result/archive/p/analysis.h5" not in opened
    assert not {"run_uuid", "calcjob_uuid"} & set(calls[0][1]["expected_identity"])
    run_read(
        adapter(),
        "history",
        {
            **query,
            "phase": "scba",
            "selection": {"start": 0, "stop": 5, "step": 1, "columns": ["r_K", "J"]},
        },
    )
    assert calls[-1][1]["phase"] == "scba" and calls[-1][1]["selection"]["stop"] == 5


@pytest.mark.parametrize(
    "selection",
    [
        {"start": True, "stop": 5, "step": 1, "columns": ["J"]},
        {"start": 0, "stop": 513, "step": 1, "columns": ["J"]},
        {"start": 0, "stop": 5, "step": 0, "columns": ["J"]},
        {"start": 0, "stop": 5, "step": 1, "columns": ["J", "J"]},
        {"start": 0, "stop": 5, "step": 1, "columns": ["/private/path"]},
        {"start": 0, "stop": 5, "step": 1, "columns": ["sequence"]},
        {"start": 0, "stop": 5, "step": 1, "columns": ["J"], "latest": True},
    ],
)
def test_history_envelope_refuses_before_actor(selection):
    service, files, opened, brief = repository_history_fixture()
    service.get_export_selection = lambda *a, **k: pytest.fail("invalid envelope reached actor")
    with pytest.raises(SavedResultError):
        run_read(
            SavedResults(service, SimpleNamespace(saved_results_enabled=True), brief=brief),
            "history",
            dict(
                point_id="p",
                source_commit_sha256="a" * 64,
                metadata_sha256="a" * 64,
                phase="scba",
                selection=selection,
            ),
        )


@pytest.mark.parametrize("analysis_present", [False, True])
def test_nfs_diagnostics_ignores_unselected_analysis_preserves_registration(analysis_present):
    from qcl_negf_api.saved_nfs import canonical

    service, dto, pins, calls = source_fixture()
    point = dto["record"]["points"][0]
    commit = json.loads(dto["metadata"][point["commit_path"]])
    analysis = commit["artifacts"][0]
    analysis.update(bytes=67108865)
    point["declared_analysis"].update(bytes=67108865)
    if not analysis_present:
        commit["artifacts"] = []
        point["declared_analysis"] = None
    history = dict(
        path="history.h5",
        role="science.history",
        schema="qcl-negf-scientific-history-v4",
        media_type="application/x-hdf5",
        profile="science",
        bytes=24,
        sha256="b" * 64,
        dependencies=[],
    )
    commit["artifacts"].append(history)
    raw = json.dumps(commit)
    dto["metadata"][point["commit_path"]] = raw
    point["commit_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    series = json.loads(dto["metadata"]["series_result.json"])
    series["points"][0]["data"]["source_commit_sha256"] = point["commit_sha256"]
    dto["metadata"]["series_result.json"] = json.dumps(series)
    for descriptor in dto["record"]["metadata"]:
        data = dto["metadata"][descriptor["path"]].encode()
        descriptor.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    data = dto["metadata"]["series_result.json"].encode()
    dto["record"]["snapshot"].update(
        result_bytes=len(data),
        result_sha256=hashlib.sha256(data).hexdigest(),
        metadata_set_sha256=hashlib.sha256(canonical(dto["record"]["metadata"])).hexdigest(),
    )
    dto["source_sha256"] = pins["source_sha256"] = hashlib.sha256(
        canonical(dto["record"])
    ).hexdigest()

    def worker(operation, request, root, cancelled, deadline):
        assert operation == "diagnostics"
        assert (
            request["expected_identity"]["attempt"] == 1
            and "calcjob_uuid" not in request["expected_identity"]
        )
        from qcl_negf_api.saved_nfs import declarations

        closure, owner = declarations(
            json.loads(request["commit_metadata"]), request["commit_path"], operation=operation
        )
        assert closure == ["archive/p0/history.h5"] and owner.role == "science.history"
        assert not list(root.iterdir())
        return {"history": {"status": "available"}, "source_commit_sha256": point["commit_sha256"]}

    # Metadata digest is independent of analysis availability.
    page_adapter = SavedResults(
        service, SimpleNamespace(saved_results_enabled=True), binding=BINDING, worker=worker
    )
    # Retained registration meaning remains separate from role selection.
    from qcl_negf_api.saved_nfs import retained

    record, raws, plan, series_raw, *_ = retained(dto, SEL, pins)

    def frame(raw):
        return len(raw).to_bytes(8, "big") + raw

    digest = hashlib.sha256(
        b"qcl-negf.portal-saved-nfs.v1\0"
        + frame(canonical(SEL))
        + frame(canonical(pins))
        + frame(plan)
        + frame(series_raw)
    ).hexdigest()
    result = run_read(
        page_adapter,
        "diagnostics",
        {
            **pins,
            "point_id": "p0",
            "source_commit_sha256": point["commit_sha256"],
            "metadata_sha256": digest,
        },
    )
    assert (
        result["source_attempt"] == 1
        and result["source_native_identity"] == point["native_identity"]
    )
    assert (
        point["declared_analysis"]["bytes"] == 67108865
        if analysis_present
        else point["declared_analysis"] is None
    )


def test_two_registered_points_retained_and_real_brief_without_payload_probes(monkeypatch):
    from pathlib import Path

    import h5py
    import qcl_negf_results.saved_views as owning_views

    from qcl_negf_api import saved_nfs

    service, dto, pins, calls = source_fixture()
    first = dto["record"]["points"][0]
    second = deepcopy(first)
    second.update(
        point_id="p1",
        commit_path="archive/p1/commit.json",
        source_attempt=2,
        row_attempt=2,
    )
    commit = json.loads(dto["metadata"][first["commit_path"]])
    commit["identity"].update(point_id="p1", attempt=2)
    second["native_identity"] = commit["identity"]
    second["declared_analysis"]["path"] = "archive/p1/analysis.h5"
    raw = json.dumps(commit)
    second["commit_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    dto["metadata"][second["commit_path"]] = raw
    dto["record"]["points"].append(second)
    series = json.loads(dto["metadata"]["series_result.json"])
    series["points"][1]["data"]["source_commit_sha256"] = second["commit_sha256"]
    dto["metadata"]["series_result.json"] = json.dumps(series)
    dto["record"]["metadata"].append(
        {"path": second["commit_path"], "retained_node_uuid": dto["source_uuid"]}
    )
    for descriptor in dto["record"]["metadata"]:
        data = dto["metadata"][descriptor["path"]].encode()
        descriptor.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    series_raw = dto["metadata"]["series_result.json"].encode()
    dto["record"]["snapshot"].update(
        result_bytes=len(series_raw),
        result_sha256=hashlib.sha256(series_raw).hexdigest(),
        metadata_set_sha256=hashlib.sha256(
            saved_nfs.canonical(dto["record"]["metadata"])
        ).hexdigest(),
    )
    dto["source_sha256"] = pins["source_sha256"] = hashlib.sha256(
        saved_nfs.canonical(dto["record"])
    ).hexdigest()
    assert first["commit_sha256"] != second["commit_sha256"]
    assert callable(owning_views.brief_result)  # Actual producer, never a fake projection.

    def forbidden(*args, **kwargs):
        pytest.fail("retained/brief inspected a filesystem, HDF or payload worker")

    monkeypatch.setattr(saved_nfs, "_access", forbidden)
    with monkeypatch.context() as trap:
        for name in ("stat", "lstat", "resolve", "open"):
            trap.setattr(Path, name, forbidden)
        trap.setattr(h5py, "File", forbidden)
        *_, captured = saved_nfs.retained(dto, SEL, pins)
        assert list(captured) == ["p0", "p1"]
        page = run_read(
            SavedResults(
                service,
                SimpleNamespace(saved_results_enabled=True),
                binding=BINDING,
                worker=forbidden,
            ),
            "brief",
            pins,
        )
    assert page["registered_point_ids"] == ["p0", "p1"]
    assert [p["point_id"] for p in page["points"]] == ["p0", "p1", "p2"]
    assert [p["analysis_source_status"] for p in page["points"]] == [
        "registered",
        "registered",
        "unregistered",
    ]
    assert [p["source_commit_sha256"] for p in page["points"]] == [
        first["commit_sha256"],
        second["commit_sha256"],
        None,
    ]
    assert [p["source_native_identity"] for p in page["points"][:2]] == [
        first["native_identity"],
        second["native_identity"],
    ]
    assert [p["source_attempt"] for p in page["points"]] == [1, 2, 1]
    assert page["provider_selection"] == SEL and page["current_segments"] == [["p0", "p1"]]
    assert page["repository_copied_bytes"] == page["payload_temp_copied_bytes"] == 0
    assert calls[0]["expected_source_sha256"] == pins["source_sha256"]


def test_real_diagnostic_reader_native_coordinates_and_masks(tmp_path):
    import h5py
    import numpy as np
    from qcl_negf_results.commits import artifact_row

    identity = {"point_id": "p", "execution_id": "ex", "attempt": 1, "plan_fingerprint": "a" * 64}
    path = tmp_path / "history.h5"
    with h5py.File(path, "w") as h:
        m = h.create_group("metadata")
        for k, v in {
            "schema": "qcl-negf-scientific-history-v4",
            "schema_version": "4.0",
            "artifact_role": "science.history",
            "contract_set": "qcl-negf.results.v1",
        }.items():
            m.attrs[k] = np.bytes_(v)
        m.attrs["scba_rows"] = 5
        m.attrs["outer_rows"] = 0
        m["identity_json"] = np.bytes_(json.dumps(identity))
        for key, values in {
            "sequence": [100, 101, 102, 103, 104],
            "iteration": [1, 2, 3, 1, 2],
            "outer_iteration": [1, 1, 1, 2, 2],
        }.items():
            h["scba/" + key] = np.asarray(values, dtype="i8")
        h["scba/r_K"] = [0.1, 0.001, float("nan"), 0.2, 0.02]
        h["scba/J"] = [9000.0, 9100.0, 9200.0, 7000.0, 7100.0]
        h["scba/J"].attrs["units"] = np.bytes_("A/m^2")
        for key in ("sequence", "iteration", "r_U", "J"):
            h["outer/" + key] = np.asarray(
                [], dtype="i8" if key in ("sequence", "iteration") else "f8"
            )
    commit = {
        "schema": "qcl-negf.artifact-commit.v2",
        "contract_set": "qcl-negf.results.v1",
        "generation": 7,
        "identity": identity,
        "terminal_status": "failed",
        "quality": "unconverged",
        "artifacts": [
            artifact_row(
                path,
                relative="history.h5",
                role="science.history",
                schema="qcl-negf-scientific-history-v4",
                media_type="application/x-hdf5",
            )
        ],
        "performance": {
            "checkpoint_payload_seconds": 2.0,
            "checkpoint_payload_bytes": 9007199254740993,
        },
    }
    raw = json.dumps(commit).encode()
    (tmp_path / "commit.json").write_bytes(raw)
    expected = {**identity, "source_commit_sha256": hashlib.sha256(raw).hexdigest()}
    descriptor = _read_request(
        {"operation": "diagnostics", "commit_path": "commit.json", "expected_identity": expected},
        tmp_path,
    )
    assert descriptor["closed"] is None and descriptor["resources"]["checkpoint"][
        "checkpoint_payload_bytes"
    ] == {"kind": "integer", "decimal": "9007199254740993"}
    result = _read_request(
        {
            "operation": "history",
            "commit_path": "commit.json",
            "expected_identity": expected,
            "phase": "scba",
            "selection": {"start": 0, "stop": 5, "step": 1, "columns": ["r_K", "J"]},
        },
        tmp_path,
    )
    assert result["coordinates"]["sequence"] == [100, 101, 102, 103, 104]
    assert result["coordinates"]["iteration"] == [1, 2, 3, 1, 2]
    assert result["columns"][0]["values"] == [0.1, 0.001, None, 0.2, 0.02]
    assert result["columns"][0]["invalid_mask"] == [None, None, "nan", None, None]
    assert result["integrity"]["owner_sha_verified"] is True
