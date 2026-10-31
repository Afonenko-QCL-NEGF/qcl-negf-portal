"""U1 exact public-source adapter controls; no solver or native NFS claim."""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_saved_nfs import BINDING, COMPUTER
from test_saved_results import SEL

from qcl_negf_api import live_nfs as live
from qcl_negf_api.api import create_app
from qcl_negf_api.saved_results import SavedResultError, SavedResults
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
KEY = "a" * 64
QUERY = dict(mode="retained", anchor_key=KEY)


def refusal(status="unavailable"):
    return dict(
        schema="qcl-negf.performance-source.v2",
        status=status,
        stage="origin_qualification" if status == "unavailable" else "anchor_selection",
        reason="retained_performance_source_missing"
        if status == "unavailable"
        else "scientific_anchor_pending",
        provider_selection=SEL,
        candidates=[],
        integrity=dict(
            origin="not_qualified",
            native_contexts="not_reverified",
            current_access="not_checked",
            scientific_acceptance="not_assessed",
        ),
    )


def test_http_exact_mode_anchor_auth_and_no_store():
    calls = []

    class Adapter:
        async def close(self):
            pass

        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"status": "unavailable"}

    base = f"/api/v1/runs/{SEL['run_uuid']}/live-results/performance"
    body = {k: v for k, v in SEL.items() if k != "run_uuid"} | QUERY
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(
        create_app(Settings(TOKEN, (COMPUTER,)), SimpleNamespace(), saved_results=Adapter())
    ) as c:
        assert c.post(base, json=body).status_code == 401
        answer = c.post(base, json=body, headers=headers)
        assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
        assert calls == [("performance", SEL, QUERY)]
        for bad in (
            {k: v for k, v in body.items() if k != "anchor_key"},
            body | {"anchor_key": None},
            body | {"anchor_key": "A" * 64},
            body | {"mode": "latest"},
            body | {"path": "/tmp"},
            body | {"attempt": True},
            body | {"attempt": 2**53},
            body | {"execution_id": "../ex"},
        ):
            assert c.post(base, json=bad, headers=headers).status_code == 422
        assert c.post(base + "?mode=current", json=body, headers=headers).status_code == 422


@pytest.mark.parametrize("status", ["unavailable", "staging"])
def test_unavailable_whole_owner_dto_and_opaque_runtime(status):
    calls = []
    dto = refusal(status)

    def getter(*a, **kw):
        calls.append((a, kw))
        return dto

    adapter = SavedResults(
        SimpleNamespace(get_performance_source=getter),
        SimpleNamespace(saved_results_enabled=True),
        binding=BINDING,
    )
    try:
        answer = asyncio.run(adapter.read("performance", SEL, QUERY))
        assert calls == [
            (
                (SEL["run_uuid"], SEL["execution_id"]),
                dict(attempt=SEL["attempt"], calcjob_uuid=SEL["calcjob_uuid"], **QUERY),
            )
        ]
        assert answer == dict(
            schema="qcl-negf.portal-performance.v2",
            operation="performance",
            status=status,
            provider_selection=SEL,
            mode="retained",
            anchor_key=KEY,
            source_metadata=dto,
            access=None,
            view=None,
            owner_runtime=dict(
                scope="aiida_public_source_getters",
                status="not_measured",
                actual_io_bytes=None,
                heap_bytes=None,
                peak_rss_bytes=None,
                reason="not_reported_by_public_source_getters",
            ),
        )
    finally:
        asyncio.run(adapter.close())


def test_output_cap_before_final_full_encode(monkeypatch):
    monkeypatch.setattr(
        live, "_bytes", lambda value: pytest.fail("full encode over cap"), raising=False
    )
    with pytest.raises(SavedResultError, match="budget_exceeded"):
        live.checked_response({"x": "x" * (2 * 1024 * 1024)})


@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_pre_effect_stop(tmp_path, monkeypatch, stop):
    from qcl_negf_api import saved_results as saved

    event = threading.Event()
    if stop == "cancel":
        event.set()
    monkeypatch.setattr(saved.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned"))
    with pytest.raises(SavedResultError, match="budget_exceeded"):
        saved._worker(
            "performance",
            {},
            tmp_path,
            event,
            time.monotonic() - 1 if stop == "deadline" else time.monotonic() + 10,
        )
    assert not list(tmp_path.iterdir())


def test_obsolete_live_child_is_explicitly_unavailable(tmp_path):
    with pytest.raises(SavedResultError, match="capability_unavailable"):
        live.read_request({"operation": "live-source"}, tmp_path)


def test_actual_saved_future_cancel_and_unknown_actor_outcome_quarantine():
    from qcl_negf_api.service import ActorReceipt, ActorUnavailable

    entered = threading.Event()
    release = threading.Event()

    def getter(*a, **kw):
        entered.set()
        release.wait(2)
        raise ActorUnavailable(
            "operation_deadline", ActorReceipt("u1", "get_performance_source", "unknown", "ready")
        )

    adapter = SavedResults(
        SimpleNamespace(get_performance_source=getter),
        SimpleNamespace(saved_results_enabled=True),
        binding=BINDING,
        cleanup_seconds=0.01,
    )

    async def run():
        task = asyncio.create_task(adapter.read("performance", SEL, QUERY))
        while not entered.is_set():
            await asyncio.sleep(0.002)
        with pytest.raises(SavedResultError, match="busy"):
            await adapter.read("performance", SEL, QUERY)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert adapter.closed and adapter.busy and adapter.active is not None
        release.set()
        for _ in range(200):
            if adapter.active is None:
                break
            await asyncio.sleep(0.002)
        assert adapter.active is None and adapter.lease_cleanup_unconfirmed and adapter.busy
        with pytest.raises(SavedResultError, match="stopping"):
            await adapter.read("performance", SEL, QUERY)

    try:
        asyncio.run(run())
    finally:
        release.set()
        asyncio.run(adapter.close())


def test_body_cap_matches_actual_json_response():
    from starlette.responses import JSONResponse

    value = {
        "source_metadata": {"unicode": 'Я😀\n"\\'},
        "metric": {"decimal": "18446744073709551615"},
    }
    assert live.checked_response(value) is value
    assert live._bytes(value) == JSONResponse(value).body


@pytest.mark.parametrize(
    "error,status",
    [
        ("source_changed", 409),
        ("identity_mismatch", 409),
        ("busy", 409),
        ("budget_exceeded", 422),
        ("unavailable_storage", 503),
        ("stopping", 503),
    ],
)
def test_http_known_failure_has_no_partial_view(error, status):
    class Adapter:
        async def close(self):
            pass

        async def read(self, *a, **kw):
            raise SavedResultError(error)

    base = f"/api/v1/runs/{SEL['run_uuid']}/live-results/performance"
    body = {k: v for k, v in SEL.items() if k != "run_uuid"} | QUERY
    with TestClient(
        create_app(Settings(TOKEN, (COMPUTER,)), SimpleNamespace(), saved_results=Adapter())
    ) as c:
        answer = c.post(base, json=body, headers={"Authorization": "Bearer " + TOKEN})
        assert answer.status_code == status and answer.headers["cache-control"] == "no-store"
        assert set(answer.json()) == {"detail"} and answer.json()["detail"]["code"] == error


def test_nofollow_read_does_not_treat_access_time_as_source_change(tmp_path):
    import os

    p = tmp_path / "index.json"
    p.write_bytes(b"{}")
    os.utime(p, (1, time.time()))
    assert live._read(tmp_path, {"path": "index.json"}, 65536) == b"{}"
    p.unlink()
    p.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SavedResultError, match="unavailable_storage"):
        live._read(tmp_path, {"path": "index.json"}, 65536)


def test_http_rejects_noncanonical_run_uuid_before_adapter():
    class Adapter:
        async def close(self):
            pass

        async def read(self, *a, **kw):
            return {"status": "unexpected"}

    path = f"/api/v1/runs/{SEL['run_uuid'].replace('-', '')}/live-results/performance"
    body = {k: v for k, v in SEL.items() if k != "run_uuid"} | QUERY
    with TestClient(
        create_app(Settings(TOKEN, (COMPUTER,)), SimpleNamespace(), saved_results=Adapter())
    ) as c:
        assert (
            c.post(path, json=body, headers={"Authorization": "Bearer " + TOKEN}).status_code == 422
        )


def test_actual_child_deadline_kills_reaps_and_closes_pipes(tmp_path, monkeypatch):
    import sys

    from qcl_negf_api import saved_results as saved

    actual = saved.subprocess.Popen
    children = []

    def sleeper(command, **kwargs):
        child = actual([sys.executable, "-c", "import time; time.sleep(10)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(saved.subprocess, "Popen", sleeper)
    with pytest.raises(SavedResultError, match="budget_exceeded"):
        saved._worker("performance", {}, tmp_path, threading.Event(), time.monotonic() + 0.1)
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_response_wait_does_not_release_actual_future(monkeypatch):
    from qcl_negf_api import saved_results as saved

    entered, release = threading.Event(), threading.Event()

    def getter(*args, **kwargs):
        entered.set()
        release.wait(2)
        return refusal()

    monkeypatch.setattr(saved, "WALL", 0.03)
    adapter = SavedResults(
        SimpleNamespace(get_performance_source=getter),
        SimpleNamespace(saved_results_enabled=True),
        binding=BINDING,
        cleanup_seconds=0.01,
    )

    async def run():
        with pytest.raises(SavedResultError, match="budget_exceeded"):
            await adapter.read("performance", SEL, QUERY)
        assert entered.is_set() and adapter.active is not None and adapter.busy and adapter.closed
        release.set()
        for _ in range(200):
            if adapter.active is None:
                break
            await asyncio.sleep(0.002)
        assert adapter.active is None and adapter.closed
        with pytest.raises(SavedResultError, match="stopping"):
            await adapter.read("performance", SEL, QUERY)

    try:
        asyncio.run(run())
    finally:
        release.set()
        asyncio.run(adapter.close())


def test_probe_file_close_uncertainty_stops_consumer(tmp_path, monkeypatch):
    (tmp_path / "pointer.json").write_bytes(b"{}")
    actual = live.os.fdopen

    class UncertainClose:
        def __init__(self, *args):
            self.stream = actual(*args)

        def __enter__(self):
            return self.stream

        def __exit__(self, *args):
            self.stream.close()
            raise OSError("fixture close uncertainty")

    monkeypatch.setattr(live.os, "fdopen", UncertainClose)
    with pytest.raises(SavedResultError, match="stopping") as raised:
        live._read(tmp_path, {"path": "pointer.json"}, 65536)
    assert raised.value.cleanup_unconfirmed


def test_probe_access_time_is_not_an_immutable_byte_stamp(tmp_path, monkeypatch):
    (tmp_path / "pointer.json").write_bytes(b"{}")
    original = live.os.fstat
    calls = []

    def observed(fd):
        info = original(fd)
        calls.append(fd)
        return SimpleNamespace(
            **{
                name: getattr(info, name)
                for name in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
            },
            st_atime=len(calls),
        )

    monkeypatch.setattr(live.os, "fstat", observed)
    assert live._read(tmp_path, {"path": "pointer.json"}, 65536) == b"{}"


def test_plan_join_keeps_source_attempt_and_full_original_byte_capacity():
    """Local join discriminator; genuine original ownership is tested separately."""
    import hashlib
    import json
    from copy import deepcopy

    raw = b" \n{}\n" + b" " * (1024 * 1024 + 17)
    digest = hashlib.sha256(raw).hexdigest()
    ref = dict(
        path="plan.json",
        bytes=str(len(raw)),
        sha256=digest,
        schema="qcl-negf-scientific-plan-v2",
        media_type="application/json",
    )
    source = dict(
        execution_id=SEL["execution_id"],
        calcjob_uuid=SEL["calcjob_uuid"],
        attempt=3,
        plan_fingerprint=KEY,
    )
    dto = dict(
        entry=dict(
            runner_anchor_utf8=json.dumps(
                dict(scientific=dict(selected_state=dict(identity=dict(point_id="point"))))
            ),
            telemetry=dict(
                source_transport=dict(selection=source, frozen_plan=ref), canonical_plan_ref=ref
            ),
        ),
        origin=dict(
            input_nodes=dict(
                plan=dict(node_uuid=SEL["calcjob_uuid"], bytes=str(len(raw)), sha256=digest)
            )
        ),
    )
    calls = []

    def getter(*args, **kwargs):
        calls.append(kwargs)
        return dict(
            context=dict(run_uuid=SEL["run_uuid"], point_id="point", **source),
            frozen_plan=dict(bytes=raw, sha256=digest),
            snapshot=None,
            input_status=None,
            release_id=None,
        )

    adapter = SimpleNamespace(service=SimpleNamespace(get_model_help_input=getter))
    assert live._plan(adapter, dto, SEL, lambda: None) == (raw, ref)
    assert calls == [dict(attempt=3, calcjob_uuid=SEL["calcjob_uuid"])]
    bad = deepcopy(dto)
    bad["origin"]["input_nodes"]["plan"]["bytes"] = str(len(raw) - 1)
    with pytest.raises(SavedResultError, match="identity_mismatch"):
        live._plan(adapter, bad, SEL, lambda: None)


@pytest.mark.parametrize(
    "mode",
    [
        [],
        {},
        ["retained"],
        {"mode": "retained"},
        [["current"]],
        {"nested": {"mode": "retained"}},
        None,
        True,
        False,
        0,
        1,
        "",
        "latest",
        "Retained",
        "retained\n",
    ],
)
def test_http_malformed_mode_is_no_store_rejection_before_adapter(mode):
    calls = []

    class Adapter:
        async def close(self):
            pass

        async def read(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"status": "unexpected"}

    body = {k: v for k, v in SEL.items() if k != "run_uuid"} | dict(
        mode=mode, anchor_key=KEY
    )
    with TestClient(
        create_app(Settings(TOKEN, (COMPUTER,)), SimpleNamespace(), saved_results=Adapter()),
        raise_server_exceptions=False,
    ) as client:
        answer = client.post(
            f"/api/v1/runs/{SEL['run_uuid']}/live-results/performance",
            json=body,
            headers={"Authorization": "Bearer " + TOKEN},
        )
    assert answer.status_code == 422
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json()["detail"]["code"] == "selection_required"
    assert calls == []


@pytest.mark.parametrize("mode", ["retained", "current"])
def test_http_scalar_mode_preserves_exact_valid_selection(mode):
    calls = []

    class Adapter:
        async def close(self):
            pass

        async def read(self, operation, selection, query, disconnect=None):
            calls.append((operation, selection, query))
            return {"status": "unavailable"}

    query = dict(mode=mode, anchor_key=KEY)
    body = {k: v for k, v in SEL.items() if k != "run_uuid"} | query
    with TestClient(
        create_app(Settings(TOKEN, (COMPUTER,)), SimpleNamespace(), saved_results=Adapter())
    ) as client:
        answer = client.post(
            f"/api/v1/runs/{SEL['run_uuid']}/live-results/performance",
            json=body,
            headers={"Authorization": "Bearer " + TOKEN},
        )
    assert answer.status_code == 200
    assert answer.headers["cache-control"] == "no-store"
    assert calls == [("performance", SEL, query)]
