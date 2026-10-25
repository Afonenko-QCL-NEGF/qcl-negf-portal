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


def test_nfs_sources_selector_transport_and_safe_errors():
    calls = []

    class Saved:
        async def read(self, op, selection, query, disconnect=None):
            calls.append((op, selection, query))
            if query.get("source_sha256") == "f" * 64:
                raise SavedResultError("source_identity_mismatch")
            return {"selection": selection}

        async def close(self):
            pass

    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())
    ) as client:
        base = f"/api/v1/runs/{RUN}/saved-results/"
        headers = {"Authorization": "Bearer " + TOKEN}
        exact = dict(execution_id="ex", attempt=2, calcjob_uuid=JOB)
        assert client.get(base + "sources", params=exact).status_code == 401
        response = client.get(base + "sources", params=exact, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert calls[-1][0] == "sources" and calls[-1][2] == {}
        assert (
            client.get(
                base + "sources", params={**exact, "provider": "nfs_registered"}, headers=headers
            ).status_code
            == 422
        )
        nfs = dict(
            provider="nfs_registered",
            source_uuid="44444444-4444-4444-8444-444444444444",
            source_sha256="a" * 64,
        )
        assert (
            client.get(base + "brief", params={**exact, **nfs}, headers=headers).status_code == 200
        )
        assert calls[-1][2] == nfs
        failed = client.get(
            base + "brief", params={**exact, **nfs, "source_sha256": "f" * 64}, headers=headers
        )
        assert failed.status_code == 409 and failed.headers["cache-control"] == "no-store"
        assert b"/run/" not in failed.content
        duplicate = list(exact.items()) + list(nfs.items()) + [("source_uuid", JOB)]
        assert client.get(base + "brief", params=duplicate, headers=headers).status_code == 422
        body = {
            **exact,
            **nfs,
            "point_id": "p0",
            "source_commit_sha256": "b" * 64,
            "metadata_sha256": "c" * 64,
            "view_id": "density",
            "selection": [0],
        }
        assert client.post(base + "slice", json=body, headers=headers).status_code == 200
        for malformed in (
            {**body, "provider": "aiida_retrieved"},
            {k: v for k, v in body.items() if k != "source_sha256"},
            {**body, "path": "/private/secret"},
        ):
            assert client.post(base + "slice", json=malformed, headers=headers).status_code == 422
