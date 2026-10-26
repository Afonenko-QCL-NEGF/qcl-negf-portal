"""One AiiDA owner with bounded caller waits and explicit effect uncertainty."""

import asyncio
import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from importlib import import_module
from typing import Any, Literal
from uuid import UUID, uuid4

from .settings import ACTOR_DEFAULTS, Settings

LOG = logging.getLogger(__name__)
OWNER_GUARD = threading.Lock()
CARD_OPERATIONS = frozenset({
    "create_research_card", "edit_research_card", "list_research_cards",
    "get_research_card", "list_research_card_versions", "read_research_card_version",
    "attach_research_card_run", "list_research_card_runs",
    "attach_research_card_report", "list_research_card_reports", "get_research_card_request",
})
MUTATIONS = frozenset({"submit_plan", "kill_run", "save_agent_report",
    "create_research_card", "edit_research_card", "attach_research_card_run",
    "attach_research_card_report"})
OPERATIONS = CARD_OPERATIONS | frozenset(
    {
        "list_recovery_executions",
        "get_recovery_execution",
        "list_runs",
        "get_run",
        "get_model_help_input",
        "get_export_plan",
        "get_export_selection",
        "get_saved_sources",
        "get_saved_source",
        "get_live_history_source",
        "get_performance_source",
        "list_execution_artifacts",
        "submit_plan",
        "get_run_report",
        "save_agent_report",
        "list_agent_reports",
        "read_agent_report",
        "kill_run",
        "list_artifacts",
        "get_artifact_metadata",
    }
)
ActorState = Literal["initializing", "ready", "blocked", "stopping", "closed", "failed"]
EffectOutcome = Literal["not_started", "returned", "unknown"]
OperationState = Literal["queued", "running", "returned", "expired", "raised"]
CloseState = Literal["completed", "stopping", "failed"]
ActorErrorCode = Literal[
    "initialization_deadline",
    "operation_deadline",
    "queue_full",
    "lease_limit",
    "actor_initializing",
    "actor_blocked",
    "actor_stopping",
    "actor_closed",
    "actor_failed",
    "owner_unavailable",
    "mutation_outcome_unknown",
    "mutation_unresolved",
    "lease_cleanup_failed",
    "lease_cleanup_pending",
]


@dataclass(frozen=True)
class CloseResult:
    state: CloseState
    pending_operations: int | None
    pending_leases: int | None
    error_code: str | None = None


@dataclass(frozen=True)
class ActorReceipt:
    operation_id: str
    operation: str
    outcome: EffectOutcome
    actor_state: ActorState
    run_uuid: str | None = None
    calcjob_uuid: str | None = None
    attempt: int | None = None
    unresolved_operation_id: str | None = None


class ActorUnavailable(RuntimeError):
    def __init__(
        self,
        code: ActorErrorCode,
        receipt: ActorReceipt,
        *,
        backend_status: Literal[404, 422, 503] | None = None,
    ):
        super().__init__(code)
        self.code, self.receipt, self.backend_status = code, receipt, backend_status


class ActorDeadline(ActorUnavailable):
    """Response deadline expired; the receipt distinguishes dispatch from expiry."""


class ActorOutcomeUnknown(ActorUnavailable):
    """A dispatched mutation has no known successful response."""


@dataclass
class _Message:
    name: str
    args: tuple
    kwargs: dict
    deadline: float
    operation_id: str = field(default_factory=lambda: str(uuid4()))
    state: OperationState = "queued"
    future: Future = field(default_factory=Future)
    value: Any = None
    error: BaseException | None = None
    lease_id: str | None = None
    abandoned: bool = False


@dataclass
class _Lease:
    identifier: str
    state: str = "acquiring"
    context: Any = None
    handle: Any = None
    outstanding: bool = False
    exit_started: bool = False
    exception: tuple = (None, None, None)
    error: BaseException | None = None


def _uuid(value):
    try:
        return value if isinstance(value, str) and str(UUID(value)) == value else None
    except ValueError:
        return None


class _ArtifactReader:
    def __init__(self, actor: AiidaService, lease: _Lease) -> None:
        self._actor, self._lease = actor, lease

    def read(self, size: int) -> bytes:
        if type(size) is not int or not 0 <= size <= 65536:
            raise ValueError("Artifact reads must use chunks of at most 65536 bytes")
        return self._actor._reader_call(self._lease, "read", size)

    def seek(self, offset: int) -> int:
        if type(offset) is not int or offset < 0:
            raise ValueError("Artifact seek requires a nonnegative byte offset")
        return self._actor._reader_call(self._lease, "seek", offset)

    def close(self, *, deadline: float | None = None) -> CloseResult:
        return self._actor._release(self._lease, deadline=deadline)


class AiidaService:
    """The executor owns one mailbox consumer, never an unbounded task per call."""

    def __init__(
        self,
        profile: str,
        *,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._policy = {
            name: getattr(settings, name) if settings is not None else value
            for name, value in ACTOR_DEFAULTS.items()
        }
        self._condition = threading.Condition()
        self._state: ActorState = "initializing"
        self._queue = deque()
        self._running: _Message | None = None
        self._leases: dict[str, _Lease] = {}
        self._mutation_receipt: ActorReceipt | None = None
        self._terminal: CloseResult | None = None
        self._init_done = Future()
        self._init_error = None
        self._cleanup_error = None
        self._events = deque(maxlen=256)
        if not OWNER_GUARD.acquire(blocking=False):
            raise ActorUnavailable(
                "owner_unavailable",
                self._receipt(_Message("initialize", (), {}, self._clock()), "not_started"),
            )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qcl-negf-aiida")
        try:
            self._owner_future = self._executor.submit(self._owner_main, profile)
            self._owner_future.add_done_callback(self._owner_returned)
        except BaseException:
            OWNER_GUARD.release()
            raise
        deadline = self._clock() + self._policy["actor_initialize_timeout_seconds"]
        try:
            self._init_done.result(timeout=max(0, deadline - self._clock()))
        except TimeoutError:
            with self._condition:
                if not self._init_done.done():
                    self._state = "stopping"
                    self._condition.notify_all()
                    raise ActorDeadline(
                        "initialization_deadline",
                        self._receipt(_Message("initialize", (), {}, deadline), "unknown"),
                    ) from None
        if self._init_error is not None:
            raise self._init_error

    def _receipt(self, message: _Message, outcome: EffectOutcome) -> ActorReceipt:
        run = (
            (_uuid(message.value.get("uuid")) if isinstance(message.value, dict) else None)
            if message.name == "submit_plan"
            else (_uuid(message.args[0]) if message.args else None)
        )
        card_operation = message.name in CARD_OPERATIONS
        if card_operation:
            run = None
        return ActorReceipt(
            message.operation_id,
            message.name,
            outcome,
            self._state,
            run,
            None if card_operation else _uuid(message.kwargs.get("calcjob_uuid")),
            None if card_operation else message.kwargs.get("attempt"),
            self._mutation_receipt.operation_id if self._mutation_receipt is not None else None,
        )

    def _event(self, phase: str, message: _Message, outcome: EffectOutcome):
        # Caller paths only append bounded scalar metadata. Logging I/O is owner-only.
        report_uuid = (
            _uuid(message.value.get("uuid"))
            if message.name == "save_agent_report" and isinstance(message.value, dict)
            else None
        )
        self._events.append((phase, self._clock(), self._receipt(message, outcome), report_uuid))

    def _flush_events(self):
        with self._condition:
            events = list(self._events)
            self._events.clear()
        for phase, timestamp, receipt, report_uuid in events:
            LOG.info(
                "AiiDA actor %s time=%s operation=%s name=%s outcome=%s "
                "state=%s run=%s child=%s attempt=%s report=%s",
                phase,
                timestamp,
                receipt.operation_id,
                receipt.operation,
                receipt.outcome,
                receipt.actor_state,
                receipt.run_uuid,
                receipt.calcjob_uuid,
                receipt.attempt,
                report_uuid,
            )

    @staticmethod
    def _signal(messages):
        # Never run Future callbacks under the metadata lock.
        for message in messages:
            if not message.future.done():
                message.future.set_result(None)

    def _drain(self):
        drained = list(self._queue)
        self._queue.clear()
        for message in drained:
            message.state = "expired"
            message.error = ActorDeadline(
                "operation_deadline", self._receipt(message, "not_started")
            )
            self._expire_lease(message)
        return drained

    def _expire_lease(self, message):
        if message.lease_id is None:
            return
        lease = self._leases[message.lease_id]
        # Abandoning a running caller does not complete its backend operation.
        # The owner clears this flag only after the actual read/seek returns.
        if message.state != "running":
            lease.outstanding = False
        if lease.state == "acquiring" and message.state == "expired":
            lease.state = "closed"
            self._leases.pop(lease.identifier, None)
        elif lease.state not in {"closed", "failed"}:
            lease.state = "closing"

    def _admit(self, name, args, kwargs, *, lease=None):
        timeout = self._policy[
            "actor_io_timeout_seconds" if lease is not None else "actor_call_timeout_seconds"
        ]
        message = _Message(
            name,
            args,
            kwargs,
            self._clock() + timeout,
            lease_id=lease.identifier if lease is not None else None,
        )
        with self._condition:
            if (
                lease is not None
                and name != "artifact_enter"
                and (lease.state != "open" or lease.outstanding)
            ):
                raise ValueError("Artifact reader is closed or has an outstanding operation")
            if name in MUTATIONS and self._mutation_receipt is not None:
                raise ActorUnavailable("mutation_unresolved", self._receipt(message, "not_started"))
            if self._state != "ready":
                raise ActorUnavailable(
                    "actor_" + self._state, self._receipt(message, "not_started")
                )
            if (
                len(self._queue) + (self._running is not None)
                >= self._policy["actor_max_pending_calls"]
            ):
                raise ActorUnavailable("queue_full", self._receipt(message, "not_started"))
            if lease is not None:
                if name == "artifact_enter":
                    if (
                        sum(
                            item.state not in {"closed", "failed"} for item in self._leases.values()
                        )
                        >= self._policy["actor_max_open_artifacts"]
                    ):
                        raise ActorUnavailable("lease_limit", self._receipt(message, "not_started"))
                    self._leases[lease.identifier] = lease
                else:
                    if lease.state != "open" or lease.outstanding:
                        raise ValueError(
                            "Artifact reader is closed or has an outstanding operation"
                        )
                    lease.outstanding = True
            self._queue.append(message)
            self._event("admitted", message, "not_started")
            self._condition.notify_all()
        return self._wait(message)

    def _wait(self, message):
        try:
            message.future.result(timeout=max(0, message.deadline - self._clock()))
        except TimeoutError:
            pass
        drained = []
        with self._condition:
            if message.state not in {"returned", "raised", "expired"}:
                message.abandoned = True
                if message.state == "queued":
                    self._queue.remove(message)
                    message.state = "expired"
                    outcome = "not_started"
                else:
                    if self._state != "stopping":
                        self._state = "blocked"
                    outcome = "unknown"
                    drained = self._drain()
                self._expire_lease(message)
                receipt = self._receipt(message, outcome)
                if message.name in MUTATIONS and outcome == "unknown":
                    self._mutation_receipt = self._mutation_receipt or receipt
                    message.error = ActorOutcomeUnknown("operation_deadline", receipt)
                else:
                    message.error = ActorDeadline("operation_deadline", receipt)
                self._event("deadline", message, outcome)
                self._condition.notify_all()
            error, value = message.error, message.value
        self._signal(drained)
        if error is not None:
            raise error
        return value

    def __getattr__(self, name):
        if name not in OPERATIONS:
            raise AttributeError(name)
        return partial(self._call, name)

    def _call(self, name: str, *args: Any, **kwargs: Any):
        if name not in OPERATIONS:
            raise AttributeError(name)
        return self._admit(name, args, kwargs)

    def _reader_call(self, lease, name, value):
        return self._admit("reader_" + name, (value,), {}, lease=lease)

    @contextmanager
    def open_artifact(self, identifier, execution_id, path, *, attempt=None, calcjob_uuid=None,
                      close_observer: Callable[[CloseResult], None] | None = None):
        lease = _Lease(str(uuid4()))
        try:
            reader = self._admit(
                "artifact_enter",
                (identifier, execution_id, path),
                {"attempt": attempt, "calcjob_uuid": calcjob_uuid},
                lease=lease,
            )
        except ActorUnavailable as error:
            if close_observer is not None and error.receipt.outcome == "unknown":
                close_observer(CloseResult("stopping", None, 1, "lease_cleanup_pending"))
            raise
        try:
            yield reader
        finally:
            exception = sys.exc_info()
            result = self._release(lease, exception=exception)
            if close_observer is not None:
                close_observer(result)
            if exception[0] is None and result.state != "completed":
                message = _Message("artifact_exit", (), {}, self._clock())
                cls = ActorDeadline if result.state == "stopping" else ActorUnavailable
                code = (
                    "lease_cleanup_pending"
                    if result.state == "stopping"
                    else "lease_cleanup_failed"
                )
                raise cls(code, self._receipt(message, "unknown"))

    def _deadline(self, deadline):
        local = self._clock() + self._policy["actor_close_timeout_seconds"]
        return local if deadline is None else min(local, deadline)

    def _release(self, lease, *, deadline=None, exception=(None, None, None)):
        deadline = self._deadline(deadline)
        with self._condition:
            if lease.state not in {"closed", "failed", "closing"}:
                lease.state = "closing"
                lease.exception = exception
            # Resolve queued work before priority cleanup can remove its lease.
            # A dispatch that already won this lock remains outstanding until
            # its backend returns; a queued call has never touched the handle.
            cancelled = [message for message in self._queue if message.lease_id == lease.identifier]
            for message in cancelled:
                self._queue.remove(message)
                message.state = "expired"
                message.error = ValueError("Artifact reader is closed")
                self._expire_lease(message)
            self._condition.notify_all()
        self._signal(cancelled)
        with self._condition:
            while lease.state not in {"closed", "failed"}:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return CloseResult(
                        "stopping", int(lease.outstanding), 1, "lease_cleanup_pending"
                    )
                self._condition.wait(remaining)
            return CloseResult(
                "failed" if lease.error is not None else "completed",
                0,
                0,
                "lease_cleanup_failed" if lease.error is not None else None,
            )

    def close(self, *, deadline=None):
        deadline = self._deadline(deadline)
        with self._condition:
            if self._terminal is not None:
                return self._terminal
            self._state = "stopping"
            drained = self._drain()
            for lease in self._leases.values():
                if lease.state not in {"closed", "failed"}:
                    lease.state = "closing"
            self._condition.notify_all()
        self._signal(drained)
        with self._condition:
            while self._terminal is None:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return CloseResult(
                        "stopping",
                        int(self._running is not None),
                        sum(
                            lease.state not in {"closed", "failed"}
                            for lease in self._leases.values()
                        ),
                    )
                self._condition.wait(remaining)
            return self._terminal

    def _execute(self, message):
        lease = self._leases.get(message.lease_id)
        if message.name == "artifact_enter":
            context = self._service.open_artifact(*message.args, **message.kwargs)
            handle = context.__enter__()
            with self._condition:
                lease.context, lease.handle = context, handle
                if lease.state == "acquiring":
                    lease.state = "open"
            return _ArtifactReader(self, lease)
        if message.name.startswith("reader_"):
            return getattr(lease.handle, message.name.removeprefix("reader_"))(*message.args)
        if message.name in {"get_saved_sources", "get_saved_source", "get_live_history_source", "get_performance_source"}:
            operation = getattr(self._service, message.name, None)
            if not callable(operation):
                from .saved_results import SavedResultError
                raise SavedResultError("unsupported_contract")
            return operation(*message.args, **message.kwargs)
        return getattr(self._service, message.name)(*message.args, **message.kwargs)

    def _exit(self, lease):
        try:
            if lease.context is not None:
                lease.context.__exit__(*lease.exception)
        except BaseException as exc:
            lease.error = exc
            self._cleanup_error = self._cleanup_error or "lease_cleanup_failed"
        finally:
            with self._condition:
                drained = []
                if lease.error is not None:
                    self._state = "stopping"
                    drained = self._drain()
                    for other in self._leases.values():
                        if other.state not in {"closed", "failed"}:
                            other.state = "closing"
                lease.state = "failed" if lease.error is not None else "closed"
                lease.context = lease.handle = None
                lease.exception = (None, None, None)
                self._leases.pop(lease.identifier, None)
                self._condition.notify_all()
            self._signal(drained)
            LOG.info("AiiDA actor lease-exit lease=%s state=%s", lease.identifier, lease.state)

    def _owner_main(self, profile):
        loop = None
        initialized = False
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            from aiida import load_profile

            load_profile(profile)
            self._service = import_module("aiida_qcl_negf.service")
            initialized = True
        except BaseException as exc:
            self._init_error = exc
        with self._condition:
            if self._init_error is not None:
                self._state = "stopping"
            elif self._state == "initializing":
                self._state = "ready"
        self._init_done.set_result(None)
        try:
            while True:
                self._flush_events()
                with self._condition:
                    lease = next(
                        (
                            lease
                            for lease in self._leases.values()
                            if lease.state == "closing" and not lease.exit_started
                        ),
                        None,
                    )
                    if lease is not None:
                        lease.exit_started = True
                        message = None
                    elif self._state == "stopping":
                        break
                    elif self._queue:
                        message = self._queue.popleft()
                        # Admission can precede another mutation's unknown
                        # effect. Recheck the fence atomically at dispatch.
                        if message.name in MUTATIONS and self._mutation_receipt is not None:
                            message.state = "expired"
                            message.error = ActorUnavailable(
                                "mutation_unresolved", self._receipt(message, "not_started")
                            )
                        elif message.deadline <= self._clock():
                            message.state = "expired"
                            self._expire_lease(message)
                            message.error = ActorDeadline(
                                "operation_deadline", self._receipt(message, "not_started")
                            )
                        else:
                            message.state = "running"
                            self._running = message
                            self._event("started", message, "unknown")
                    else:
                        if self._state == "blocked":
                            self._state = "ready"
                            self._condition.notify_all()
                        self._condition.wait()
                        continue
                if lease is not None:
                    self._exit(lease)
                    continue
                if message.state == "expired":
                    self._signal([message])
                    continue
                value, error = None, None
                try:
                    value = self._execute(message)
                except BaseException as exc:
                    error = exc
                with self._condition:
                    self._running = None
                    if message.lease_id is not None:
                        lease = self._leases[message.lease_id]
                        lease.outstanding = False
                        if error is not None:
                            if message.name == "artifact_enter" and lease.handle is None:
                                lease.state = "closed"
                                self._leases.pop(lease.identifier, None)
                            else:
                                lease.state = "closing"
                    if error is not None and message.name in MUTATIONS:
                        status = (
                            404
                            if isinstance(error, LookupError)
                            else 422
                            if isinstance(error, ValueError)
                            else 503
                        )
                        receipt = self._receipt(message, "unknown")
                        self._mutation_receipt = self._mutation_receipt or receipt
                        wrapped = ActorOutcomeUnknown(
                            "mutation_outcome_unknown", receipt, backend_status=status
                        )
                        wrapped.__cause__ = error
                        error = wrapped
                    message.value = value
                    if not message.abandoned:
                        message.error = error
                    message.state = "raised" if error is not None else "returned"
                    self._event(message.state, message, "unknown" if error else "returned")
                    self._condition.notify_all()
                self._signal([message])
        finally:
            # All phases remain owner-only, attempted once even after a prior failure.
            if loop is not None:
                from aiida.manage import get_manager

                for phase in (
                    lambda: get_manager().unload_profile(),
                    lambda: loop.run_until_complete(loop.shutdown_asyncgens()),
                    loop.close,
                    lambda: asyncio.set_event_loop(None),
                ):
                    try:
                        phase()
                    except BaseException:
                        self._cleanup_error = self._cleanup_error or "actor_failed"
            with self._condition:
                failed = self._cleanup_error is not None or not initialized
                self._state = "failed" if failed else "closed"
                self._final_result = CloseResult(
                    "failed" if failed else "completed",
                    0,
                    0,
                    self._cleanup_error or ("actor_failed" if failed else None),
                )

    def _owner_returned(self, future):
        if self._cleanup_error is None and future.exception() is None:
            OWNER_GUARD.release()
        self._executor.shutdown(wait=False, cancel_futures=True)
        with self._condition:
            self._terminal = (
                self._final_result
                if future.exception() is None
                else CloseResult("failed", 0, 0, "actor_failed")
            )
            self._condition.notify_all()
