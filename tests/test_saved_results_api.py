from types import SimpleNamespace

from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
RUN = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
CODE = "33333333-3333-4333-8333-333333333333"


def test_R1_page_routes_strict_selectors_before_dispatch():
    import asyncio

    import httpx2

    calls = []

    class Saved:
        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"operation": operation}

        async def close(self):
            pass

    exact = dict(execution_id="exec", attempt=2, calcjob_uuid=JOB)
    headers = {"Authorization": "Bearer " + TOKEN}
    app = create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())

    async def heartbeat():
        while True:
            await asyncio.sleep(0.005)

    async def scenario():
        # Python3.14/AnyIO in this SOURCE/L host needs a timer to wake thread callbacks.
        # Auth/router/lifespan remain the actual application; no dependency is mocked.
        pulse = asyncio.create_task(heartbeat())
        try:
            await requests()
        finally:
            pulse.cancel()
            try:
                await pulse
            except asyncio.CancelledError:
                pass

    async def requests():
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
            ) as client,
        ):
            base = f"/api/v1/runs/{RUN}/saved-results/"
            response = await client.get(
                base + "sources", params={**exact, "limit": 3}, headers=headers
            )
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert calls[-1][2] == {"limit": 3}
            accepted = len(calls)
            for bad in (
                {**exact, "limit": 0},
                {**exact, "limit": 9},
                {**exact, "limit": "01"},
                {**exact, "limit": 2, "cursor": "x"},
                {**exact, "point_id": "first"},
                {**exact, "attempt": 9007199254740992},
                {**exact, "path": "/private"},
            ):
                assert (
                    await client.get(base + "sources", params=bad, headers=headers)
                ).status_code == 422
            assert (
                await client.get(base + "brief", params=exact, headers=headers)
            ).status_code == 422
            assert (
                await client.request(
                    "GET", base + "sources", params=exact, content=b"{}", headers=headers
                )
            ).status_code == 422
            assert (
                await client.get(
                    base + "sources", params=list(exact.items()) + [("attempt", 2)], headers=headers
                )
            ).status_code == 422
            assert (await client.get(base + "sources", params=exact)).status_code == 401
            assert len(calls) == accepted

    asyncio.run(scenario())


def test_native5_exact_retained_selectors_and_full_state_reach_existing_routes():
    calls = []

    class Saved:
        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"schema": "qcl-negf.portal-selected-reader.v2", "operation": operation}

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
    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())
    ) as client:
        base = f"/api/v1/runs/{RUN}/saved-results/"
        response = client.get(base + "manifest", params=exact, headers=headers)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        state = {"identity": {"attempt": "18446744073709551615"}, "manifest": {"sha256": "a" * 64}}
        body = {
            **exact,
            "source_state": state,
            "view_id": "density",
            "selection": [{"start": 0, "stop": 2, "step": 1}],
        }
        response = client.post(base + "slice", json=body, headers=headers)
        assert response.status_code == 200 and calls[-1][2]["source_state"] == state
        for bad in ({**body, "path": "/private"}, {**body, "source_commit_sha256": "f" * 64}):
            assert client.post(base + "slice", json=bad, headers=headers).status_code == 422
        duplicate = list(exact.items()) + [("native_binding_uuid", JOB)]
        assert client.get(base + "manifest", params=duplicate, headers=headers).status_code == 422


def test_exact_point_refresh_without_manual_owner_pins():
    calls = []

    class Saved:
        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"schema": "qcl-negf.portal-point-source.v1", "status": "available"}

        async def close(self):
            pass

    exact = dict(execution_id="exec", attempt=2, calcjob_uuid=JOB, point_id="point")
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(
        create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())
    ) as client:
        path = f"/api/v1/runs/{RUN}/saved-results/point-source"
        response = client.get(path, params=exact, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert calls == [
            (
                "point-source",
                dict(run_uuid=RUN, execution_id="exec", attempt=2, calcjob_uuid=JOB),
                {"point_id": "point"},
            )
        ]

        accepted_calls = len(calls)
        assert client.get(path, params=exact).status_code == 401
        assert (
            client.get(path, params=exact, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        for key in exact:
            assert (
                client.get(
                    path, params={k: v for k, v in exact.items() if k != key}, headers=headers
                ).status_code
                == 422
            )
        for bad in (
            {**exact, "binding_uuid": CODE},
            {**exact, "state_manifest_sha256": "a" * 64},
            {**exact, "attempt": 9007199254740992},
            {**exact, "attempt": 0},
            {**exact, "attempt": "2.5"},
            {**exact, "point_id": "../point"},
            {**exact, "calcjob_uuid": "job"},
            {**exact, "execution_id": "/private"},
        ):
            assert client.get(path, params=bad, headers=headers).status_code == 422
        assert (
            client.get(
                path, params=list(exact.items()) + [("point_id", "foreign")], headers=headers
            ).status_code
            == 422
        )
        assert (
            client.request("GET", path, params=exact, content=b"{}", headers=headers).status_code
            == 422
        )
        assert len(calls) == accepted_calls
