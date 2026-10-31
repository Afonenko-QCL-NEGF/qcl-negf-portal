from types import SimpleNamespace

from fastapi.testclient import TestClient
from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings
from test_saved_results_api import CODE, JOB, RUN, TOKEN


def test_native_history_exact_body_auth_no_store_and_no_legacy_row_ranges():
    calls = []

    class Saved:
        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"operation": operation}

        async def close(self):
            pass

    headers = {"Authorization": "Bearer " + TOKEN}
    exact = dict(
        execution_id="exec",
        attempt=2,
        calcjob_uuid=JOB,
        provider="native_nfs",
        point_id="point",
        native_binding_uuid=CODE,
        state_manifest_sha256="a" * 64,
    )
    body = {
        **exact,
        "source_state": {"manifest": {"sha256": "a" * 64}},
        "stage": "scba",
        "fields": ["J"],
        "maximum_records": 2,
        "cursor": None,
    }
    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())
    ) as client:
        base = f"/api/v1/runs/{RUN}/saved-results/"
        assert client.get(base + "diagnostics", params=exact).status_code == 401
        assert client.post(base + "history", json=body).status_code == 401
        answer = client.post(base + "history", json=body, headers=headers)
        assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
        assert calls[-1][2]["fields"] == ["J"] and calls[-1][2]["cursor"] is None
        assert (
            client.post(
                base + "history",
                json={**body, "phase": "scba", "selection": {"start": 0, "stop": 2}},
                headers=headers,
            ).status_code
            == 422
        )
