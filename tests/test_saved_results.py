"""Repository transport controls; synthetic bytes and injected owning Results API."""

import asyncio
import hashlib
import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest

from qcl_negf_api.saved_results import SavedResultError, SavedResults
from qcl_negf_api.service import CloseResult

RUN = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
SEL = {"run_uuid": RUN, "execution_id": "ex", "attempt": 2, "calcjob_uuid": JOB}
SHA = "a" * 64


def fixture():
    owner = b"synthetic analysis, not HDF5"
    commit = {
        "schema": "qcl-negf.artifact-commit.v2",
        "contract_set": "qcl-negf.results.v1",
        "generation": 1,
        "quality": "unconverged",
        "terminal_status": "failed",
        "scientific_accepted": False,
        "identity": {"point_id": "p", "execution_id": "ex", "attempt": 2, "plan_fingerprint": SHA},
        "artifacts": [
            {
                "path": "analysis.h5",
                "role": "physics.analysis",
                "schema": "qcl-negf-physics-analysis-v4",
                "media_type": "application/x-hdf5",
                "profile": "science",
                "bytes": len(owner),
                "sha256": hashlib.sha256(owner).hexdigest(),
                "dependencies": [],
            }
        ],
    }
    raw = json.dumps(commit).encode()
    series = {
        "points": [
            {
                "id": "p",
                "execution_id": "ex",
                "attempt": 2,
                "data": {
                    "result_commit": "archive/p/commit.json",
                    "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                },
            }
        ]
    }
    files = {
        "result/series_result.json": json.dumps(series).encode(),
        "result/archive/p/commit.json": raw,
        "result/archive/p/analysis.h5": owner,
        "result/unselected/physics.h5": b"never opened",
    }
    opened = []

    @contextmanager
    def open_artifact(run, ex, path, **kwargs):
        close_observer = kwargs.pop("close_observer")
        assert (run, ex, kwargs) == (RUN, "ex", {"attempt": 2, "calcjob_uuid": JOB})
        opened.append(path)
        try:
            yield BytesIO(files[path])
        finally:
            opened.append("closed:" + path)
            close_observer(CloseResult("completed", 0, 0))

    service = SimpleNamespace(
        get_export_selection=lambda *a, **kw: {
            "execution_id": "ex",
            "attempt": 2,
            "calcjob_uuid": JOB,
            "status": "failed",
        },
        get_export_plan=lambda *a, **kw: {
            "plan": json.dumps({"fingerprint": SHA}).encode(),
            "source": "aiida.input.plan",
        },
        list_execution_artifacts=lambda *a, **kw: [
            {
                **{k: SEL[k] for k in ("execution_id", "attempt", "calcjob_uuid")},
                "path": p,
                "size": len(b),
            }
            for p, b in files.items()
        ],
        open_artifact=open_artifact,
    )

    def brief(metadata, **kwargs):
        return {
            "selection": SEL,
            "identity": {"definition_id": "study", "variant_id": "base", "plan_fingerprint": SHA},
            "points": [
                {
                    "point_id": "p",
                    "source_commit_sha256": metadata["series"]["points"][0]["data"].get(
                        "source_commit_sha256"
                    ),
                    "source_attempt": 2,
                }
            ],
            "current_segments": [["p"]],
            "statuses": {"scientific_accepted": False},
            "source": metadata["source"],
        }

    return service, files, opened, raw, brief


def test_brief_never_copies_hdf_and_exact_metadata_cursor():
    async def case():
        service, files, opened, raw, brief = fixture()
        adapter = SavedResults(
            service,
            SimpleNamespace(saved_results_enabled=True),
            brief=brief,
            worker=lambda *a: pytest.fail("brief invoked HDF worker"),
        )
        page = await adapter.read("brief", SEL, {})
        assert page["provider_selection"] == SEL and page["total"] == 1 and page["complete_page"]
        assert opened == ["result/series_result.json", "closed:result/series_result.json"]
        files["result/series_result.json"] += b" "
        with pytest.raises(SavedResultError) as error:
            await adapter.read("brief", SEL, {"cursor": page["metadata_sha256"] + ":0"})
        assert error.value.code == "identity_mismatch"

    asyncio.run(case())


def test_declared_owner_only_commit_and_selection_pinned(tmp_path):
    async def case():
        service, files, opened, raw, brief = fixture()
        captured = []

        def worker(operation, request, root, cancelled, deadline):
            captured.append((operation, request, root))
            assert (root / "archive/p/commit.json").read_bytes() == raw
            assert (root / "archive/p/analysis.h5").read_bytes() == files[
                "result/archive/p/analysis.h5"
            ]
            return {
                "files": [],
                "views": [],
                "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
            }

        adapter = SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), brief=brief, worker=worker
        )
        page = await adapter.read("brief", SEL, {})
        result = await adapter.read(
            "manifest",
            SEL,
            {
                "point_id": "p",
                "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                "metadata_sha256": page["metadata_sha256"],
            },
        )
        assert result["provider_selection"] == SEL
        assert captured[0][1]["expected_identity"]["attempt"] == 2
        assert not captured[0][2].exists()
        assert "result/unselected/physics.h5" not in opened
        with pytest.raises(SavedResultError) as error:
            await adapter.read(
                "manifest",
                SEL,
                {
                    "point_id": "p",
                    "source_commit_sha256": "0" * 64,
                    "metadata_sha256": page["metadata_sha256"],
                },
            )
        assert error.value.code == "identity_mismatch"

    asyncio.run(case())


@pytest.mark.parametrize(
    "change", ["foreign_attempt", "foreign_plan", "escape", "oversize", "wrong_sha"]
)
def test_refuse_before_owner_copy(change):
    async def case():
        service, files, opened, raw, brief = fixture()
        if change == "foreign_attempt":
            service.get_export_selection = lambda *a, **kw: {
                "execution_id": "ex",
                "attempt": 3,
                "calcjob_uuid": JOB,
                "status": "failed",
            }
        if change == "foreign_plan":
            commit = json.loads(raw)
            commit["identity"]["plan_fingerprint"] = "b" * 64
            raw = json.dumps(commit).encode()
            files["result/archive/p/commit.json"] = raw
            series = json.loads(files["result/series_result.json"])
            series["points"][0]["data"]["source_commit_sha256"] = hashlib.sha256(raw).hexdigest()
            files["result/series_result.json"] = json.dumps(series).encode()
        if change == "escape":
            series = json.loads(files["result/series_result.json"])
            series["points"][0]["data"]["result_commit"] = "../private"
            files["result/series_result.json"] = json.dumps(series).encode()
        if change == "oversize":
            original = service.list_execution_artifacts
            service.list_execution_artifacts = lambda *a, **kw: [
                {**r, "size": 65 * 1024 * 1024} if r["path"].endswith("analysis.h5") else r
                for r in original(*a, **kw)
            ]
        if change == "wrong_sha":
            files["result/archive/p/analysis.h5"] = b"wrong same-size bytes".ljust(
                len(files["result/archive/p/analysis.h5"]), b" "
            )
        adapter = SavedResults(
            service,
            SimpleNamespace(saved_results_enabled=True),
            brief=brief,
            worker=lambda *a: pytest.fail("refusal reached native worker"),
        )
        with pytest.raises(SavedResultError):
            page = await adapter.read("brief", SEL, {})
            await adapter.read(
                "manifest",
                SEL,
                {
                    "point_id": "p",
                    "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                    "metadata_sha256": page["metadata_sha256"],
                },
            )
        assert "result/unselected/physics.h5" not in opened
        if change in ("foreign_attempt", "foreign_plan", "escape", "oversize"):
            assert "result/archive/p/analysis.h5" not in opened

    asyncio.run(case())


def test_busy_cancel_keeps_gate_until_worker_cleanup():
    async def case():
        import threading

        service, files, opened, raw, brief = fixture()
        entered, release = threading.Event(), threading.Event()
        paths = []

        def worker(op, req, root, cancelled, deadline):
            paths.append(root)
            entered.set()
            while not release.wait(0.01):
                if cancelled.is_set():
                    release.set()
            return {"files": [], "views": []}

        adapter = SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), brief=brief, worker=worker
        )
        page = await adapter.read("brief", SEL, {})
        task = asyncio.create_task(
            adapter.read(
                "manifest",
                SEL,
                {
                    "point_id": "p",
                    "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                    "metadata_sha256": page["metadata_sha256"],
                },
            )
        )
        while not entered.is_set():
            await asyncio.sleep(0.001)
        with pytest.raises(SavedResultError) as error:
            await adapter.read("brief", SEL, {})
        assert error.value.code == "busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert paths and not paths[0].exists() and not adapter.busy

    asyncio.run(case())


def test_missing_recorded_commit_digest_is_captured_from_only_declared_small_commit():
    async def case():
        service, files, opened, raw, brief = fixture()
        series = json.loads(files["result/series_result.json"])
        del series["points"][0]["data"]["source_commit_sha256"]
        files["result/series_result.json"] = json.dumps(series).encode()
        adapter = SavedResults(
            service,
            SimpleNamespace(saved_results_enabled=True),
            brief=brief,
            worker=lambda *a: pytest.fail("brief opened HDF"),
        )
        page = await adapter.read("brief", SEL, {})
        assert page["points"][0]["source_commit_sha256"] == hashlib.sha256(raw).hexdigest()
        assert page["points"][0]["source_commit_binding"] == "provider_captured_sha256"
        assert opened == [
            "result/series_result.json",
            "closed:result/series_result.json",
            "result/archive/p/commit.json",
            "closed:result/archive/p/commit.json",
        ]

    asyncio.run(case())


def test_borrowed_checkpoint_keeps_source_attempt_separate_from_provider():
    async def case():
        service, files, opened, raw, brief = fixture()
        commit = json.loads(raw)
        commit["identity"]["attempt"] = 1
        raw = json.dumps(commit).encode()
        files["result/archive/p/commit.json"] = raw
        series = json.loads(files["result/series_result.json"])
        series["points"][0]["data"].update(
            checkpoint_source_attempt=1, source_commit_sha256=hashlib.sha256(raw).hexdigest()
        )
        files["result/series_result.json"] = json.dumps(series).encode()

        def worker(op, request, root, cancelled, deadline):
            assert request["expected_identity"]["attempt"] == 1
            assert request["expected_identity"]["calcjob_uuid"] == JOB
            return {"views": [], "files": []}

        adapter = SavedResults(
            service, SimpleNamespace(saved_results_enabled=True), brief=brief, worker=worker
        )
        page = await adapter.read("brief", SEL, {})
        result = await adapter.read(
            "manifest",
            SEL,
            {
                "point_id": "p",
                "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                "metadata_sha256": page["metadata_sha256"],
            },
        )
        assert result["provider_selection"]["attempt"] == 2 and result["source_attempt"] == 1

    asyncio.run(case())


def test_fixed_native_worker_sets_literal_limits_before_owning_read(tmp_path, monkeypatch):
    import resource
    import sys

    from qcl_negf_api import saved_results

    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "operation": "manifest",
                "commit_path": "p/commit.json",
                "expected_identity": {"point_id": "p"},
            }
        )
    )
    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda which, cap: calls.append((which, cap)))
    monkeypatch.setattr(saved_results.os, "sched_getaffinity", lambda pid: {3, 5})
    monkeypatch.setattr(
        saved_results.os, "sched_setaffinity", lambda pid, cpus: calls.append(("cpu", cpus))
    )

    def describe(path, *, expected_identity):
        assert calls == [
            (resource.RLIMIT_CPU, (10, 10)),
            (resource.RLIMIT_AS, (536870912, 536870912)),
            (resource.RLIMIT_FSIZE, (2097152, 2097152)),
            ("cpu", {3}),
        ]
        assert path == tmp_path / "p/commit.json" and expected_identity == {"point_id": "p"}
        return {"views": [], "integrity": {"native_validation": "not_performed"}}

    monkeypatch.setitem(
        sys.modules,
        "qcl_negf_results.saved_views",
        SimpleNamespace(
            describe_saved_views=describe,
            read_saved_view=lambda *a, **k: pytest.fail("wrong operation"),
        ),
    )
    output = BytesIO()
    monkeypatch.setattr(saved_results.sys, "argv", ["internal", str(request)])
    monkeypatch.setattr(saved_results.sys, "stdout", SimpleNamespace(buffer=output))
    assert saved_results._main() == 0
    assert json.loads(output.getvalue())["integrity"]["native_validation"] == "not_performed"


@pytest.mark.parametrize(
    "query",
    [
        {"point_id": "p", "source_commit_sha256": 1, "metadata_sha256": "a" * 64},
        {
            "point_id": "p",
            "source_commit_sha256": "a" * 64,
            "metadata_sha256": "a" * 64,
            "view_id": "/private/path",
            "selection": [0],
        },
        {
            "point_id": "p",
            "source_commit_sha256": "a" * 64,
            "metadata_sha256": "a" * 64,
            "view_id": "density",
            "selection": [True],
        },
    ],
)
def test_invalid_semantic_selector_refused_before_provider_calls(query):
    async def case():
        service = SimpleNamespace(
            get_export_selection=lambda *a, **k: pytest.fail("invalid request reached provider")
        )
        adapter = SavedResults(service, SimpleNamespace(saved_results_enabled=True))
        with pytest.raises(SavedResultError) as error:
            await adapter.read("slice", SEL, query)
        assert error.value.code == "selection_required"

    asyncio.run(case())


def test_stalled_cleanup_returns_bounded_but_retains_gate_and_owned_future():
    async def case():
        import threading

        service, files, opened, raw, brief = fixture()
        entered, release = threading.Event(), threading.Event()
        roots = []

        def worker(op, request, root, cancelled, deadline):
            roots.append(root)
            entered.set()
            release.wait(2)  # Controlled stand-in for an actor/storage stall.
            return {"views": [], "files": []}

        adapter = SavedResults(
            service,
            SimpleNamespace(saved_results_enabled=True),
            brief=brief,
            worker=worker,
            cleanup_seconds=0.02,
        )
        page = await adapter.read("brief", SEL, {})
        task = asyncio.create_task(
            adapter.read(
                "manifest",
                SEL,
                {
                    "point_id": "p",
                    "source_commit_sha256": hashlib.sha256(raw).hexdigest(),
                    "metadata_sha256": page["metadata_sha256"],
                },
            )
        )
        while not entered.is_set():
            await asyncio.sleep(0.001)
        try:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 0.25)
            assert adapter.busy and adapter.closed and adapter.active is not None
            assert roots[0].exists()  # Never delete input while real worker owns it.
            with pytest.raises(SavedResultError):
                await adapter.read("brief", SEL, {})
            await asyncio.wait_for(adapter.close(), 0.25)
            assert adapter.busy
        finally:
            release.set()
        for _ in range(1000):
            if not adapter.busy:
                break
            await asyncio.sleep(0.001)
        assert not adapter.busy and not roots[0].exists()

    asyncio.run(case())


def real_brief_fixture():
    """Native-shaped literals; no fake brief projection or native HDF owner."""
    service, files, opened, _, _ = fixture()
    plan = {
        "schema": "qcl-negf-scientific-plan-v2",
        "root_definition_id": "study",
        "root_kind": "study",
        "name": "Study",
        "fingerprint": SHA,
        "computation_count": 3,
        "inclusions": [],
        "executions": [{"id": "ex", "definition_id": "study", "variant_id": "base"}],
        "points": [
            {
                "id": f"p{i}",
                "execution_id": "ex",
                "temperature_K": 70.0,
                "voltage_per_period_V": i * 0.01,
                "branch": "b",
                "order": i,
            }
            for i in range(3)
        ],
    }
    rows = []
    template = json.loads(files.pop("result/archive/p/commit.json"))
    for i, attempt, status in ((0, 1, "completed"), (1, 2, "completed"), (2, 2, "paused")):
        commit = json.loads(json.dumps(template))
        source = 1 if i == 2 else attempt
        commit["identity"].update(point_id=f"p{i}", attempt=source)
        commit_raw = json.dumps(commit).encode()
        path = f"archive/p{i}/commit.json"
        files["result/" + path] = commit_raw
        files[f"result/archive/p{i}/analysis.h5"] = files["result/archive/p/analysis.h5"]
        data = {
            "result_commit": path,
            "source_commit_sha256": hashlib.sha256(commit_raw).hexdigest(),
        }
        if i == 2:
            data.update(
                checkpoint_source_attempt=1,
                pause_reason="resource_pressure",
                resume_kind="checkpoint",
                recovery_origin="last_committed_before_resource_pause",
            )
        rows.append(
            {
                "id": f"p{i}",
                "execution_id": "ex",
                "attempt": attempt,
                "status": status,
                "data": data,
                "observables": {
                    "current_density_A_per_m2": i + 1,
                    "scientific_assessment": {"scientific_accepted": i != 2},
                },
            }
        )
    series = {
        "schema": "qcl-negf-series-result-v3",
        "contract_set": "qcl-negf.results.v1",
        "plan_fingerprint": SHA,
        "execution_id": "ex",
        "attempt": 2,
        "run_uuid": RUN,
        "calcjob_uuid": JOB,
        "points": rows,
    }
    files["result/series_result.json"] = json.dumps(series).encode()
    service.get_export_plan = lambda *a, **k: {
        "plan": json.dumps(plan).encode(),
        "source": "aiida.input.plan",
    }
    return service, files, opened, series


def test_real_owning_brief_retained_and_borrowed_source_attempts(monkeypatch):
    import h5py

    monkeypatch.setattr(h5py, "File", lambda *a, **k: pytest.fail("brief opened HDF"))

    async def case():
        service, files, opened, series = real_brief_fixture()
        expected = []

        def worker(op, request, root, cancelled, deadline):
            expected.append(request["expected_identity"])
            return {"views": [], "files": []}

        adapter = SavedResults(service, SimpleNamespace(saved_results_enabled=True), worker=worker)
        try:
            page = await adapter.read("brief", SEL, {})  # Calls real brief_result.
            assert page["selection"] == SEL and page["provider_selection"] == SEL
            assert [p["source_attempt"] for p in page["points"]] == [1, 2, 1]
            assert page["current_segments"] == [["p0", "p1"]]
            assert opened == ["result/series_result.json", "closed:result/series_result.json"]
            for point in (page["points"][0], page["points"][2]):
                result = await adapter.read(
                    "manifest",
                    SEL,
                    {
                        "point_id": point["point_id"],
                        "source_commit_sha256": point["source_commit_sha256"],
                        "metadata_sha256": page["metadata_sha256"],
                    },
                )
                assert (
                    result["provider_selection"]["attempt"] == 2 and result["source_attempt"] == 1
                )
            assert [identity["attempt"] for identity in expected] == [1, 1]
            assert [r["attempt"] for r in series["points"]] == [1, 2, 2]
        finally:
            await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize("bad", ["future", "bool", "foreign_identity"])
def test_real_owning_brief_rejects_invalid_source_attempt_or_identity(bad, monkeypatch):
    import h5py

    monkeypatch.setattr(h5py, "File", lambda *a, **k: pytest.fail("brief opened HDF"))

    async def case():
        service, files, opened, series = real_brief_fixture()
        if bad == "future":
            series["points"][0]["attempt"] = 3
        elif bad == "bool":
            series["points"][0]["attempt"] = True
        else:
            series["run_uuid"] = "foreign-run"
        files["result/series_result.json"] = json.dumps(series).encode()
        adapter = SavedResults(service, SimpleNamespace(saved_results_enabled=True))
        try:
            with pytest.raises(SavedResultError) as error:
                await adapter.read("brief", SEL, {})
            assert error.value.code == "identity_mismatch"
        finally:
            await adapter.close()

    asyncio.run(case())


@pytest.mark.parametrize("read_failure", [False, True])
def test_real_actor_pending_context_exit_permanently_disables_saved_gate(monkeypatch, read_failure):
    import threading
    import time

    import aiida
    import aiida.manage

    from qcl_negf_api import service as actor_module
    from qcl_negf_api.settings import Settings

    service, files, opened, raw, brief = fixture()
    exit_started, release, exit_done = threading.Event(), threading.Event(), threading.Event()
    exits = []

    class Payload(BytesIO):
        def read(self, size):
            if read_failure:
                raise OSError("synthetic read failure")
            return super().read(size)

    @contextmanager
    def held_exit(*args, **kwargs):
        try:
            yield Payload(files[args[2]])
        finally:
            exits.append("started")
            exit_started.set()
            assert release.wait(3)
            exits.append("closed")
            exit_done.set()

    plugin = SimpleNamespace(
        get_export_selection=service.get_export_selection,
        get_export_plan=service.get_export_plan,
        list_execution_artifacts=service.list_execution_artifacts,
        list_runs=lambda **kwargs: [],
        open_artifact=held_exit,
    )
    monkeypatch.setattr(aiida, "load_profile", lambda profile: None)
    monkeypatch.setattr(actor_module, "import_module", lambda name: plugin)
    monkeypatch.setattr(
        aiida.manage, "get_manager", lambda: SimpleNamespace(unload_profile=lambda: None)
    )
    settings = Settings(
        "0123456789abcdef" * 4,
        ("33333333-3333-4333-8333-333333333333",),
        actor_call_timeout_seconds=0.5,
        actor_io_timeout_seconds=0.5,
        actor_close_timeout_seconds=0.02,
    )
    actor = actor_module.AiidaService("synthetic", settings=settings)

    async def case():
        adapter = SavedResults(actor, settings, brief=brief, cleanup_seconds=0.02)
        try:
            with pytest.raises(SavedResultError):
                await asyncio.wait_for(adapter.read("brief", SEL, {}), 0.5)
            assert exit_started.is_set() and not exit_done.is_set()
            assert adapter.active is None  # Wrapper finished; actor lease did not.
            assert adapter.closed and adapter.busy
            with pytest.raises(SavedResultError):
                await adapter.read("brief", SEL, {})
            assert exits == ["started"]
            release.set()
            await asyncio.to_thread(actor.list_runs)
            assert exit_done.is_set() and exits == ["started", "closed"]
            with pytest.raises(SavedResultError):
                await adapter.read("brief", SEL, {})
        finally:
            release.set()
            await adapter.close()

    try:
        asyncio.run(case())
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)
