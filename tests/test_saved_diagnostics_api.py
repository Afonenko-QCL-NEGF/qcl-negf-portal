from types import SimpleNamespace

from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.saved_results import SavedResultError
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
RUN = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"


def test_diagnostic_routes_exact_auth_no_store_and_history_shape():
    calls = []

    class Saved:
        async def close(self):
            pass

        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            if query.get("source_commit_sha256") == "f" * 64:
                raise SavedResultError("identity_mismatch")
            return {"operation": operation}

    settings = Settings(TOKEN, ("33333333-3333-4333-8333-333333333333",))
    base = f"/api/v1/runs/{RUN}/saved-results/"
    with TestClient(create_app(settings, SimpleNamespace(), saved_results=Saved())) as client:
        exact = dict(
            execution_id="ex",
            attempt=2,
            calcjob_uuid=JOB,
            point_id="p",
            source_commit_sha256="a" * 64,
            metadata_sha256="b" * 64,
        )
        assert client.get(base + "diagnostics", params=exact).status_code == 401
        assert client.post(base + "history", json={}).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        result = client.get(base + "diagnostics", params=exact, headers=headers)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert calls[-1][0] == "diagnostics"
        body = {
            **exact,
            "phase": "scba",
            "selection": {"start": 0, "stop": 5, "step": 1, "columns": ["r_K", "J"]},
        }
        response = client.post(base + "history", json=body, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls[-1][0] == "history" and calls[-1][2]["phase"] == "scba"
        assert (
            client.post(
                base + "history", json={**body, "path": "/private"}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                base + "history", json={**body, "view_id": "density"}, headers=headers
            ).status_code
            == 422
        )
        failed = client.get(
            base + "diagnostics",
            params={**exact, "source_commit_sha256": "f" * 64},
            headers=headers,
        )
        assert failed.status_code == 409 and failed.headers["cache-control"] == "no-store"
        assert b"/private" not in failed.content
