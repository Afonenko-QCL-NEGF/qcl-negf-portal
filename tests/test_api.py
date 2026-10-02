import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
CODE = "10dd2a4c-031b-425c-90b8-2f7ea564d313"
RUN = "e7f5313d-22fd-49fa-9be5-85fbe29e1974"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def service():
    record = dict(
        uuid=RUN,
        pk=1,
        label="Example",
        process_state="waiting",
        exit_status=None,
        is_finished_ok=False,
        ctime="2026-01-01T00:00:00Z",
        mtime="2026-01-01T00:00:00Z",
    )
    calls = []

    def submit(plan, code_uuid, resources, label="", scratch_root=None):
        if json.loads(plan).get("invalid"):
            raise ValueError("Invalid frozen plan")
        calls.append((plan, code_uuid, resources, label, scratch_root))
        return record

    def get_run(uuid):
        if uuid != RUN:
            raise LookupError(uuid)
        return {**record, "results": {"point_1": {"status": "not_converged"}}}

    @contextmanager
    def open_artifact(uuid, execution_id, path):
        assert (uuid, execution_id, path) == (RUN, "point_1", "result.json")
        yield BytesIO(b'{"status":"not_converged"}')

    return SimpleNamespace(
        calls=calls,
        list_runs=lambda **kwargs: [record],
        submit_plan=submit,
        get_run=get_run,
        get_export_plan=lambda run, execution: {
            "plan": b'{ "frozen": 1.234567890123456789 }\n', "source": "aiida.input.plan",
        },
        get_run_report=lambda uuid: [
            {"level": "REPORT", "message": "Calculation submitted", "time": "2026-01-01T00:00:00Z"}
        ],
        kill_run=lambda uuid: {**record, "uuid": uuid},
        list_artifacts=lambda uuid: [
            {"execution_id": "point_1", "path": "result.json", "size": 26}
        ],
        open_artifact=open_artifact,
    )


@pytest.fixture
def client(service):
    with TestClient(create_app(Settings(TOKEN, (CODE,)), service)) as client:
        yield client


def test_authentication_and_private_schema(client):
    assert client.get("/healthz").status_code == 200
    for path in ("/api/v1/runs", "/api/v1/config", "/api/v1/openapi.json"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, headers=AUTH).status_code == 200
    response = client.get("/api/v1/config", headers=AUTH)
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_browser_is_served_from_the_python_package(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "QCL-NEGF" in response.text
    assert "/assets/" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_submission_has_one_owner_and_passes_exact_resources(client, service):
    payload = {
        "plan": '{"schema":"qcl-negf-scientific-plan-v1"}',
        "code_uuid": CODE,
        "label": "Physics",
        "resources": {"num_cores_per_mpiproc": 8},
    }
    response = client.post("/api/v1/runs", headers=AUTH, json=payload)
    assert response.status_code == 202, response.text
    assert response.json()["uuid"] == RUN
    assert len(service.calls) == 1
    assert service.calls[0][2] == {
        "num_machines": 1,
        "num_mpiprocs_per_machine": 1,
        "num_cores_per_mpiproc": 8,
        "max_wallclock_seconds": 3600,
        "max_memory_kb": 4194304,
    }


def test_frozen_plan_numerical_text_is_not_reencoded(client, service):
    source = '{\n "energy": 1.234567890123456789, "units": "μeV"\n}\n'
    response = client.post("/api/v1/runs", headers=AUTH, json={"plan": source, "code_uuid": CODE})
    assert response.status_code == 202
    assert service.calls[0][0] == source


@pytest.mark.parametrize(
    "change,status",
    [
        ({"code_uuid": RUN}, 403),
        ({"resources": {"num_machines": 3}}, 422),
        ({"resources": {"num_machines": "2"}}, 422),
        ({"resources": {"shell": "danger"}}, 422),
        ({"plan": '{"invalid":true}'}, 422),
        ({"command": "uncontrolled"}, 422),
    ],
)
def test_submission_rejections_do_not_start_work(client, service, change, status):
    payload = {"plan": "{}", "code_uuid": CODE, **change}
    assert client.post("/api/v1/runs", headers=AUTH, json=payload).status_code == status
    assert not service.calls


def test_state_scientific_results_report_and_cancellation(client):
    detail = client.get(f"/api/v1/runs/{RUN}", headers=AUTH).json()
    assert detail["process_state"] == "waiting"
    assert detail["results"]["point_1"]["status"] == "not_converged"
    assert client.get(f"/api/v1/runs/{RUN}/report", headers=AUTH).json()["entries"]
    assert (
        client.post(f"/api/v1/runs/{RUN}/kill", headers=AUTH).json()["process_state"] == "waiting"
    )
    assert client.get(f"/api/v1/runs/{CODE}", headers=AUTH).status_code == 404
    assert client.get("/api/v1/runs/not-a-uuid", headers=AUTH).status_code == 422


def test_bounded_listing(client):
    assert client.get("/api/v1/runs?limit=101", headers=AUTH).status_code == 422
    assert client.get("/api/v1/runs?offset=-1", headers=AUTH).status_code == 422


def test_download_exact_artifact_and_reject_traversal(client):
    url = f"/api/v1/runs/{RUN}/artifact"
    params = {"execution_id": "point_1", "path": "result.json"}
    response = client.get(url, params=params, headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "not_converged"}
    assert response.headers["content-disposition"] == 'attachment; filename="result.json"'
    for path in ("../result.json", "/etc/passwd", "sub/../../file", "sub\\file"):
        assert client.get(url, params={**params, "path": path}, headers=AUTH).status_code == 422
    assert client.get(url, params={**params, "path": "missing"}, headers=AUTH).status_code == 404


def test_authenticated_artifact_range_and_if_range(client):
    url = f"/api/v1/runs/{RUN}/artifact"
    params = {"execution_id": "point_1", "path": "result.json"}
    response = client.get(url, params=params, headers={**AUTH, "Range": "bytes=2-6"})
    assert response.status_code == 206
    assert response.content == b'statu'
    assert response.headers["content-range"] == "bytes 2-6/26"
    assert response.headers["accept-ranges"] == "bytes"
    assert client.get(url, params=params, headers={"Range": "bytes=2-6"}).status_code == 401
    for requested in ("bytes=26-", "bytes=4-2", "bytes=0-1,4-5", "nonsense"):
        response = client.get(url, params=params, headers={**AUTH, "Range": requested})
        assert response.status_code == 416
        assert response.headers["content-range"] == "bytes */26"


def test_native_artifact_capability_cannot_open_other_artifacts(client):
    params = {"execution_id": "point_1", "path": "result.json"}
    base = f"/api/v1/runs/{RUN}/artifact"
    response = client.post(f"{base}/authorize", params=params, headers=AUTH)
    assert response.status_code == 200
    assert TOKEN not in response.text and TOKEN not in response.headers["set-cookie"]
    assert client.get(base, params=params).status_code == 200
    assert client.get(base, params={**params, "path": "missing"}).status_code == 401
    assert client.get("/api/v1/runs").status_code == 401


def test_export_request_rejects_missing_execution_and_unrecognized_profile(client):
    url = f"/api/v1/runs/{RUN}/exports"
    assert client.post(url, json={"execution_id": "point_1"}).status_code == 401
    response = client.post(url, headers=AUTH, json={"execution_id": "missing"})
    assert response.status_code == 404
    response = client.post(
        url, headers=AUTH, json={"execution_id": "point_1", "profile": "reduced"}
    )
    assert response.status_code == 422


def test_native_export_capability_is_scoped_and_download_resumes(service, monkeypatch):
    import hashlib
    import time

    from qcl_negf_results import export

    payload = b"complete immutable archive"
    digest = hashlib.sha256(payload).hexdigest()
    service.list_artifacts = lambda uuid: [
        {"execution_id": "point_1", "path": "result/native.bin", "size": 4}
    ]

    @contextmanager
    def open_artifact(uuid, execution_id, path):
        assert (uuid, execution_id, path) == (RUN, "point_1", "result/native.bin")
        yield BytesIO(b"data")

    service.open_artifact = open_artifact

    def export_snapshot(root, destination, **kwargs):
        assert (root / "native.bin").read_bytes() == b"data"
        assert kwargs["profile"] == "science"
        assert kwargs["job_status"] == "running"
        kwargs["progress"]({"phase": "compressing", "completed_bytes": 4, "total_bytes": 4})
        (destination / f"{digest}.tar.xz").write_bytes(payload)
        return {
            "schema": "qcl-negf.science-export.v3", "contract_set": "qcl-negf.results.v1",
            "id": "point_1", "profile": "science", "snapshot_identity": "b" * 64,
            "sha256": digest, "bytes": len(payload), "archive": f"{digest}.tar.xz",
            "filename": "point_1-science-bbbbbbbbbbbb.tar.xz", "complete": False,
            "transport_schema": "qcl-negf.export-archive.v1",
        }

    monkeypatch.setattr(export, "export_snapshot", export_snapshot)
    settings = Settings(TOKEN, (CODE,), max_download_bytes=10)
    with TestClient(create_app(settings, service), base_url="https://testserver") as client:
        response = client.post(
            f"/api/v1/runs/{RUN}/exports", headers=AUTH, json={"execution_id": "point_1"}
        )
        assert response.status_code == 202, response.text
        identifier = response.json()["export_id"]
        status = f"/api/v1/exports/{identifier}"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = client.get(status, headers=AUTH).json()
            if result["state"] in {"ready", "failed"}:
                break
            time.sleep(0.01)
        assert result["state"] == "ready", result
        assert result["receipt"]["sha256"] == digest
        assert "parts" not in result["receipt"]
        url = f"{status}/download"
        assert client.get(url).status_code == 401
        response = client.post(f"{status}/authorize", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["url"] == url
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" in cookie
        assert f"Path={url}" in cookie
        assert TOKEN not in cookie and TOKEN not in response.text
        assert client.get("/api/v1/config").status_code == 401
        response = client.get(url, headers={"Range": "bytes=9-"})
        assert response.status_code == 206
        assert response.content == payload[9:]
        assert response.headers["etag"] == f'"{digest}"'
        assert response.headers["content-range"] == f"bytes 9-25/{len(payload)}"
        response = client.get(url, headers={"Range": "bytes=9-", "If-Range": '"old"'})
        assert response.status_code == 200
        assert response.content == payload
        response = client.head(url)
        assert response.status_code == 200 and response.content == b""
        assert response.headers["content-length"] == str(len(payload))


def test_request_and_download_limits(service):
    settings = Settings(TOKEN, (CODE,), max_body_bytes=100, max_download_bytes=10)
    with TestClient(create_app(settings, service)) as client:
        response = client.post("/api/v1/runs", content=b" " * 101, headers=AUTH)
        assert response.status_code == 413
        response = client.get(
            f"/api/v1/runs/{RUN}/artifact",
            headers=AUTH,
            params={"execution_id": "point_1", "path": "result.json"},
        )
        assert response.status_code == 413


def test_broker_unavailable_is_not_reported_as_success(client, service):
    def failed(uuid):
        raise RuntimeError("private internal broker address")

    service.kill_run = failed
    response = client.post(f"/api/v1/runs/{RUN}/kill", headers=AUTH)
    assert response.status_code == 503
    assert "private internal" not in response.text


def test_configuration_fails_closed():
    with pytest.raises(ValueError, match="token"):
        Settings("short", (CODE,))
    with pytest.raises(ValueError, match="approved"):
        Settings(TOKEN, ())
    with pytest.raises(ValueError, match="positive"):
        Settings(TOKEN, (CODE,), max_download_bytes=0)


def test_configured_defaults_fit_the_advertised_resource_budget(service):
    settings = Settings(
        TOKEN, (CODE,), default_memory_kb=1024, max_memory_kb=2048, max_wallclock_seconds=600
    )
    with TestClient(create_app(settings, service)) as client:
        configuration = client.get("/api/v1/config", headers=AUTH).json()
        assert configuration["defaults"]["max_memory_kb"] == 1024
        assert configuration["defaults"]["max_wallclock_seconds"] == 600
        response = client.post("/api/v1/runs", headers=AUTH, json={"plan": "{}", "code_uuid": CODE})
        assert response.status_code == 202
        assert service.calls[0][2] == configuration["defaults"]


def test_actual_aiida_plugin_rejects_invalid_plan_before_provenance_creation():
    from aiida_qcl_negf import service

    with TestClient(create_app(Settings(TOKEN, (CODE,)), service)) as client:
        response = client.post("/api/v1/runs", headers=AUTH, json={"plan": "{}", "code_uuid": CODE})
        assert response.status_code == 422
        assert response.json()["detail"] == "unsupported scientific plan"


def test_worker_scratch_path_is_a_trusted_site_setting(service):
    settings = Settings(TOKEN, (CODE,), scratch_root="/scratch/qcl-negf")
    with TestClient(create_app(settings, service)) as client:
        response = client.post("/api/v1/runs", headers=AUTH, json={"plan": "{}", "code_uuid": CODE})
        assert response.status_code == 202
        assert service.calls[0][4] == "/scratch/qcl-negf"
        response = client.post(
            "/api/v1/runs",
            headers=AUTH,
            json={"plan": "{}", "code_uuid": CODE, "scratch_root": "/other"},
        )
        assert response.status_code == 422
        assert len(service.calls) == 1


@pytest.mark.parametrize(
    "path", ["scratch", "/scratch/../other", "/scratch//nested", "/scratch\x00bad"]
)
def test_scratch_paths_must_be_normalized_and_absolute(path):
    with pytest.raises(ValueError, match="Scratch root"):
        Settings(TOKEN, (CODE,), scratch_root=path)
