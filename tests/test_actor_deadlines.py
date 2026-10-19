"""Event-controlled actor response bounds; no real AiiDA profile or solver."""

import threading
import time
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest

from qcl_negf_api import service as actor_module
from qcl_negf_api.exports import ExportStore
from qcl_negf_api.settings import Settings

TOKEN = "0123456789abcdef" * 4
CODE = "10dd2a4c-031b-425c-90b8-2f7ea564d313"
RUN = "e7f5313d-22fd-49fa-9be5-85fbe29e1974"


def policy(**kwargs):
    return Settings(
        TOKEN,
        (CODE,),
        actor_call_timeout_seconds=0.08,
        actor_io_timeout_seconds=0.08,
        actor_close_timeout_seconds=0.08,
        **kwargs,
    )


@pytest.fixture
def backend(monkeypatch):
    import aiida
    import aiida.manage

    events = []
    plugin = SimpleNamespace(list_runs=lambda **kw: [])
    monkeypatch.setattr(
        aiida, "load_profile", lambda profile: events.append(("load", threading.get_ident()))
    )
    monkeypatch.setattr(actor_module, "import_module", lambda name: plugin)
    monkeypatch.setattr(
        aiida.manage,
        "get_manager",
        lambda: SimpleNamespace(
            unload_profile=lambda: events.append(("unload", threading.get_ident()))
        ),
    )
    return plugin, events


def test_blocked_call_returns_unknown_without_duplicate_submit(backend):
    plugin, events = backend
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def submit(*args, **kwargs):
        calls.append(RUN)
        started.set()
        assert release.wait(3)
        returned.set()
        return {"uuid": RUN}

    plugin.submit_plan = submit
    actor = actor_module.AiidaService("example", settings=policy())
    try:
        before = time.monotonic()
        with pytest.raises(actor_module.ActorOutcomeUnknown) as caught:
            actor.submit_plan(b"frozen")
        assert started.is_set() and not release.is_set()
        assert time.monotonic() - before < 0.8
        receipt = caught.value.receipt
        assert receipt.outcome == "unknown" and receipt.actor_state == "blocked"
        with pytest.raises(actor_module.ActorUnavailable):
            actor.submit_plan(b"frozen")
        assert calls == [RUN]
        close = actor.close()
        assert close.state == "stopping" and close.pending_operations == 1
        assert not any(name == "unload" for name, _ in events)
        release.set()
        assert returned.wait(1)
        # A mailbox barrier is stronger than observing backend return alone.
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        assert calls == [RUN]
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)


@pytest.mark.parametrize(
    "error,status", [(ValueError("invalid"), 422), (RuntimeError("lost receipt"), 503)]
)
def test_started_mutation_exception_latches_original_receipt(backend, error, status):
    plugin, _ = backend
    calls = []

    def save(*args):
        calls.append("stored")
        raise error

    plugin.save_agent_report = save
    actor = actor_module.AiidaService("example", settings=policy())
    try:
        with pytest.raises(actor_module.ActorOutcomeUnknown) as first:
            actor.save_agent_report(RUN, b"report")
        assert first.value.backend_status == status
        assert first.value.receipt.outcome == "unknown"
        with pytest.raises(actor_module.ActorUnavailable) as refused:
            actor.save_agent_report(RUN, b"report")
        assert refused.value.code == "mutation_unresolved"
        assert refused.value.receipt.outcome == "not_started"
        assert refused.value.receipt.unresolved_operation_id == first.value.receipt.operation_id
        assert calls == ["stored"]
    finally:
        actor.close(deadline=time.monotonic() + 1)


def test_close_does_not_claim_backend_completion(backend):
    plugin, events = backend
    started, release = threading.Event(), threading.Event()
    entered, exited = [], []

    class Stream(BytesIO):
        def read(self, size):
            started.set()
            assert release.wait(3)
            return super().read(size)

    @contextmanager
    def opened(*args, **kwargs):
        entered.append(threading.get_ident())
        try:
            yield Stream(b"late bytes")
        finally:
            exited.append(threading.get_ident())

    plugin.open_artifact = opened
    actor = actor_module.AiidaService("example", settings=policy())
    context = actor.open_artifact(RUN, "point_1", "raw.bin")
    reader = context.__enter__()
    try:
        with pytest.raises(actor_module.ActorDeadline):
            reader.read(65536)
        assert started.is_set() and not release.is_set()
        assert reader.close().state == "stopping"
        assert actor.close().state == "stopping"
        assert exited == []
        with pytest.raises(ValueError):
            reader.read(1)
        release.set()
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        assert reader.close().state == "completed"
        assert entered == exited and entered[0] != threading.get_ident()
        assert [name for name, _ in events] == ["load", "unload"]
    finally:
        release.set()
        context.__exit__(None, None, None)
        actor.close(deadline=time.monotonic() + 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_call_timeout_seconds", 0),
        ("actor_io_timeout_seconds", float("nan")),
        ("actor_close_timeout_seconds", True),
        ("actor_initialize_timeout_seconds", 301),
        ("actor_max_pending_calls", True),
        ("actor_max_open_artifacts", 65),
    ],
)
def test_actor_operational_settings_reject_invalid_values(field, value):
    with pytest.raises(ValueError):
        Settings(TOKEN, (CODE,), **{field: value})


def test_store_close_bypasses_stalled_storage_metadata_lock(monkeypatch):
    from qcl_negf_api import exports

    started, release = threading.Event(), threading.Event()
    store = ExportStore(SimpleNamespace(), Settings(TOKEN, (CODE,)))
    store._entries["synthetic"] = {
        "export_id": "synthetic",
        "run_uuid": RUN,
        "execution_id": "point_1",
        "attempt": 1,
        "calcjob_uuid": CODE,
        "status": "unknown",
        "profile": "science",
        "state": "ready",
        "progress": {},
        "expires_unix": time.time() + 60,
        "reserved": 123,
        "readers": 0,
        "source_bytes": 4,
    }
    failures = []

    def disk_usage(path):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(free=10**12)

    monkeypatch.setattr(exports.shutil, "disk_usage", disk_usage)

    def storage():
        try:
            store._storage("synthetic", 0)
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=storage)
    thread.start()
    try:
        assert started.wait(1)
        assert not store._lock.acquire(blocking=False), "storage stall must own the old store lock"
        before = time.monotonic()
        result = store.close(deadline=before + 0.08)
        assert time.monotonic() - before < 0.8
        assert result.state == "stopping"
        assert result.pending_operations is None and result.pending_leases is None
        assert result.error_code == "store_metadata_pending"
        assert store._root.exists() and store._cancelled.is_set()
        assert not release.is_set()
    finally:
        release.set()
        thread.join(1)
        assert not thread.is_alive() and not failures
        store.close(deadline=time.monotonic() + 1)


def test_queued_expiry_is_not_started_and_never_replayed(backend):
    plugin, _ = backend
    started, release = threading.Event(), threading.Event()
    calls, errors = [], []

    def running():
        started.set()
        assert release.wait(3)
        return []

    plugin.list_runs = running
    plugin.get_run = lambda run: calls.append(run)
    actor = actor_module.AiidaService("example", settings=policy())
    # Distinct configured admission deadlines let the queued call expire first.
    actor._policy["actor_call_timeout_seconds"] = 1

    def client():
        try:
            actor.list_runs()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=client)
    thread.start()
    try:
        assert started.wait(1)
        actor._policy["actor_call_timeout_seconds"] = 0.08
        with pytest.raises(actor_module.ActorDeadline) as caught:
            actor.get_run(RUN)
        assert caught.value.receipt.outcome == "not_started"
        assert actor._mutation_receipt is None and not release.is_set()
        release.set()
        thread.join(1)
        assert not thread.is_alive() and not errors and calls == []
    finally:
        release.set()
        thread.join(1)
        actor.close(deadline=time.monotonic() + 1)
        assert not thread.is_alive()


def test_full_mailbox_still_accepts_close_request(backend):
    plugin, _ = backend
    started, release = threading.Event(), threading.Event()
    errors = []

    def running():
        started.set()
        assert release.wait(3)
        return []

    plugin.list_runs = running
    actor = actor_module.AiidaService("example", settings=policy(actor_max_pending_calls=1))

    def client():
        try:
            actor.list_runs()
        except actor_module.ActorDeadline:
            pass
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=client)
    thread.start()
    try:
        assert started.wait(1)
        with pytest.raises(actor_module.ActorUnavailable) as caught:
            actor.get_run(RUN)
        assert caught.value.code == "queue_full"
        assert caught.value.receipt.outcome == "not_started"
        assert actor.close().state == "stopping"
        assert not release.is_set()
    finally:
        release.set()
        thread.join(1)
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        assert not thread.is_alive() and not errors


def test_late_open_exits_once_without_publishing_reader(backend):
    plugin, _ = backend
    started, release, exited = threading.Event(), threading.Event(), threading.Event()
    exits = []

    @contextmanager
    def opened(*args, **kwargs):
        started.set()
        assert release.wait(3)
        try:
            yield BytesIO(b"data")
        finally:
            exits.append(threading.get_ident())
            exited.set()

    plugin.open_artifact = opened
    actor = actor_module.AiidaService("example", settings=policy())
    try:
        with pytest.raises(actor_module.ActorDeadline) as caught:
            with actor.open_artifact(RUN, "point_1", "raw.bin"):
                pytest.fail("late reader must never be published")
        assert caught.value.receipt.outcome == "unknown"
        assert started.is_set() and not release.is_set() and exits == []
        release.set()
        assert exited.wait(1)
        assert len(exits) == 1 and exits[0] != threading.get_ident()
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)


@pytest.mark.parametrize("outcome", ["stopping", "failed", "raised"])
def test_lifespan_always_requests_owned_actor_close_with_shared_deadline(monkeypatch, outcome):
    import asyncio

    from qcl_negf_api import api
    from qcl_negf_api.api import create_app

    calls = []

    class Actor:
        def __init__(self, profile, *, settings):
            pass

        def close(self, *, deadline):
            calls.append(("actor", deadline))
            return actor_module.CloseResult("stopping", 1, 0)

    class Store:
        def __init__(self, service, settings):
            pass

        def close(self, *, deadline):
            calls.append(("export", deadline))
            if outcome == "raised":
                raise OSError("synthetic cleanup error")
            return actor_module.CloseResult(outcome, 1, 0)

    monkeypatch.setattr(api, "AiidaService", Actor)
    monkeypatch.setattr(api, "ExportStore", Store)
    app = create_app(Settings(TOKEN, (CODE,)))

    async def lifecycle():
        async with app.router.lifespan_context(app):
            pass

    if outcome == "raised":
        with pytest.raises(OSError):
            asyncio.run(lifecycle())
    else:
        asyncio.run(lifecycle())
    assert [name for name, _ in calls] == ["export", "actor"]
    assert calls[0][1] == calls[1][1]


def test_init_deadline_keeps_guard_until_actual_owner_cleanup(backend, monkeypatch):
    import aiida

    started, release = threading.Event(), threading.Event()

    def load(profile):
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(aiida, "load_profile", load)
    actor = actor_module.AiidaService.__new__(actor_module.AiidaService)
    try:
        before = time.monotonic()
        with pytest.raises(actor_module.ActorDeadline) as caught:
            actor.__init__("example", settings=policy(actor_initialize_timeout_seconds=0.08))
        assert caught.value.code == "initialization_deadline"
        assert started.is_set() and not release.is_set()
        assert time.monotonic() - before < 0.8
        with pytest.raises(actor_module.ActorUnavailable) as refused:
            actor_module.AiidaService("example", settings=policy())
        assert refused.value.code == "owner_unavailable"
    finally:
        release.set()
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"


def test_late_mutation_return_restores_reads_but_never_clears_fence(backend):
    plugin, _ = backend
    release, returned = threading.Event(), threading.Event()
    calls = []

    def submit(*args):
        calls.append("submit")
        assert release.wait(3)
        returned.set()
        return {"uuid": RUN}

    plugin.submit_plan = submit
    plugin.get_run = lambda run: {"uuid": run}
    actor = actor_module.AiidaService("example", settings=policy())
    try:
        with pytest.raises(actor_module.ActorOutcomeUnknown) as unknown:
            actor.submit_plan(b"frozen")
        release.set()
        assert returned.wait(1)
        with actor._condition:
            assert actor._condition.wait_for(lambda: actor._state == "ready", timeout=1)
        assert actor.get_run(RUN) == {"uuid": RUN}
        with pytest.raises(actor_module.ActorUnavailable) as refused:
            actor.kill_run(RUN)
        assert refused.value.code == "mutation_unresolved"
        assert refused.value.receipt.unresolved_operation_id == unknown.value.receipt.operation_id
        assert calls == ["submit"]
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)


def test_store_close_latches_before_stalled_create_admission(monkeypatch):
    from qcl_negf_api import exports

    started, release = threading.Event(), threading.Event()
    service = SimpleNamespace(
        get_export_selection=lambda *args, **kw: {
            "execution_id": "point_1",
            "attempt": 1,
            "calcjob_uuid": CODE,
            "status": "unknown",
        },
        list_execution_artifacts=lambda *args, **kw: [
            {
                "execution_id": "point_1",
                "attempt": 1,
                "calcjob_uuid": CODE,
                "path": "result/native.bin",
                "size": 4,
            }
        ],
    )
    store = ExportStore(service, Settings(TOKEN, (CODE,)))
    failures = []

    def disk_usage(path):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(free=10**12)

    monkeypatch.setattr(exports.shutil, "disk_usage", disk_usage)

    def create():
        try:
            store.create(RUN, "point_1", "science")
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=create)
    thread.start()
    try:
        assert started.wait(1)
        assert not store._lock.acquire(blocking=False)
        before = time.monotonic()
        result = store.close(deadline=before + 0.08)
        assert time.monotonic() - before < 0.8 and not release.is_set()
        assert result.state == "stopping" and result.pending_operations is None
        assert result.pending_leases is None and result.error_code == "store_metadata_pending"
        assert store._root.exists() and store._cancelled.is_set()
    finally:
        release.set()
        thread.join(1)
        assert not thread.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], exports.ExportBusy)
        assert store.close(deadline=time.monotonic() + 1).state == "completed"


def _ready_entry(store, identifier):
    directory = store._root / identifier / "archive"
    directory.mkdir(parents=True)
    (directory / "archive.tar.xz").write_bytes(b"payload")
    store._entries[identifier] = {
        "export_id": identifier,
        "run_uuid": RUN,
        "execution_id": "point_1",
        "attempt": 1,
        "calcjob_uuid": CODE,
        "status": "unknown",
        "profile": "science",
        "state": "ready",
        "progress": {},
        "expires_unix": time.time() + 60,
        "reserved": 7,
        "readers": 0,
        "source_bytes": 4,
        "receipt": {"archive": "archive.tar.xz"},
    }


def test_ttl_cleanup_is_coalesced_on_worker_without_store_lock(monkeypatch):
    from qcl_negf_api import exports

    started, release = threading.Event(), threading.Event()
    store = ExportStore(SimpleNamespace(), Settings(TOKEN, (CODE,)))
    _ready_entry(store, "expired")
    _ready_entry(store, "leased")
    store._entries["expired"]["expires_unix"] = time.time() - 1
    removed, submitted = [], []
    original_remove, original_submit = exports.shutil.rmtree, store._executor.submit

    def rmtree(path, *args, **kwargs):
        removed.append((str(path), threading.get_ident()))
        if str(path) == str(store._root / "expired"):
            started.set()
            assert release.wait(3)
        return original_remove(path, *args, **kwargs)

    def submit(function, *args, **kwargs):
        submitted.append(function.__name__)
        return original_submit(function, *args, **kwargs)

    monkeypatch.setattr(exports.shutil, "rmtree", rmtree)
    monkeypatch.setattr(store._executor, "submit", submit)
    context = store.open("leased")
    context.__enter__()
    try:
        with pytest.raises(LookupError):
            store.get("expired")
        assert started.wait(1)
        assert store._lock.acquire(blocking=False)
        store._lock.release()
        for _ in range(5):
            with pytest.raises(LookupError):
                store.get("expired")
        assert store.close(deadline=time.monotonic() + 0.08).state == "stopping"
        assert submitted == ["_maintain"]
        assert store._entries["expired"]["reserved"] == 7
        assert (store._root / "leased" / "archive" / "archive.tar.xz").exists()
        assert not release.is_set()
        release.set()
        # Wait for the maintenance task to reach lease waiting using the old
        # directory's disappearance, witnessed by its completion Event wrapper.
        context.__exit__(None, None, None)
        context = None
        assert store.close(deadline=time.monotonic() + 1).state == "completed"
        assert not store._root.exists()
        paths = [path for path, _ in removed]
        assert paths.count(str(store._root / "expired")) == 1
        assert paths.count(str(store._root / "leased")) == 1
        assert paths.count(str(store._root)) == 1
        assert all(owner != threading.get_ident() for _, owner in removed)
    finally:
        release.set()
        if context is not None:
            context.__exit__(None, None, None)
        store.close(deadline=time.monotonic() + 1)


def test_cleanup_failure_retains_reservation_and_is_not_retried(monkeypatch):
    from qcl_negf_api import exports

    store = ExportStore(SimpleNamespace(), Settings(TOKEN, (CODE,)))
    _ready_entry(store, "failed")
    original = exports.shutil.rmtree
    calls = []

    def remove(path, *args, **kwargs):
        calls.append(str(path))
        if str(path) == str(store._root / "failed"):
            raise OSError("synthetic remove failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(exports.shutil, "rmtree", remove)
    try:
        result = store.close(deadline=time.monotonic() + 1)
        assert result.state == "failed"
        assert store._entries["failed"]["reserved"] == 7
        assert store._root.exists()
        assert store.close() == result
        assert calls == [str(store._root / "failed")]
    finally:
        # Explicit fixture cleanup after the once-only product attempt; this
        # does not turn the failed product result into completed.
        original(store._root)


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
def test_disconnect_releases_handle_exactly_once_after_backend_returns(backend, spec_version):
    import anyio

    from qcl_negf_api.api import ClosingStreamingResponse

    plugin, _ = backend
    started, release, response_returned = threading.Event(), threading.Event(), threading.Event()
    exits = []

    class Stream(BytesIO):
        def read(self, size):
            started.set()
            assert release.wait(3)
            return super().read(size)

    @contextmanager
    def opened(*args, **kwargs):
        try:
            yield Stream(b"late payload")
        finally:
            exits.append(threading.get_ident())

    plugin.open_artifact = opened
    actor = actor_module.AiidaService("example", settings=policy())

    def chunks():
        with actor.open_artifact(RUN, "point_1", "raw.bin") as reader:
            yield reader.read(65536)

    # Retain the actual response against GC while its caller disconnects.
    response = ClosingStreamingResponse(chunks())
    messages, deadlines = [], []

    async def scenario():
        async def receive():
            await anyio.to_thread.run_sync(started.wait, 1)
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        async def serving():
            try:
                await response(
                    {"type": "http", "asgi": {"spec_version": spec_version}}, receive, send
                )
            except actor_module.ActorDeadline as exc:
                # Once headers were sent, a read deadline aborts streaming. It
                # can propagate through Starlette's non-abandoning next() even
                # after caller cancellation; a successful body is not expected.
                deadlines.append(exc)
            finally:
                response_returned.set()

        with anyio.fail_after(1):
            async with anyio.create_task_group() as group:
                group.start_soon(serving)
                assert await anyio.to_thread.run_sync(started.wait, 1)
                # Disconnection cancels the caller while next() owns a backend
                # read. Both protocol cells must run the response's shielded
                # explicit generator close; backend ownership remains deferred.
                group.cancel_scope.cancel()

    try:
        before = time.monotonic()
        anyio.run(scenario)
        assert time.monotonic() - before < 0.9
        assert response_returned.is_set() and not release.is_set()
        assert len(deadlines) == 1
        assert deadlines[0].code == "operation_deadline"
        assert deadlines[0].receipt.outcome == "unknown"
        assert exits == []
        assert not any(message.get("body") for message in messages)
        assert actor.close().state == "stopping"
        release.set()
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        assert len(exits) == 1 and exits[0] != threading.get_ident()
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)


@pytest.mark.parametrize("queued_operation", ["submit_plan", "kill_run", "save_agent_report"])
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
def test_queued_mutation_is_refused_when_earlier_dispatched_mutation_becomes_unknown(
    backend,
    queued_operation,
    error_type,
):
    plugin, _ = backend
    started, release = threading.Event(), threading.Event()
    backend_calls, first_errors, second_errors, second_values = [], [], [], []

    def store(run, raw):
        if raw == b"first report":
            started.set()
            assert release.wait(3)
            raise error_type("synthetic post-store failure")
        backend_calls.append("save_agent_report")
        return {"uuid": CODE}

    plugin.save_agent_report = store
    plugin.submit_plan = lambda *args: backend_calls.append("submit_plan")
    plugin.kill_run = lambda *args: backend_calls.append("kill_run")
    plugin.get_run = lambda run: {"uuid": run}
    settings = Settings(
        TOKEN, (CODE,), actor_call_timeout_seconds=1, actor_close_timeout_seconds=0.08
    )
    actor = actor_module.AiidaService("example", settings=settings)

    def first():
        try:
            actor.save_agent_report(RUN, b"first report")
        except BaseException as exc:
            first_errors.append(exc)

    def second():
        args = (RUN, b"second report") if queued_operation == "save_agent_report" else (RUN,)
        try:
            second_values.append(getattr(actor, queued_operation)(*args))
        except BaseException as exc:
            second_errors.append(exc)

    first_thread, second_thread = threading.Thread(target=first), threading.Thread(target=second)
    first_thread.start()
    second_started = False
    try:
        assert started.wait(1)
        second_thread.start()
        second_started = True
        with actor._condition:
            assert actor._condition.wait_for(
                lambda: any(message.name == queued_operation for message in actor._queue),
                timeout=1,
            ), "second mutation must be admitted before the first backend exception"
        assert not release.is_set()
        release.set()
        first_thread.join(1)
        second_thread.join(1)
        assert not first_thread.is_alive() and not second_thread.is_alive()
        assert len(first_errors) == 1 and isinstance(
            first_errors[0], actor_module.ActorOutcomeUnknown
        )
        assert first_errors[0].receipt.outcome == "unknown"
        assert backend_calls == [], (
            "queued mutation must not cross the existing unknown-effect fence"
        )
        assert second_values == [] and len(second_errors) == 1
        refused = second_errors[0]
        assert isinstance(refused, actor_module.ActorUnavailable)
        assert refused.code == "mutation_unresolved"
        assert refused.receipt.outcome == "not_started"
        assert refused.receipt.unresolved_operation_id == first_errors[0].receipt.operation_id
        assert actor.get_run(RUN) == {"uuid": RUN}
    finally:
        release.set()
        first_thread.join(1)
        if second_started:
            second_thread.join(1)
        actor.close(deadline=time.monotonic() + 1)
        assert not first_thread.is_alive()
        assert not second_started or not second_thread.is_alive()


@pytest.mark.parametrize("operation", ["read", "seek"])
def test_reader_close_counts_include_timed_out_backend_operation_until_actual_return(
    backend, operation
):
    plugin, _ = backend
    started, release = threading.Event(), threading.Event()
    exits = []

    class Stream(BytesIO):
        def read(self, size):
            started.set()
            assert release.wait(3)
            return super().read(size)

        def seek(self, offset):
            started.set()
            assert release.wait(3)
            return super().seek(offset)

    @contextmanager
    def opened(*args, **kwargs):
        try:
            yield Stream(b"data")
        finally:
            exits.append(threading.get_ident())

    plugin.open_artifact = opened
    actor = actor_module.AiidaService("example", settings=policy())
    context = actor.open_artifact(RUN, "point_1", "raw.bin")
    reader = context.__enter__()
    try:
        with pytest.raises(actor_module.ActorDeadline):
            getattr(reader, operation)(1)
        assert started.is_set() and not release.is_set()
        for _ in range(2):
            result = reader.close(deadline=time.monotonic())
            assert result.state == "stopping"
            assert (result.pending_operations, result.pending_leases) == (1, 1)
        actor_result = actor.close(deadline=time.monotonic())
        assert (actor_result.pending_operations, actor_result.pending_leases) == (1, 1)
        assert exits == [] and not release.is_set()
        release.set()
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        result = reader.close()
        assert (result.state, result.pending_operations, result.pending_leases) == (
            "completed",
            0,
            0,
        )
        assert len(exits) == 1
    finally:
        release.set()
        context.__exit__(None, None, None)
        actor.close(deadline=time.monotonic() + 1)


def test_late_report_uuid_is_logged_with_original_operation_without_report_bytes(backend, caplog):
    import logging

    plugin, _ = backend
    started, release, returned = threading.Event(), threading.Event(), threading.Event()
    report_uuid = "017b5339-4d28-4839-8239-d8a229a691f8"
    raw = b"private report content must never enter operational diagnostics"
    writes = []

    def store(run, data):
        assert (run, data) == (RUN, raw)
        writes.append(report_uuid)
        started.set()
        assert release.wait(3)
        returned.set()
        return {"uuid": report_uuid, "filename": "agent-report.json", "bytes": len(raw)}

    plugin.save_agent_report = store
    actor = actor_module.AiidaService("example", settings=policy())
    caplog.set_level(logging.INFO, logger=actor_module.__name__)
    try:
        with pytest.raises(actor_module.ActorOutcomeUnknown) as caught:
            actor.save_agent_report(RUN, raw)
        operation_id = caught.value.receipt.operation_id
        assert started.is_set() and not release.is_set()
        release.set()
        assert returned.wait(1)
        with actor._condition:
            assert actor._condition.wait_for(lambda: actor._state == "ready", timeout=1)
        records = [
            record.getMessage() for record in caplog.records if record.name == actor_module.__name__
        ]
        assert any(
            operation_id in message
            and report_uuid in message
            and RUN in message
            and "returned" in message
            for message in records
        ), "late report effect must retain report UUID, anchor and original operation UUID together"
        assert all(raw.decode() not in message for message in records)
        with pytest.raises(actor_module.ActorUnavailable) as refused:
            actor.save_agent_report(RUN, raw)
        assert refused.value.code == "mutation_unresolved"
        assert refused.value.receipt.unresolved_operation_id == operation_id
        assert writes == [report_uuid]
    finally:
        release.set()
        actor.close(deadline=time.monotonic() + 1)


@pytest.mark.parametrize("operation", ["read", "seek"])
def test_queued_reader_close_cancels_unstarted_work_without_losing_other_leases(
    backend,
    monkeypatch,
    operation,
):
    plugin, events = backend
    started, release = threading.Event(), threading.Event()
    backend_calls, exits, blocker_errors, reader_errors = [], [], [], []
    # A broken owner intentionally retains its process guard. Isolate this
    # negative fixture's guard, restoring it only after its owner has returned;
    # the assertions below still require that the actor itself remains healthy.
    monkeypatch.setattr(actor_module, "OWNER_GUARD", threading.Lock())

    def blocking_read():
        started.set()
        assert release.wait(3)
        return []

    class Stream(BytesIO):
        def __init__(self, path):
            super().__init__(b"data")
            self.path = path

        def read(self, size):
            assert not self.closed, "backend must never read an already exited resource"
            backend_calls.append((self.path, "read"))
            return super().read(size)

        def seek(self, offset):
            assert not self.closed, "backend must never seek an already exited resource"
            backend_calls.append((self.path, "seek"))
            return super().seek(offset)

    @contextmanager
    def opened(run, execution, path, **kwargs):
        stream = Stream(path)
        try:
            yield stream
        finally:
            exits.append((path, threading.get_ident()))
            stream.close()

    plugin.list_runs = blocking_read
    plugin.get_run = lambda run: {"uuid": run}
    plugin.open_artifact = opened
    settings = Settings(
        TOKEN,
        (CODE,),
        actor_call_timeout_seconds=1,
        actor_io_timeout_seconds=1,
        actor_close_timeout_seconds=0.08,
    )
    actor = actor_module.AiidaService("example", settings=settings)
    primary_context = actor.open_artifact(RUN, "point_1", "primary.bin")
    primary = primary_context.__enter__()
    other_context = actor.open_artifact(RUN, "point_1", "independent.bin")
    other = other_context.__enter__()

    def blocker():
        try:
            actor.list_runs()
        except BaseException as exc:
            blocker_errors.append(exc)

    def reading():
        try:
            getattr(primary, operation)(1)
        except BaseException as exc:
            reader_errors.append(exc)

    blocker_thread = threading.Thread(target=blocker)
    reader_thread = threading.Thread(target=reading)
    reader_started = False
    blocker_thread.start()
    try:
        assert started.wait(1)
        reader_thread.start()
        reader_started = True
        with actor._condition:
            assert actor._condition.wait_for(
                lambda: any(message.name == "reader_" + operation for message in actor._queue),
                timeout=1,
            ), "reader work must already be queued behind the unrelated owner call"
        result = primary.close(deadline=time.monotonic())
        assert result.state == "stopping" and result.pending_leases == 1
        with actor._condition:
            assert primary._lease.state == "closing"
        assert not release.is_set() and exits == []
        release.set()
        blocker_thread.join(1.2)
        reader_thread.join(1.2)
        assert not blocker_thread.is_alive() and not reader_thread.is_alive()
        assert blocker_errors == []
        assert len(reader_errors) == 1
        assert isinstance(reader_errors[0], ValueError), (
            "an admitted but closed reader must return a local closed-reader refusal, "
            f"not {type(reader_errors[0]).__name__}"
        )
        assert "closed" in str(reader_errors[0]).lower()
        assert backend_calls == [], "unstarted read/seek must not reach backend after close"
        assert [path for path, _ in exits] == ["primary.bin"]
        assert not any(name == "unload" for name, _ in events)
        assert actor.get_run(RUN) == {"uuid": RUN}
        assert other.read(1) == b"d"
        assert backend_calls == [("independent.bin", "read")]
        completed = primary.close()
        assert (completed.state, completed.pending_operations, completed.pending_leases) == (
            "completed",
            0,
            0,
        )
        assert other.close().state == "completed"
        assert [path for path, _ in exits] == ["primary.bin", "independent.bin"]
        assert all(owner != threading.get_ident() for _, owner in exits)
        assert actor.close(deadline=time.monotonic() + 1).state == "completed"
        assert [name for name, _ in events] == ["load", "unload"]
    finally:
        release.set()
        blocker_thread.join(1.2)
        if reader_started:
            reader_thread.join(1.2)
        # Pass a fixture exception triple so an already failed owner's deferred
        # close does not replace the distinguishing regression assertion.
        fixture_error = RuntimeError("negative fixture cleanup")
        primary_context.__exit__(RuntimeError, fixture_error, None)
        other_context.__exit__(RuntimeError, fixture_error, None)
        actor.close(deadline=time.monotonic() + 1)
        try:
            actor._owner_future.result(timeout=1)
        except KeyError:
            # Preserve the original assertion above; this is the very owner
            # crash the regression rejects, never a product-success condition.
            pass
        assert not blocker_thread.is_alive()
        assert not reader_started or not reader_thread.is_alive()
