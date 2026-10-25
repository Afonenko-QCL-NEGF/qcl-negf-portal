"""Card HTTP envelope/admission controls, no native profile or computation."""

import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from qcl_negf_api.api import create_app
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
AUTH = {"Authorization": f"Bearer {TOKEN}"}
C = "11111111-1111-4111-8111-111111111111"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
A = "66666666-6666-4666-8666-666666666666"
Q = "88888888-8888-4888-8888-888888888888"
RAW = (
    '{"schema":"qcl-negf-research-card-v1","title":"Test","goal":" Compare A/B ",'
    '"question":"Why?\\nNegative results remain."}'
)
RECEIPT = {
    "card_uuid": C,
    "version_uuid": C,
    "version": 1,
    "previous_version_uuid": None,
    "title": "Test",
    "bytes": len(RAW.encode()),
    "sha256": "a" * 64,
    "ctime": "2026-10-25T00:00:00+00:00",
}


@pytest.fixture
def source():
    calls = []
    result = {"status": "stored", "kind": "version", "receipt": RECEIPT}

    def call(name):
        def operation(*args, **kwargs):
            calls.append((name, args, kwargs))
            if name == "read_research_card_version":
                return (
                    RAW.encode()
                    if kwargs.get("raw")
                    else {"version": RECEIPT, "card": json.loads(RAW)}
                )
            if name == "get_research_card":
                return {"head": RECEIPT, "card": json.loads(RAW)}
            if name.startswith("list_"):
                return [RECEIPT]
            return dict(result)

        return operation

    names = [
        "create_research_card",
        "edit_research_card",
        "get_research_card",
        "list_research_cards",
        "list_research_card_versions",
        "read_research_card_version",
        "attach_research_card_run",
        "list_research_card_runs",
        "attach_research_card_report",
        "list_research_card_reports",
        "get_research_card_request",
    ]
    service = NS(**{name: call(name) for name in names})
    with TestClient(create_app(Settings(TOKEN, (R,)), service)) as client:
        yield NS(client=client, calls=calls, result=result, service=service)


def test_auth_known_store_replay_file_and_exact_bytes(source):
    c = source.client
    assert (
        c.post("/api/v1/research-cards", json={"request_uuid": Q, "card": RAW}).status_code == 401
    )
    assert not source.calls
    response = c.post("/api/v1/research-cards", headers=AUTH, json={"request_uuid": Q, "card": RAW})
    assert response.status_code == 201 and response.json()["receipt"] == RECEIPT
    assert response.headers["cache-control"] == "no-store"
    assert source.calls[-1] == ("create_research_card", (Q, RAW.encode()), {})
    source.result["status"] = "replayed"
    assert (
        c.post(
            "/api/v1/research-cards", headers=AUTH, json={"request_uuid": Q, "card": RAW}
        ).status_code
        == 200
    )
    file = c.get(f"/api/v1/research-cards/{C}/versions/{C}/file", headers=AUTH)
    assert file.content == RAW.encode() and file.headers["content-length"] == str(len(RAW.encode()))
    assert file.headers["content-disposition"] == 'attachment; filename="research-card.json"'


@pytest.mark.parametrize(
    "reason,status",
    [
        ("not_found", 404),
        ("version_conflict", 409),
        ("request_conflict", 409),
        ("invalid_binding", 422),
        ("unsupported_plan", 422),
    ],
)
def test_known_refusals_are_returned_without_unknown_headers(source, reason, status):
    source.result.clear()
    source.result.update(status="rejected", reason=reason)
    response = source.client.post(
        "/api/v1/research-cards", headers=AUTH, json={"request_uuid": Q, "card": RAW}
    )
    assert response.status_code == status and response.json() == {
        "status": "rejected",
        "reason": reason,
    }
    assert "x-qcl-operation-outcome" not in response.headers


@pytest.mark.parametrize(
    "body",
    [
        {"request_uuid": Q, "card": RAW, "extra": True},
        {"request_uuid": Q, "card": {}},
        {"request_uuid": "AAAAAAAA-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "card": RAW},
        {"request_uuid": Q, "card": RAW.replace("Test", "\\ud800")},
        {
            "request_uuid": Q,
            "card": RAW.replace('"title":"Test"', '"title":"Test","title":"Other"'),
        },
        {"request_uuid": Q, "card": RAW + " " * 65537},
        {"request_uuid": Q, "card": RAW.replace("research-card-v1", "other")},
    ],
)
def test_bad_content_refused_before_actor(source, body):
    response = source.client.post("/api/v1/research-cards", headers=AUTH, json=body)
    assert response.status_code == 422 and source.calls == []


def test_duplicate_outer_fields_surrogates_and_byte_boundary(source):
    c = source.client
    for content in [
        '{"request_uuid":"' + Q + '","request_uuid":"' + Q + '","card":' + json.dumps(RAW) + "}",
        '{"request_uuid":"' + Q + '","card":"\\ud800"}',
    ]:
        assert (
            c.post(
                "/api/v1/research-cards",
                headers={**AUTH, "Content-Type": "application/json"},
                content=content,
            ).status_code
            == 422
        )
    assert source.calls == []
    assert (
        c.post(
            "/api/v1/research-cards",
            headers=AUTH,
            json={"request_uuid": Q, "card": RAW + " " * (65536 - len(RAW))},
        ).status_code
        == 201
    )


def test_locators_pagination_cap_readback_and_precise_attach_args(source):
    c = source.client
    for path in [
        "/research-cards?limit=101",
        "/research-cards?offset=-1",
        "/research-cards?limit=true",
        f"/research-cards/{C}/versions/AAAAAAAA-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ]:
        assert c.get("/api/v1" + path, headers=AUTH).status_code == 422
    assert source.calls == []
    assert c.get("/api/v1/research-cards?limit=1&offset=3", headers=AUTH).json() == {
        "cards": [RECEIPT]
    }
    assert source.calls[-1] == ("list_research_cards", (), {"limit": 1, "offset": 3})
    assert (
        c.post(
            f"/api/v1/research-cards/{C}/versions",
            headers=AUTH,
            json={"request_uuid": Q, "base_version_uuid": C, "card": RAW},
        ).status_code
        == 201
    )
    assert source.calls[-1] == ("edit_research_card", (C, Q, C, RAW.encode()), {})
    assert (
        c.post(
            f"/api/v1/research-cards/{C}/runs",
            headers=AUTH,
            json={
                "request_uuid": Q,
                "card_version_uuid": V,
                "run_uuid": R,
                "plan_fingerprint": "a" * 64,
            },
        ).status_code
        == 201
    )
    assert source.calls[-1] == ("attach_research_card_run", (C, Q, V, R, "a" * 64), {})
    assert (
        c.post(
            f"/api/v1/research-cards/{C}/analyses",
            headers=AUTH,
            json={
                "request_uuid": Q,
                "card_version_uuid": V,
                "anchor_run_uuid": R,
                "report_uuid": A,
            },
        ).status_code
        == 201
    )
    assert source.calls[-1] == ("attach_research_card_report", (C, Q, V, R, A), {})
    source.result.clear()
    source.result.update(status="not_found")
    assert c.get(f"/api/v1/research-card-requests/{Q}", headers=AUTH).json() == {
        "status": "not_found"
    }
    source.service.get_research_card = lambda *args: {"oversized": "x" * 1048576}
    assert c.get(f"/api/v1/research-cards/{C}", headers=AUTH).status_code == 422


def test_untrusted_backend_validation_detail_is_safe(source):
    def refuse(*args):
        raise ValueError("unsafe \\ud800 marker")

    source.service.get_research_card = refuse
    response = source.client.get(f"/api/v1/research-cards/{C}", headers=AUTH)
    assert (
        response.status_code == 422
        and response.json()["detail"] == "Research-card validation was refused"
    )


def test_actor_unknown_diagnostic_is_preserved_on_card_write(source):
    from qcl_negf_api.service import ActorOutcomeUnknown, ActorReceipt

    receipt = ActorReceipt(Q, "create_research_card", "unknown", "ready")

    def uncertain(*args):
        raise ActorOutcomeUnknown("mutation_outcome_unknown", receipt, backend_status=422)

    source.service.create_research_card = uncertain
    response = source.client.post(
        "/api/v1/research-cards", headers=AUTH, json={"request_uuid": Q, "card": RAW}
    )
    assert response.status_code == 422
    assert response.headers["x-qcl-operation-id"] == Q
    assert response.headers["x-qcl-operation-outcome"] == "unknown"
    assert response.headers["x-qcl-auto-retry"] == "false"
    assert "Do not submit or repeat" in response.json()["detail"]
