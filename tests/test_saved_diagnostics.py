"""Retained canonical native5 history: exact cursor/IEEE/missing semantics."""

import json
from copy import deepcopy

import h5py
import numpy as np
import pytest
from qcl_negf_api.saved_results import SavedResultError
from test_saved_results import native_case as native_case
from test_saved_results import read


@pytest.fixture
def native_history_case(native_case):
    from result_v2_fixture import encoded, metadata, put, record, reference

    c = native_case
    root = c.data
    identity = c.state["identity"]
    records = [record(1, 1), record(2, 2, "7ff8000000000001"), record(3, 3, "c010000000000000")]
    for row in records:
        row["identity"] = deepcopy(identity)
    raws = [encoded(row) for row in records]
    with h5py.File(root / "retained-history.h5", "w") as handle:
        metadata(handle, identity=identity)
        handle["records/offsets"] = np.cumsum([0] + list(map(len, raws)), dtype=np.uint64)
        handle["records/json_utf8"] = np.frombuffer(b"".join(raws), dtype=np.uint8)
        handle["records/stream_ordinal"] = np.array([1, 2, 3], dtype="<u8")
    leaf = reference(
        root, "retained-history.h5", "qcl-negf-scientific-history-v5", "application/x-hdf5"
    )
    totals = dict(records="3", last_record="3", scba_rows="3", outer_rows="0", witness_updates="0")
    page = dict(
        schema="qcl-negf.scientific-catalog.v1",
        contract_set="qcl-negf.results.v2",
        identity=identity,
        invocation={"source_epoch": "epoch"},
        generation="1",
        previous=None,
        artifacts=[dict(leaf, role="science.history", profile="science", dependencies=[])],
        entries="1",
        totals=totals,
        range={"first_record": "1", "last_record": "3"},
    )
    page_ref = put(root, "retained-catalog.json", page, page["schema"])
    pin = dict(head=page_ref, entries="1", **totals)
    manifest = json.loads((root / c.state["manifest"]["path"]).read_bytes())
    checkpoint = json.loads((root / manifest["checkpoint"]["path"]).read_bytes())
    checkpoint["history"] = deepcopy(pin)
    cpref = put(root, "checkpoint-with-history.json", checkpoint, checkpoint["schema"])
    manifest["checkpoint"] = cpref
    manifest["history"] = pin
    # No final assertion is borrowed from the different, unselected checkpoint.
    manifest["artifacts"] = [
        a for a in manifest["artifacts"] if a["role"] not in ("physics.analysis", "recovery")
    ] + [
        dict(
            cpref,
            role="recovery",
            profile="full-state",
            dependencies=[p["path"] for p in checkpoint["payloads"]],
        )
    ]
    for axis in ("convergence", "physics"):
        manifest["status"][axis] = dict(
            state="not_measured", reason="synthetic checkpoint", evidence=None
        )
    c.state["manifest"] = put(root, "selected-history.json", manifest, manifest["schema"])
    c.source.update(
        kind="checkpoint",
        selected_state=c.state,
        diagnostics=None,
        observations=None,
        reason="interrupted",
    )
    c.query["state_manifest_sha256"] = c.state["manifest"]["sha256"]
    return c


def test_retained_history_uses_public_canonical_cursor_and_preserves_invalid(native_history_case):
    c = native_history_case
    descriptor = read(c, "diagnostics")["view"]
    assert "J" in descriptor["fields"]["scba"] and descriptor["units"]["status"] == "not_loaded"
    body = dict(source_state=c.state, stage="scba", fields=["J"], maximum_records=1, cursor=None)
    first = read(c, "history", body)["view"]
    assert first["rows"][0]["cells"]["J"]["value"] == 3.0 and not first["at_cutoff"]
    second = read(c, "history", {**body, "cursor": first["cursor"]})["view"]
    assert second["rows"][0]["cells"]["J"]["value"] is None
    assert second["rows"][0]["cells"]["J"]["invalid_reason"] == "nan"
    assert second["rows"][0]["cells"]["J"]["tagged"]["bits"] == "7ff8000000000001"
    assert second["rows"][0]["break_before"] is True and second["cursor"] != first["cursor"]
    third = read(c, "history", {**body, "cursor": second["cursor"]})["view"]
    assert third["rows"][0]["cells"]["J"]["value"] == -4.0 and third["at_cutoff"]
    assert len(third["records"]) == 1 and not third["witness_updates"]


def test_foreign_cursor_and_unpublished_field_refused_without_rebase(native_history_case):
    c = native_history_case
    body = dict(source_state=c.state, stage="scba", fields=["J"], maximum_records=1, cursor=None)
    cursor = read(c, "history", body)["view"]["cursor"]
    cursor["source_epoch"] = "foreign"
    with pytest.raises(SavedResultError):
        read(c, "history", {**body, "cursor": cursor})
    with pytest.raises(SavedResultError):
        read(c, "history", {**body, "fields": ["tensor"]})


def test_real_child_reads_frozen_history_and_exact_next_cursor(
    native_history_case, tmp_path, monkeypatch
):
    from qcl_negf_api import saved_results

    c = native_history_case
    wrapper = tmp_path / "synthetic_history_child.py"
    wrapper.write_text(
        "from qcl_negf_api import saved_nfs,saved_results\n"
        + "saved_nfs.ROOT="
        + repr(str(c.root))
        + "\nsaved_nfs._mountinfo=lambda:"
        + repr(c.mount)
        + "\nraise SystemExit(saved_results._main())\n"
    )
    original = saved_results.subprocess.Popen
    children = []

    def spawn(args, **kwargs):
        child = original([args[0], str(wrapper), args[-1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(saved_results.subprocess, "Popen", spawn)
    c.adapter.worker = saved_results._worker
    body = dict(source_state=c.state, stage="scba", fields=["J"], maximum_records=1, cursor=None)
    first = read(c, "history", body)["view"]
    second = read(c, "history", {**body, "cursor": first["cursor"]})["view"]
    assert first["rows"][0]["cells"]["J"]["value"] == 3.0
    assert second["rows"][0]["cells"]["J"]["tagged"]["bits"] == "7ff8000000000001"
    assert second["rows"][0]["cells"]["J"]["value"] is None
    assert len(children) == 2
    assert all(p.poll() is not None and p.stdout.closed and p.stderr.closed for p in children)
