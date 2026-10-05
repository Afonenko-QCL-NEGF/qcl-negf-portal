import hashlib
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import anyio
import pytest
from starlette.requests import ClientDisconnect

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
CODE = "10dd2a4c-031b-425c-90b8-2f7ea564d313"
RUN = "e7f5313d-22fd-49fa-9be5-85fbe29e1974"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("kind", ["artifact", "export"])
async def test_asgi_disconnect_closes_reader_and_releases_export_lease(
    kind, spec_version, monkeypatch,
):
    payload = b"a" * (2 * 65536)
    digest = hashlib.sha256(payload).hexdigest()
    opened = []

    @contextmanager
    def open_artifact(run, execution, path):
        assert (run, execution) == (RUN, "point_1")
        stream = BytesIO(payload if path == "raw.bin" else b"data")
        opened.append(stream)
        try:
            yield stream
        finally:
            stream.close()

    service = SimpleNamespace(
        list_artifacts=lambda run: [
            {"execution_id": "point_1", "path": "result/native.bin", "size": 4},
            {"execution_id": "point_1", "path": "raw.bin", "size": len(payload)},
        ],
        open_artifact=open_artifact,
        get_run=lambda run: {"results": {}},
        get_export_plan=lambda run, execution: {"plan": b"{}", "source": "aiida.input.plan"},
    )

    def export_snapshot(root, destination, **kwargs):
        assert (root / "native.bin").read_bytes() == b"data"
        (destination / f"{digest}.tar.xz").write_bytes(payload)
        return {
            "schema": "qcl-negf.science-export.v3", "contract_set": "qcl-negf.results.v1",
            "transport_schema": "qcl-negf.export-archive.v1", "profile": "science",
            "snapshot_identity": "b" * 64, "sha256": digest, "bytes": len(payload),
            "filename": "small-science.tar.xz", "archive": f"{digest}.tar.xz",
        }

    monkeypatch.setattr("qcl_negf_results.export.export_snapshot", export_snapshot)
    app = create_app(Settings(TOKEN, (CODE,)), service)
    # Keep the real response alive after cancellation. Otherwise CPython's GC
    # can hide a leaked generator by closing it after the response is dropped.
    responses = []
    template = (
        "/api/v1/runs/{run_uuid}/artifact" if kind == "artifact"
        else "/api/v1/exports/{export_id}/download"
    )
    route = next(route for route in app.routes if route.path == template and "GET" in route.methods)
    original = route.dependant.call

    def capture_response(**kwargs):
        response = original(**kwargs)
        responses.append(response)
        return response

    monkeypatch.setattr(route.dependant, "call", capture_response)
    async with app.router.lifespan_context(app):
        identifier = None
        if kind == "export":
            entry = app.state.exports.create(RUN, "point_1", "science")
            identifier = entry["export_id"]
            with anyio.fail_after(3):
                while app.state.exports.get(identifier)["state"] == "preparing":
                    await anyio.sleep(0.001)
            assert app.state.exports.get(identifier)["state"] == "ready"
            path = f"/api/v1/exports/{identifier}/download"
            query = b""
        else:
            path = f"/api/v1/runs/{RUN}/artifact"
            query = b"execution_id=point_1&path=raw.bin"
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1", "method": "GET", "scheme": "https",
            "path": path, "raw_path": path.encode(), "query_string": query,
            "root_path": "", "server": ("testserver", 443), "client": ("127.0.0.1", 1000),
            "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
        }
        body_started = anyio.Event()
        disconnect_received = anyio.Event()
        received_request = False
        bodies = []

        async def receive():
            nonlocal received_request
            if not received_request:
                received_request = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await body_started.wait()
            disconnect_received.set()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                bodies.append(message["body"])
                body_started.set()
                if spec_version == "2.4":
                    raise OSError("Client closed the connection")
                # BaseHTTPMiddleware sends through its own response, whose send
                # is not cancelled by the inner response's disconnect listener.
                # Let it finish sending this chunk once the client disconnects.
                await disconnect_received.wait()

        with anyio.fail_after(3):
            try:
                await app(scope, receive, send)
            except (ClientDisconnect, OSError):
                assert spec_version == "2.4"
        assert bodies and len(bodies[0]) == 65536
        assert len(responses) == 1
        if kind == "artifact":
            assert opened[-1].closed, "Repository reader must close before ASGI response exits"
        else:
            assert app.state.exports._entries[identifier]["readers"] == 0
