"""Tiny no-solve seam fixtures; production NFS remains a separate native gate."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.live_nfs import read_request
from qcl_negf_api.saved_results import SavedResultError, SavedResults
from qcl_negf_api.settings import Settings
from test_saved_nfs import BINDING, COMPUTER
from test_saved_results import SEL, SHA

TOKEN = "0123456789abcdef" * 4


def test_routes_auth_exact_body_and_source_changed():
    calls = []

    class Adapter:
        async def close(self):
            pass

        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            if operation == "live-history":
                raise SavedResultError("source_changed")
            return {"status": "unavailable", "reason": "performance_binding_missing"}

    settings = Settings(TOKEN, (COMPUTER,))
    base = f"/api/v1/runs/{SEL['run_uuid']}/live-results/"
    exact = {k: v for k, v in SEL.items() if k != "run_uuid"}
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(create_app(settings, SimpleNamespace(), saved_results=Adapter())) as client:
        assert client.post(base + "performance", json=exact).status_code == 401
        answer = client.post(base + "performance", json=exact, headers=headers)
        assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
        assert calls[-1] == ("performance", SEL, {})
        assert answer.json()["reason"] == "performance_binding_missing"
        for extra in ({"path": "/private"}, {"namespace_id": "caller"}, {"point_id": "p"}):
            assert client.post(base + "performance", json={**exact, **extra}, headers=headers).status_code == 422
        history = dict(**exact, point_id="p", source_attempt=3, index_sha256=SHA,
                       generation=1, phase="scba", selection=dict(start=0, stop=1, step=1, columns=["r_K"]))
        response = client.post(base + "live-history", json=history, headers=headers)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "source_changed"


def test_active_mapping_uses_child_without_sealed_registrar(tmp_path):
    seen = []
    owner = {**SEL, "point_id": "p", "source_attempt": 3, "plan_fingerprint": SHA}
    dto = dict(schema="qcl-negf.live-source.v1", status="available", selection=owner,
               access_mode="selected_closed_copy", descriptor_verified=False,
               descriptor_path="job/live/current.json",
               storage=dict(namespace_id="cluster", storage_identity="nfs:results",
                            computer_uuid=COMPUTER, relative_job_root="job"))

    def get_source(*args, **kwargs):
        assert kwargs["source_attempt"] == 3  # no source<=input assumption
        return dto

    def worker(operation, request, root, cancelled, deadline):
        seen.append((operation, request))
        assert request["expected_selection"] == owner and root.is_dir()
        return dict(scope="closed_prefix", descriptor_verified=True)

    service = SimpleNamespace(get_live_history_source=get_source)
    adapter = SavedResults(service, SimpleNamespace(saved_results_enabled=True), binding=BINDING, worker=worker)
    try:
        result = asyncio.run(adapter.read("live-source", SEL, dict(point_id="p", source_attempt=3)))
        assert result["descriptor_verified"] is True and len(seen) == 1
        dto["storage"]["computer_uuid"] = SEL["calcjob_uuid"]
        with pytest.raises(SavedResultError, match="unavailable_storage"):
            asyncio.run(adapter.read("live-source", SEL, dict(point_id="p", source_attempt=3)))
        assert len(seen) == 1
    finally:
        asyncio.run(adapter.close())


def test_child_reads_actual_descriptor_and_index_not_mapping_claim(monkeypatch, tmp_path):
    import qcl_negf_api.live_nfs as live

    owner = {**SEL, "point_id": "p", "source_attempt": 3, "plan_fingerprint": SHA}
    native = dict(point_id="p", execution_id="ex", attempt=3, plan_fingerprint=SHA)
    job = tmp_path / "job"
    history = job / "live/ex/p/attempt-3/history"
    history.mkdir(parents=True)
    index = dict(schema="qcl-negf-scientific-history-index-v2", contract_set="qcl-negf.results.v1",
                 identity=native, segments=[], generation=1, updated_unix=1,
                 scba_rows=0, outer_rows=0)
    raw = json.dumps(index).encode()
    (history / "index.json").write_bytes(raw)
    descriptor = dict(schema="qcl-negf.live-prefix.v1", identity=native,
                      access_mode="selected_closed_copy", index_path="live/ex/p/attempt-3/history/index.json",
                      index_sha256=hashlib.sha256(raw).hexdigest(), generation=1,
                      scba_rows=0, outer_rows=0, live_publication_incomplete=True)
    target = job / "live/current.json"
    target.write_text(json.dumps(descriptor))
    accesses = []

    def access(alias, relative, paths):
        accesses.append(paths)
        assert alias == BINDING["root"] and relative == "job"
        return job

    monkeypatch.setattr(live, "_access", access)
    request = dict(operation="live-source", binding=BINDING, relative_job_root="job",
                   expected_selection=owner, query=dict(point_id="p", source_attempt=3))
    result = read_request(request, tmp_path)
    assert result["descriptor_verified"] is True and result["live_publication_incomplete"] is True
    assert result["cutoff"] == dict(scba_rows=0, outer_rows=0) and accesses[0] == ["live/current.json"]
    descriptor["identity"] = {**native, "point_id": "foreign"}
    target.write_text(json.dumps(descriptor))
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        read_request(request, tmp_path)
    descriptor["identity"] = native
    descriptor["index_sha256"] = "f" * 64
    target.write_text(json.dumps(descriptor))
    with pytest.raises(SavedResultError, match="source_changed"):
        read_request(request, tmp_path)


def test_performance_raw_transport_uses_independent_reader_and_keeps_unknown(tmp_path):
    from qcl_negf_contracts.messages import ContractError
    from qcl_negf_results.capture import make_binding
    from qcl_negf_results.telemetry import TelemetryWriter

    science = tmp_path / "result"
    science.mkdir()
    sentinel = b'{"science":"unchanged"}\n'
    (science / "series_result.json").write_bytes(sentinel)
    native = {k: SEL[k] for k in ("calcjob_uuid", "execution_id", "attempt")}
    native["plan_fingerprint"] = SHA
    writer = TelemetryWriter(tmp_path / "performance", native, {})
    closed = writer.close()
    pins = [dict(point_id="borrowed", source_attempt=1, point_commit_path="result/prior/commit.json",
                 point_commit_sha256="b" * 64)]
    value = make_binding(tmp_path, selection=native, result_path="result/series_result.json",
                         point_pins=pins, closed_path=closed, capture_status={"terminal_tail": "unknown"})
    raw = (tmp_path / "performance-binding.json").read_bytes()
    binding = dict(selection=native, sidecar_sha256=hashlib.sha256(raw).hexdigest(),
                   point_pins=pins, runner_result=value["runner_result"])
    spool = tmp_path / "request"
    spool.mkdir()
    request = dict(operation="performance", sidecar_utf8=raw.decode(), expected_binding=binding)
    answer = read_request(request, spool)
    assert answer["scope"] == "execution_attempt" and answer["binding"]["point_pins"] == pins
    assert answer["coverage"]["total_scheduler_grant"] == "not_measured"
    assert answer["summary"]["max_observed_rss_bytes"] is None
    assert (science / "series_result.json").read_bytes() == sentinel
    request["sidecar_utf8"] += " "
    with pytest.raises(ContractError):
        read_request(request, spool)
