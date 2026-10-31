from types import SimpleNamespace

from fastapi.testclient import TestClient
from test_saved_results import catalog_case as catalog_case
from test_saved_results import history_case as history_case
from test_saved_results import native_case as native_case

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


def test_P10_history_posts_strict_before_dispatch_and_never_get():
    import asyncio
    import json

    import httpx2
    from test_saved_results import SEL

    calls = []

    class Saved:
        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"operation": operation}

        async def close(self):
            pass

    pin = dict(node_uuid=CODE, bytes="2", sha256="a" * 64)
    state = dict(
        identity=dict(
            run_id="native",
            execution_id="ex",
            point_id="p",
            attempt="1",
            branch_id="branch",
            model_sha256="a" * 64,
            plan_sha256="b" * 64,
        ),
        state_id="s",
        state_sequence="1",
        manifest=dict(
            path="manifest.json",
            bytes="100",
            sha256="c" * 64,
            schema="qcl-negf.result-manifest.v1",
            media_type="application/json",
        ),
    )
    body = dict(
        **SEL,
        provider="native_nfs",
        point_id="p",
        retained=dict.fromkeys(
            ("native_result", "native_binding", "input_plan", "source_graph"), pin
        ),
        source_state=state,
        source_invocation={"source_epoch": "epoch"},
        reduction=dict(stage="scba", field="J", first_record="1", last_record="2"),
    )
    single = {k: v for k, v in body.items() if k != "run_uuid"}
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=[], reason="Unassessed")
    app = create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=Saved())

    async def pulse():
        while True:
            await asyncio.sleep(0.005)

    async def scenario():
        timer = asyncio.create_task(pulse())
        try:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                path = f"/api/v1/runs/{RUN}/saved-results/"
                h = {"Authorization": "Bearer " + TOKEN}
                assert (await client.post(path + "derive-history", json=single)).status_code == 401
                response = await client.post(path + "derive-history", json=single, headers=h)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                assert calls[-1][0] == "derive-history"
                pair = dict(left=body, right=body, comparability=assessment)
                assert (
                    await client.post(path + "compare-history", json=pair, headers=h)
                ).status_code == 200
                n = len(calls)
                for bad in (
                    {**single, "unknown": 1},
                    {**single, "attempt": True},
                    {**single, "reduction": {**single["reduction"], "last_record": "02"}},
                ):
                    r = await client.post(path + "derive-history", json=bad, headers=h)
                    assert r.status_code == 422 and r.headers["cache-control"] == "no-store"
                duplicate = json.dumps(single)[:-1] + ',"attempt":2}'
                for raw in (duplicate, " " * 65537, "[" * 70 + "0" + "]" * 70):
                    assert (
                        await client.post(path + "derive-history", content=raw, headers=h)
                    ).status_code == 422
                assert (
                    await client.post(path + "derive-history?x=1", json=single, headers=h)
                ).status_code == 422
                assert (await client.get(path + "derive-history", headers=h)).status_code == 405
                assert len(calls) == n
        finally:
            timer.cancel()
            try:
                await timer
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_P10_actual_asgi_public_derive_and_pair(history_case):
    import asyncio

    import httpx2
    from test_saved_results import history_selection

    c = history_case
    left, right = history_selection(c), history_selection(c, 1)
    assessment = dict.fromkeys(("model", "discretization", "protocol", "source"), "unknown")
    assessment.update(evidence=["NEW source/L fixture"], reason="Explicit author declaration")
    app = create_app(Settings(TOKEN, (CODE,)), SimpleNamespace(), saved_results=c.adapter)

    async def pulse():
        while True:
            await asyncio.sleep(0.005)

    async def scenario():
        timer = asyncio.create_task(pulse())
        try:
            async with (
                app.router.lifespan_context(app),
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app), base_url="http://test"
                ) as client,
            ):
                base = f"/api/v1/runs/{RUN}/saved-results/"
                h = {"Authorization": "Bearer " + TOKEN}
                single = {k: v for k, v in left.items() if k != "run_uuid"}
                reply = await client.post(base + "derive-history", json=single, headers=h)
                assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
                assert (
                    reply.json()["view"]["result"]["maximum"]["value"]["bits"] == "8000000000000000"
                )
                reply = await client.post(
                    base + "compare-history",
                    json=dict(left=left, right=right, comparability=assessment),
                    headers=h,
                )
                assert reply.status_code == 200 and reply.json()["view"]["differences"] is None
                assert reply.json()["assessment_origin"] == "author_declared"
        finally:
            timer.cancel()
            try:
                await timer
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
