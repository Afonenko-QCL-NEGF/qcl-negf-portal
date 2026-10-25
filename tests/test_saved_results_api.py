from types import SimpleNamespace

from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.saved_results import SavedResultError
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
RUN = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
CODE = "33333333-3333-4333-8333-333333333333"


def test_saved_routes_auth_exact_selection_no_store():
    calls = []

    class Saved:
        async def read(self, op, selection, query, disconnect=None):
            calls.append((op, selection, query))
            if selection.get("attempt") is None:
                raise SavedResultError("selection_required")
            return {"provider_selection": selection}

        async def close(self):
            pass

    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())
    ) as client:
        base = f"/api/v1/runs/{RUN}/saved-results"
        for suffix in ("brief", "manifest"):
            assert client.get(base + "/" + suffix).status_code == 401
        assert client.post(base + "/slice", json={}).status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert client.get(base + "/brief", headers=headers).status_code == 422
        response = client.get(
            base + "/brief",
            params={"execution_id": "ex", "attempt": 2, "calcjob_uuid": JOB},
            headers=headers,
        )
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls[-1][1] == {
            "run_uuid": RUN,
            "execution_id": "ex",
            "attempt": 2,
            "calcjob_uuid": JOB,
        }
        response = client.post(
            base + "/slice",
            headers=headers,
            json={"execution_id": "ex", "attempt": 2, "calcjob_uuid": JOB, "path": "/private"},
        )
        assert response.status_code == 422
