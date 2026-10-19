# Actor lifecycle: bounded responses and truthful cleanup

**Статус: принятый целевой контракт для реализации и fault-проверок.**
Документ задаёт поведение actor, HTTP adapter и временного export store.
Наличие описанных механизмов в конкретной версии определяется её исходниками
и проверками; design acceptance само по себе не является runtime pass.

## 1. Область и baseline

Baseline для source observations ниже — Portal
`6ff9f6100623c578028506d43a39972dbfbc00dc` и AiiDA
`5579d55684939495230241f446b7ea07c8ae0224`. Слова current/existing при описании
исходного поведения относятся к этому baseline. Целевые types, settings,
deadlines и cleanup seam задают контракт следующей реализации, а не утверждают
их наличие в baseline. Проверки раздела12 остаются требованиями до фактического
выполнения на точном implementation source.

Цель — конечное ожидание вызывающего потока, сохранение единственного AiiDA
profile/thread/loop owner и точное различение not-started, returned и unknown.
Зависший backend может продолжать жить после ответа и после lifespan cleanup.
Контракт сохраняет physics, solver, SCBA/Poisson, frozen plan bytes, scientific
status, Slurm retry и существующее разделение владельцев компонентов.
Process completion, convergence и scientific acceptance остаются разными
утверждениями. Веб-интерфейс сохраняет фиксированную светлую тему.

## 2. Наблюдение и выбор architecture

Current `service.py::AiidaService` сериализует профиль, ORM и handles, но каждый
`Future.result()` и final `executor.shutdown(wait=True)` не имеют deadline.
`exports.py::ExportStore.close` также ждёт worker; cooperative callback не
прерывает backend read. Это source inference, а не наблюдённый production hang.

Рассмотрены три варианта:

| Вариант | Свойства и цена | Решение контракта |
| --- | --- | --- |
| Только добавить timeout к существующим submit/result | Caller получает ответ, но executor queue и late-open cleanup остаются неуправляемыми | недостаточно |
| Один owner, bounded mailbox, bounded caller waits, lease registry и stopping drain | Сохраняет ownership; даёт обозримую границу admission/late cleanup; backend termination не обещает | выбран |
| Отдельный процесс/supervisor | Может дать отдельный kill/exit contract, требует IPC и иной recovery policy | отдельный design при необходимости process-exit guarantee |

Реализуемая форма выбранного варианта: сохранить `ThreadPoolExecutor(max_workers=1)`
как единственный owner; отправить в него один долгоживущий `_owner_main`.
Только он выполняет initialize, выбирает сообщения bounded deque, выполняет
backend операции и cleanup. Не подавать по одной операции в скрытую безлимитную
очередь executor. `Future` каждого сообщения служит только для ответа.
`Condition`/короткий lock защищают metadata, admission, deadline/start race;
никакой backend/ORM/I/O/logger call не выполняется под этим lock.

Управляющие действия — flags/lease state в bounded registry, а не новые
неограниченные cleanup tasks. На каждой итерации owner сначала завершает
requested lease exits, затем проверяет STOPPING, затем выбирает user message.
Блокировка owner внутри backend остаётся явно возможной.

## 3. Инварианты

1. Load/unload profile, asyncio loop, ORM, repository enter/read/seek/exit —
   только owner thread. HTTP/export threads получают bytes/scalars и proxy.
2. Для каждого успешно entered context ровно одна **попытка** `__exit__`.
   Исключение из exit не превращается в «handle closed»; cleanup failed.
3. Start/not-started определяется одной lock-атомарной точкой: owner проверяет
   deadline/state и переводит запись queued→running перед backend call.
   По ту сторону этой точки timeout не доказывает отсутствие effect.
4. Timeout/HTTP disconnect не выполняет backend mutation повторно и не вызывает
   `kill_run` как cleanup. Существующие retry/max_attempts не меняются.
5. `completed` close возможен только после всех lease exits, unload, shutdown
   async generators и loop close без ошибки. `stopping` не successful close.
6. Unresolved mutation не разрешает новый provenance submit. В текущем
   процессе закрывается admission всех трёх mutation operations; reads могут
   возобновиться после возврата owner. Никакого automatic reset/retry нет.
7. Late result не меняет уже отправленный HTTP response и не публикует ready
   export после cancellation/stopping. Cleanup не удаляет leased files.

## 4. Целевые Python types и интерфейсы

Имена ниже задают принятый целевой контракт; их наличие в baseline implementation не утверждается.
Types принадлежат `service.py`; отдельный package/module не нужен.

```python
ActorState = Literal["initializing", "ready", "blocked", "stopping", "closed", "failed"]
EffectOutcome = Literal["not_started", "returned", "unknown"]
OperationState = Literal["queued", "running", "returned", "expired", "raised"]
CloseState = Literal["completed", "stopping", "failed"]
ActorErrorCode = Literal[
    "initialization_deadline", "operation_deadline", "queue_full", "lease_limit",
    "actor_initializing", "actor_blocked", "actor_stopping", "actor_closed",
    "actor_failed", "owner_unavailable", "mutation_outcome_unknown",
    "mutation_unresolved", "lease_cleanup_failed", "lease_cleanup_pending",
]

@dataclass(frozen=True)
class CloseResult:
    state: CloseState
    pending_operations: int | None
    pending_leases: int | None
    error_code: str | None = None

@dataclass(frozen=True)
class ActorReceipt:
    operation_id: str       # server-generated canonical UUID4, before admission
    operation: str          # fixed allowlist name, no client-controlled prose
    outcome: EffectOutcome
    actor_state: ActorState
    run_uuid: str | None    # exact known locator only, never guessed
    calcjob_uuid: str | None
    attempt: int | None
    unresolved_operation_id: str | None  # original unknown mutation on fence refusal

class ActorUnavailable(RuntimeError):
    code: ActorErrorCode
    receipt: ActorReceipt
    backend_status: Literal[404, 422, 503] | None

class ActorDeadline(ActorUnavailable):
    """Configured response deadline expired; receipt defines start boundary."""

class ActorOutcomeUnknown(ActorUnavailable):
    """A started mutation did not provide a known successful receipt."""
```

`ActorReceipt` — compact diagnostic metadata, не scientific/provenance receipt.
`returned` означает «backend call вернул value», а не convergence/job stopped.
Failed backend call после начала mutation даёт `unknown`, не `returned`.
Exact exception constructor для всех трёх inherited classes:
`(code: ActorErrorCode, receipt: ActorReceipt, *, backend_status:
Literal[404, 422, 503] | None = None)`. Cause передаётся обычным `raise ... from exc`.
Init timeout использует initialization_deadline; user response timeout —
operation_deadline; expired pending и started timeout имеют разные receipt outcomes.
State refusals используют actor_STATE, second-owner refusal owner_unavailable.
Коды вне fixed enum не добавлять implementation guessing.
Actor close/reader close всегда возвращают exact integer counts. Только export
close может вернуть None для недоступного под deadline metadata snapshot (§10.1).
None означает «не наблюдалось», никогда не ноль; completed всегда имеет оба нуля.

Exact constructor:
`AiidaService(profile: str, *, settings: Settings | None = None,
clock: Callable[[], float] = time.monotonic)`.
`settings=None` использует те же dataclass operational defaults, без чтения env,
token files или нового Settings instance с фиктивными credentials; вынести
константы defaults в `settings.py`, читать их в actor при None.
Production API передаёт уже validated `settings=settings`.
Existing `AiidaService("example")` для finite fixture сохраняется.

`_call(name, *args, **kwargs)` и registered public operation signatures
сохраняются; backend kwargs не получают clock/deadline. Reader read/seek bounds
0..65536 и nonnegative exact-int offset сохраняются до admission.
`close(*, deadline: float | None = None) -> CloseResult` добавляет optional
absolute monotonic deadline. При None — now + close timeout; supplied deadline
может только сократить этот local close wait. Repeated close не создаёт второй
unload. Existing callers, игнорирующие return, совместимы для healthy close.
`_ArtifactReader.close(*, deadline=None) -> CloseResult` — idempotent lease release.
`ExportStore.close(*, deadline=None) -> CloseResult` использует тот же type.

Base backend exceptions для обычных finite reads остаются исходными
`LookupError/ValueError/RuntimeError`; actor-specific exceptions ловятся в API
**до** общего RuntimeError. После started mutation backend exception сохраняется
как cause/категория, но оборачивается `ActorOutcomeUnknown` с conservative outcome.
Внутреннее поле `backend_status` у этого exception — строго `404 | 422 | 503`,
выводимое из original `LookupError | ValueError | прочее`; raw error не идёт в
headers/log. HTTP-side validation остаётся перед actor и не создаёт uncertainty.

## 5. Settings, source deadline и новые operational decisions

В current `settings.py` нет actor policy/deadlines. Следующие значения —
**новое operational решение этого контракта**, а не восстановленная политика,
научный tolerance или measured latency SLO. Секунды берутся с monotonic clock.

| Settings field / environment suffix после `QCL_NEGF_` | Default | Valid domain | Основание default |
| --- | ---: | --- | --- |
| `actor_initialize_timeout_seconds` / `ACTOR_INITIALIZE_TIMEOUT_SECONDS` | 30.0 s | finite float, 0 < x <= 300 | startup fail within finite admin interval; не backend latency proof |
| `actor_call_timeout_seconds` / `ACTOR_CALL_TIMEOUT_SECONDS` | 30.0 s | finite float, 0 < x <= 300 | existing AiiDA kill broker timeout10s получает запас; остальные metadata/mutation calls также finite |
| `actor_io_timeout_seconds` / `ACTOR_IO_TIMEOUT_SECONDS` | 15.0 s | finite float, 0 < x <= 300 | per-chunk/open/seek wait меньше generic call; размер chunk уже <=65536 |
| `actor_close_timeout_seconds` / `ACTOR_CLOSE_TIMEOUT_SECONDS` | 10.0 s | finite float, 0 < x <= 300 | finite shared lifespan/cleanup response interval; не unload guarantee |
| `actor_max_pending_calls` / `ACTOR_MAX_PENDING_CALLS` | 16 | exact int, 1..256, bool запрещён | bounded running+queued message count; существующий finite 8-call control помещается |
| `actor_max_open_artifacts` / `ACTOR_MAX_OPEN_ARTIFACTS` | 8 | exact int, 1..64, bool запрещён | ограничивает owned contexts и bounded deferred cleanup metadata |

Parsing environment: четыре time fields через float с explicit `isfinite`,
два counts через int из decimal env; dataclass direct values валидируются как
real finite number, bool запрещён. Ноль/negative/NaN/infinity/malformed — startup
ValueError. Незаданные env используют defaults; `from_environment` не пытается
разобрать эти time fields в существующем общем integer loop.
Верхние пределы — operational guards против случайного почти бесконечного wait/
queue, также новые решения этого контракта. Site может выбрать меньше/больше default
в пределах guard. Изменение guards требует owning review, а не physical decision.
HTTP client не может задавать deadlines или capacity. Тесты inject малые значения.

Deadline каждой операции `D = clock_at_admission + configured_timeout`;
includes queue time и backend wait, не обнуляется при dequeue/вложенном вызове.
Future wait использует только `max(0, D - clock())`. При пробуждении outcome
решается под metadata lock: если completion уже записан, вернуть его; иначе
если queued — expired/not_started; если running — unknown/blocked.
Системный scheduling delay сверх D возможен; это конечное программное ожидание,
не hard realtime bound. Каждое ожидание имеет finite cap и test witness.

## 6. Actor state и admission

| State | Admission | Переходы и cleanup |
| --- | --- | --- |
| `initializing` | domain calls не admitted; constructor ждёт init deadline | успешный init→ready; init exception→stopping cleanup→failed; init timeout caller raises deadline/unknown, state→stopping, late init сразу cleanup |
| `ready` | allowlisted calls при available message slot; enter также резервирует lease slot | running timeout→blocked; close→stopping; unrecoverable owner cleanup fault→failed |
| `blocked` | новые domain calls немедленно503, not_started; control lease close/actor close принимаются metadata-only | late running return/raise и cleanup →ready при отсутствии close; mutation fence остаётся; close→stopping |
| `stopping` | новые domain calls503/not_started; repeated close и lease release допустимы | cancel queued, finish current effect, exit leases, unload, close loop; затем closed либо failed |
| `closed` | domain calls503/not_started | close возвращает completed; никаких новых executor/backend calls |
| `failed` | domain calls503/not_started | close возвращает failed; повторной unsafe cleanup попытки нет; exact failure retained |

Capacity `actor_max_pending_calls` включает running + queued domain messages,
не lifecycle flags. Admission не ждёт свободного slot: full→503 code `queue_full`,
outcome `not_started`. Full lease registry→503 code `lease_limit`, также до enter.
Завершённые/expired queued records освобождают slot сразу; running timeout
удерживает свой slot до backend return. Pending queue не содержит orphan Futures.
Lease slot reserved **до** dispatch enter, released при enter error/not_started
или после single exit attempt; timeout open сохраняет reservation до late cleanup.

Если backend running timeout происходит при других queued messages, owner
metadata немедленно помечает их expired/not_started и сигналит их Futures;
они никогда не запускаются после late return. Это не просто Future.cancel:
owner проверяет tombstone/start под тем же lock. Cancellation pending не
отменяет running effect. Close выполняет тот же pending drain.

Один process-global owner guard не допускает второй `AiidaService`, пока первый
не завершил owner cleanup. Init timeout не освобождает этот guard. Guard release
только после actual owner return **и** known successful completion всех profile/
loop cleanup phases. Init failure с полностью successful cleanup может отпустить
guard; unload/loop cleanup failure оставляет guard closed до process restart.
Failed с незавершившимся owner guard также не освобождает. Это исключает параллельный
profile reload после constructor timeout. Несколько профилей/actors в одном
процессе этим contract не поддерживаются, как и текущая production topology.

## 7. Полная operation table

Read group R = `list_runs`, `get_run`, `get_export_plan`, `get_export_selection`,
`list_execution_artifacts`, `get_run_report`, `list_agent_reports`,
`read_agent_report`, `list_artifacts`, `get_artifact_metadata`.
Mutation group M = `submit_plan`, `kill_run`, `save_agent_report`.
`cancel` в постановке означает exact existing operation `kill_run`.
Других dynamic operations нет. Нельзя расширить allowlist через client name.

| Operation | Source deadline / phase | До start deadline/refusal | После start deadline/error | Finite success и close action |
| --- | --- | --- | --- | --- |
| initialize | init / owner load_profile + import_module + loop setup | domain admission закрыт | timeout→stopping; partial load cleanup остаётся на owner; constructor fail | ready, profile owner retained |
| R call | call / queue+backend | not_started; backend_count0 | timeout unknown value, blocked; finite backend error сохраняет category | вернуть прежние bytes/scalars/dict; no scientific reinterpretation |
| submit_plan | call / queue+backend | not_started; submit_count0 | timeout **или любой backend exception** unknown mutation, fence; никаких новых submit/kill/report writes | прежний202 body только после backend summary return; workflow may continue |
| kill_run | call / queue+backend | not_started; kill_count0 | timeout/error unknown cancellation; fence; no implied scheduler stop | прежний body отражает refreshed parent; не гарантирует child/Slurm/process stop |
| save_agent_report | call / queue+backend | not_started; store_count0 | timeout/error unknown store; fence; повторныйPOST может создать duplicate UUID и запрещён автоматически | прежний201 receipt; exact bytes сохранены |
| artifact enter | io / reserved lease+queue+context.__enter__ | not_started; release reservation | timeout retains acquiring lease, closes late returned context once; blocked | return proxy, no raw backend handle in caller |
| reader.read | io / queue+handle.read | not_started; timeout poisons this reader | timeout running discards late bytes, marks lease closing, blocked | bytes<=65536; backend error propagates and closes lease |
| reader.seek | io / queue+handle.seek | not_started; timeout poisons reader | timeout running makes position unusable; lease closing, blocked | existing nonnegative offset behavior |
| lease release/context exit | close / metadata mark + wait only | always accepted idempotently, no admission slot required | stopping result while effect blocked; failed if exit raises | completed only after single exit attempt succeeds; no second call |
| actor close | close / shared absolute deadline | set stopping atomically; cancel unstarted; mark all leases closing | deadline→CloseResult(stopping,...); backend can still be alive | completed only after all successful exits+unload+loop shutdown+loop close |

For mutation backend exceptions, class alone cannot establish which internal
effect occurred. Current AiiDA `submit_plan` calls `submit(...)` then `_summary`;
`save_agent_report` stores before receipt; `kill_run` can lose broker confirmation.
Portal has no authoritative effect-phase callback. Conservative uncertainty
also applies to a backend ValueError/LookupError after dispatch; this may fence
an operation that in reality failed validation. Safer availability refinement
needs a separate **typed AiiDA phase contract**, not an inferred class rule here.
HTTP-side known validation/auth rejection remains cheap and unfenced.

## 8. Unknown receipt, reconnect и mutation fence

Перед backend dispatch actor назначает operation UUID, monotonically ordered
sequence и captures known exact locator. Logger records bounded event metadata:
admitted/started/returned/raised/deadline/lease-exit/unload plus timestamp,
operation UUID/name, outcome, exact known run/CalcJob/attempt, counts. Tokens,
raw plans, report text, credentials, raw exception prose не логируются.
Для submit допускается SHA256 original frozen plan bytes и digest canonical
admission inputs; это correlation, **не unique provenance identity**.
Late known returned run/report UUID фиксируется тем же operation UUID.
Logs — operational diagnostics; durable commit/fsync этим design не доказан.

Running timeout или backend exception M устанавливает `mutation_uncertain=True`
и сохраняет первый compact ActorReceipt. Он не удаляется при late completion,
disconnect или обычном read. После late return actor может снова обслуживать R,
но все M немедленно503 `mutation_unresolved`; response ссылается на original op.
Ни connection refresh, ни status poll не запускают второй submit. Fence не имеет
HTTP reset endpoint, TTL или automatic restart/retry. Это сознательно fail-closed
для неоднозначных effects; normal finite successful mutation fence не ставит.

Reconnect workflow:

1. Сохранить returned diagnostic headers/detail и известные IDs; no automatic
   retry mutation, no assumption «503 значит не submitted».
2. Если run UUID известен, использовать existing GET run/report/artifact metadata
   с exact child selectors. Для report write известный anchor позволяет прочитать
   report receipts; одинаковый текст сам по себе не доказывает тот же store.
3. Пока actor blocked/stopping — read получает503; не создавать второй owner.
   После late return same known-run reads допустимы.
4. Для submit без известного run UUID нужна reconciliation AiiDA owner по
   retained operation diagnostics и exact provenance. Plan hash/label/«пустой
   list_runs сейчас» не доказывают absence/uniqueness и не разрешают resubmit.
   Если exact outcome не установлен, оставить unknown и mutation admission closed.
5. Успешная поздняя response receipt позволяет установить returned effect,
   но не снимает latch автоматически. Возобновление mutation допускается только
   после внешнего documented reconciliation и controlled service restart.
   Сам restart **не** является доказательством reconciliation; нерешённая
   uncertainty переносится в existing operator/coordination evidence.

Новый durable idempotency store/global exactly-once API не вводится. Процессовый
fence не сохраняется через crash. Автоматический restart/resubmit нельзя
использовать для обхода unresolved effects. Потеря HTTP response до получения
operation UUID также оставляет effect unknown; восстановление по догадке
не предлагается. Это explicit limitation current interface, а не exactly-once
claim. Hard cross-crash deduplication потребует отдельного owning design.

## 9. Artifact lease и streaming disconnect

Lease states exact: `acquiring → open → closing → closed | failed`.
Enter error до получения handle: release reservation, no exit. На успешном
enter owner регистрирует context/handle **до** публикации proxy/Future result.
Если deadline/close уже победил, caller proxy не получает: owner сразу выполняет
single exit. Late-open handle не теряется.

Каждый proxy связан с одним lease UUID. В open допускается не более одного
outstanding read/seek этого proxy; конкурентная операция этого же reader даёт
ValueError до queue. Closing/closed/failed reader read/seek дают ValueError
до backend. Queue expiry или running timeout read/seek делает lease closing:
нельзя повторить чтение с неизвестным offset. Другие независимые leases после
возврата owner могут продолжаться, если actor не stopping.

`reader.close` атомарно помечает closing, хранит first context exit exception
triple (если release от contextmanager) и сигналит owner. Это metadata-only,
не ждёт user queue slot и не исполняет handle.close на caller. Повторные release
сохраняют ту же cleanup Future; once-only attempt на owner. Exception triple
удаляется после exit, чтобы не удерживать traceback дольше lifecycle.
Owner не вызывает exit, пока его предшествующее read/seek не вернулось.

`open_artifact` contextmanager finally вызывает bounded reader.close. Если
cleanup deferred, пишет truthful stopping event и сохраняет owner-held lease;
original body/GeneratorExit exception не маскируется cleanup timeout. Если body
успешен, cleanup failure поднимается как ActorUnavailable(code="lease_cleanup_failed");
cleanup timeout как ActorDeadline(code="lease_cleanup_pending"). При уже идущем
stream это только abort/log, не новая HTTP JSON success/error body.

`ClosingStreamingResponse` сохраняет explicit generator close в shielded finally.
После in-flight next() actor read должен вернуть/timeout; close() затем имеет
свой bounded cleanup wait. Для disconnect at stalled **actor artifact read**
software wait ограничен remaining io budget + close budget, с scheduling caveat. Возврат ASGI
означает response cleanup requested; он **не** утверждает actual backend exit.
Healthy finite fixture по-прежнему требует handle closed перед response return.
Если response headers уже sent, timeout завершает/truncates body и логирует
event: Content-Length/ETag не подделываются, успешный полный download не заявляется.
Native range/resume остаётся отдельным новым GET с exact existing selectors.
Direct ready-export download читает local filesystem вне actor; arbitrary stuck
filesystem read не получает эту io bound. Его lease удерживается до actual
reader return, store close честно остаётся stopping. Deadline local-file streaming
и hard OS I/O termination требуют другого design, не подразумеваются здесь.

## 10. Export lease и общий close

Preparation worker не владеет ORM; artifact proxies подчиняются разделу9.
ExportStore получает states `open → stopping → closed | failed`.
New `create`/new `open` при stopping/closed отклоняются ExportBusy409; `get`
разрешён для уже существующих metadata до teardown. Ongoing ready-download
lease продолжает держать local archive; global cleanup не удаляет его файл.
Expiration cleanup также требует readers==0, как existing path.

`ExportStore.close(deadline=...)` устанавливает stopping/cancel через отдельный
короткий lifecycle control lock, **не** через существующий store RLock; точная
deadline-aware admission и deferred cleanup определены в §10.1. Он отменяет
не начатую preparation Future и ждёт только remaining close budget.
Нельзя звать blocking `executor.shutdown(wait=True)`.
Return completed только когда worker завершён, readers==0, temporary cleanup
завершён без ошибки; stopping если worker/reader/cleanup ещё жив; failed при
известной cleanup ошибке. Repeated close возвращает shared current state.

Preparation проверяет cancel перед новым actor call и в existing callbacks.
Когда stalled actor read timeout вернулся, preparation unwinds its artifact
context; output directory удаляется только после ухода локального writer;
late backend bytes discarded. Interrupted preparation existing entry→failed,
error string explicit cancellation/unavailable; `reserved` освобождается после
фактического directory cleanup, не при merely requested cancellation.
No ready publish после stopping: последний `entry.update(state="ready")`
атомарно повторно проверяет store state/cancel flag под lock. Все существующие exact-child
selectors, pinned status/cache identity и spool/reserve/receipt checks сохраняются.

После timeout close object/root directory остаются живы до worker done и
last reader release. Once-only deferred finalization выполняется при выполнении
обоих условий. Не полагаться на GC/TemporaryDirectory finalizer для своевременного
удаления: automatic finalizer не должен удалить дерево с active lease. Если
filesystem removal сама блокируется, close остаётся stopping/unknown; cleanup
не переносится в HTTP/lifespan поток. Удаление идёт на existing preparation
worker/finalizer path с retained object и zero active leases. Никакого обещания
prompt filesystem cleanup при зависшем OS I/O.

Lifespan вычисляет **один** D=monotonic()+actor_close_timeout_seconds, вызывает
`exports.close(deadline=D)`, затем `service.close(deadline=D)` в finally,
даже если первый close failed/raised. Service.close немедленно ставит stopping
при исчерпанном D и возвращает stopping, не пропускается. Закрытие actor после
export close request допустимо: outstanding reads уже owned, новые refused,
late lease exits остаются на owner. Injected test service не получает forced
close сверх existing service ownership; actual owned actor закрывает lifespan.

Owner closing: cancel user mailbox, mark all owned leases closing, после
текущей backend операции single exit каждого lease, unload_profile, shutdown
async generators, loop.close, asyncio.set_event_loop(None). Errors сохраняются,
остальные безопасные cleanup phases всё равно attempted на owner; successful
close при любой ошибке не возвращается. `executor.shutdown(wait=False,
cancel_futures=True)` вызывается после terminal owner return; ни одного
wait=True в caller path. Persistent stuck backend может удерживать executor/
Python process даже после returned stopping. Thread kill/force unload не вводятся.

### 10.1. Store lock admission и coalesced filesystem cleanup

Source seam: current `exports.py::_storage`67–77 вызывает `disk_usage` под
`self._lock`; `create`154–181 также проверяет free space под этим RLock;
`_cleanup`50–56 делает `rmtree` из `get`79–85 и `create`154–155. Существующий
RLock поэтому **не** считается bounded metadata lock. Не опираться на prompt
acquire этого lock перед установкой cancel/stopping или ответом close.

Выбран следующий вариант: **deadline-aware admission к store
RLock + truthful unknown counts**, с переносом удаления на existing worker.
`disk_usage` в `_storage`/create сохраняет существующую сериализацию storage
admission; его блокировка не должна задерживать lifecycle control. Полный перенос
free-space admission в отдельный asynchronous API не входит в исправление.
Обычный create/storage caller может ждать filesystem read; native filesystem
responsiveness не заявляется. Close caller остаётся ограничен своим deadline.

Добавить один `_lifecycle_lock`/Condition только для metadata:
`close_requested`, `maintenance_scheduled`, `maintenance_running`, `maintenance_dirty`, terminal
`CloseResult` и preparation Future reference. Под ним запрещены store RLock
acquire, filesystem/backend/logger calls, Future.result/shutdown и ожидание
readers/worker. Если нужны оба lock, разрешён только порядок store RLock →
lifecycle lock для короткой регистрации Future/состояния. Обратный порядок
запрещён: close отпускает lifecycle lock **до** timed acquire store RLock.
Logger events не писать синхронно
на finite-response path; хранить compact metadata и передавать existing worker.

Exact close algorithm, repeated callers используют то же terminal state/task:

0. Под lifecycle lock проверить terminal result. Если он уже completed/failed,
   вернуть его без нового submit/cancel/cleanup и без обращения к shutdown executor.
1. Вычислить local D по §4; если supplied D уже истёк, всё равно немедленно
   set `_cancelled` и `close_requested` под lifecycle control, но не ждать.
   Это independent stop latch, видимый worker/caller до store RLock.
2. Cancel pending preparation Future metadata-only; running Future не отменять.
   Request единственный coalesced maintenance task (ниже), не уничтожать
   executor. При уже scheduled/running task лишь поднять close flag, без нового
   submit. Mutex не удерживается во время submit; флаг scheduled резервируется
   атомарно до submit, submit failure записывает failed result под lifecycle lock.
3. Для optional exact счётчиков попытаться `self._lock.acquire(timeout=max(0,
   D-clock()))`, а при remaining0 — `acquire(blocking=False)`. Успешный acquire
   только читает preparation/reader counts и release сразу, без `_cleanup`,
   disk_usage, stat или delete. Не ждать повторно при failure. Если acquire не
   состоялся, оба counts=None, `error_code="store_metadata_pending"`.
4. Если lifecycle terminal result уже опубликован worker, вернуть его. Иначе
   ждать его Condition/Future только оставшееся `max(0,D-clock())`; не ждать
   RLock, executor shutdown или filesystem напрямую сверх D. При отсутствии
   terminal result вернуть stopping с last exact observed counts или None.
   Даже exact0/0 не означает completed до worker-confirmed temporary cleanup.
   Error_code metadata_pending не трактовать как failed cleanup или successful
   completion; subsequent close может получить fresh exact counts/result.
5. До возврата timeout/ошибки сохраняются maintenance task/self/root references.
   Установка stop latch или возврат stopping не освобождают reservations/files.
   Lifespan finally по прежнему просит actor.close с **тем же** D.

Source `_cleanup` заменить metadata-only expiry marking: под store RLock
выбрать non-preparing entries с expired TTL и readers0, пометить internal
`delete_pending=True`; не удалять directory и не вызывать stat/disk_usage.
`get` уже expired entry отдаёт404; `create` не reuse delete_pending cache entry.
Сохранять entry/reserved в общей сумме и лимите128 до actual deletion. Internal
delete flags/claim и task control не попадают в `_public` response.
После release RLock request coalesced maintenance; это request не запускает I/O
в caller. `open` checking/readers increment остаются atomic под store RLock,
но перед ним проверяется independent close latch; после lock acquire latch
повторно проверяется, чтобы late open после close не получил новый lease.
Post-lock scheduling выполняется в finally общего get/create metadata path:
404, cache-hit return и admission refusal также не должны потерять pending expiry.
Existing create branch, удаляющий failed entry из metadata, также заменяется
delete_pending при ещё retained reservation/directory; никогда не терять
accounting из-за cache retry. После verified deletion такой entry удаляется.

Maintenance — максимум **одна** queued/running Future на существующем
`ThreadPoolExecutor(max_workers=1)`, помимо максимум одной preparation Future.
Нет нового потока, task на каждый expired entry, unbounded helper queue либо
thread/process kill. Один metadata flag coalesces repeated get/create/close/
reader-release requests. Пока maintenance scheduled/running, новый preparation
не admitted (existing ExportBusy409); cached ready response допустим до close.
Не запускать подготовку, которая заняла бы очередь за ожиданием старых readers.
Create повторно проверяет independent close latch и maintenance flag после
disk_usage, непосредственно перед publication/submission новой preparation.
Preparation Future reference регистрируется до release store RLock. Если close
успел поставить maintenance раньше фактического submit, maintenance после acquire
store RLock увидит queued preparation reference и отменит именно её до cleanup;
это не отменяет maintenance Future. Running preparation и maintenance одновременно
невозможны на одном worker. Отказ позднего create освобождает лишь неиспользованную
metadata reservation; если directory уже создан, reserved держится до cleanup.
Repeated requests атомарно устанавливают maintenance_dirty. Перед возвратом worker
под lifecycle lock либо consumes dirty/close и продолжает, либо clears scheduled;
новый request после clear резервирует ровно один task. Close flag не теряется
на гонке task completion и повторного close.

На worker maintenance выбирает delete_pending entry под store RLock, atomically
claims once и проверяет readers0/нет active writer для этого entry, затем release
RLock **до** `rmtree`. С этим claim новые readers/preparation этого ID запрещены.
Успешное удаление → кратко удалить metadata/release reserved; failure → сохранить
reservation и failed diagnostic, без повторного автоматического rmtree того же
claim. Queued maintenance не отменяется `cancel_futures` во время close.
Не использовать ignore_errors=True как proof удаления: отсутствующий directory
FileNotFoundError означает nothing-to-delete; остальные errors явно failed,
reservation остаётся. Preparation failure cleanup следует тому же правилу.
Если такой cleanup failure уже известен, close публикует failed с retained
accounting и не пытается удалить тот же путь косвенно через root cleanup.
Повторный close возвращает тот же failed result; новый cleanup/recovery вне этой
once-only attempt требует отдельного evidence/решения, не hidden retry.

При close task исполняется только после возврата running preparation (FIFO single
worker). Если preparation была cancelled before start, её запись не считается
active writer; directory мог ещё не существовать, cleanup отсутствующего directory
успешен. После expiry removals task проверяет current readers. Если readers>0,
не держит store/lifecycle lock и ждёт proposed internal `_lease_changed` Event; Event clears
**до** read/check, а release reader sets Event после decrement, чтобы не потерять
wakeup. Close caller этого ожидания не разделяет. После readers0/worker done
worker atomically claims root cleanup, releases locks, выполняет temporary cleanup
один раз, затем публикует completed0/0 либо failed с retained accounting.
Existing expiry deletion не включает удаление root; каждый directory/root claim
имеет свой once-only witness. После terminal cleanup executor shutdown(wait=False)
без отмены required maintenance; failed cleanup не проходит identical auto retry.

Все `rmtree`/TemporaryDirectory.cleanup, в том числе cancellation/TTL/root removal,
выполняются на existing preparation/maintenance worker вне store RLock и вне
HTTP/lifespan thread. Existing `_progress` и final `_prepare` storage calls могут
по прежнему блокировать RLock в disk_usage; close admission обходит эту блокировку
через отдельный stop latch и deadline snapshot. Зависший disk_usage/rmtree/OS I/O
может удерживать worker/process; caller stopping не обещает его termination.

## 11. Exact HTTP mapping, compatibility

Success bodies/status, authentication, path validation, original frozen bytes,
exact-child selectors и download capabilities не меняются. Нового public status
endpoint и client request field нет. Current frontend `describeDetail` умеет
string; поэтому `detail` **остаётся string**, а diagnostics идут в headers.
Новый frontend API для этого контракта не требуется.

Для actor errors добавить headers:
`X-QCL-Operation-ID` (если known), `X-QCL-Operation-Outcome`
(`not_started|unknown`), `X-QCL-Actor-State`, `X-QCL-Auto-Retry: false`.
При mutation fence refusal `X-QCL-Operation-ID` относится к **новой refused**
операции с outcome not_started; дополнительный `X-QCL-Unresolved-Operation-ID`
содержит original unknown UUID. Нельзя приписать not_started исходной mutation.
`Cache-Control: no-store` сохраняется. Header code по fixed enum; raw backend
error, token, plan, locator guesses туда не попадают. Retry-After не задаётся:
никакой гарантии, что arbitrary backend вернётся через известный интервал.

| Scenario before response headers | HTTP / detail | Outcome |
| --- | --- | --- |
| auth/schema/resource/path rejects before actor | existing401/403/413/422 string | actor не вызван; прежний contract |
| queued expiry | 504 `AiiDA operation did not start before its response deadline. Do not retry automatically. Operation: UUID.` | not_started |
| running read/open/seek deadline | 504 `AiiDA response deadline expired; the backend operation may still be running. Operation: UUID.` | unknown |
| running mutation deadline | 503 `Mutation outcome is unknown. Do not submit or repeat the mutation; reconcile existing provenance. Operation: UUID.` | unknown |
| started mutation exception | existing backend category404/422/503; safe original validation detail при422 + same unknown/no-repeat sentence; generic detail при404/503 | unknown; fence latched |
| full/lease_limit/blocked/stopping/closed/failed/owner_guard | 503 `AiiDA service cannot accept this operation (CODE).` | not_started |
| mutation fence reject | 503 `An earlier mutation is unresolved. Do not repeat it; reconcile provenance. Operation: ORIGINAL_UUID.` | not_started for new request; original receipt remains unknown |
| normal finite R backend exceptions | existing404/422/503 | no new mutation claim |
| export local busy/stopping/storage/invalid/missing | existing409/507/422/404 | local contract unchanged |

Actor exceptions проходят intact через `ExportStore._selection_call`/`create`
и `api.export_call`, до generic RuntimeError→503. Async preparation fault после
202 попадает в existing failed export status; не редактирует original202.
`get_export_plan` может оборачивать причину как ExportPlan, но actor receipt
retained/logged, mutation fence independent от export wrapper.
All invoke/invoke_agent_report/export_call paths выполняют общий mapper actor
exceptions первым; agent-report sanitization не удаляет unknown suffix/headers.
Normal 202/201 остаются только после known returned backend receipt; no synthetic
accepted status upon timeout. HTTP response lost after backend returned также
не разрешает automatic resubmit, как current README уже требует.

## 12. Контракт проверок реализации

Oracle — отдельный controller thread, `threading.Event` для backend_started,
release_backend, backend_returned и owner-cleanup events. Controller наблюдает
реальный `time.monotonic`; source-injected clock/deadline проверяет queue races.
Один fake clock не заменяет observed finite wait. В каждом test `finally`
обязательно release Event, bounded join и проверка всех started helper threads.
Не use time.sleep как единственное свидетельство порядка. Watchdog общей
targeted invocation остаётся снаружи, failed original log сохраняется.

| Case / proposed assertion | Различающее свидетельство |
| --- | --- |
| healthy finite initialization/R/M/read/seek/close | same values/status; один owner != caller; one loop; порядок enter→read→seek→exit→unload→loop_close; CloseResult completed |
| queued-before-start expiry | blocker confirmed started; second queued known; advance deadline/observe expiry; second backend_count0 после release; not_started, no fence |
| capacity limit | running+queued ровно configured cap; next refusal immediate; no hidden queued Future; control close admitted even full |
| in-flight submit timeout | backend signals started then stores synthetic UUID before hold; caller receives unknown while release unset; reconnect GET attempts/reads after return; submit_count==1; all further M refused; no202 before return |
| mutation raises after synthetic store | unknown receipt/fence for RuntimeError и ValueError variants; preserved HTTP category; no second store/submit; HTTP-side422 control never calls actor/fences |
| late successful mutation | known UUID logged under same op; already returned unknown response неизменен; latch остаётся, no implicit second mutation |
| init timeout/partial failure | constructor bounded fail; second actor refused while owner alive; release→single unload/loop cleanup; guard release only after actual return + successful cleanup |
| late open completion | caller open timeout before enter returns; after release exit_count1; no leaked reservation/proxy; no exit if enter itself raised |
| read/seek timeout + repeated reader close | wait returns while Event unset; next read refused; after release bytes discarded, exit_count1 on owner; close stopping→completed/failed, no second exit |
| disconnect during actor artifact backend read | ASGI2.3/2.4; backend_started before disconnect; response object retained against GC; ASGI exits within injected io+close envelope, owner exit deferred until release; finite control closes before ASGI return |
| close under stall/repeated close | first/repeated close return stopping while Event unset; unload_count0 then; after release exit_count1, unload_count1, loopclose1; next close completed; no caller cleanup action |
| exit/unload failure | CloseResult failed, not completed; once-only exit attempt; error retained; no concurrent forced profile unload |
| export prepare + direct download lease | stop cancels at callback or actor timeout; no ready after stopping; close stopping while worker or reader active; temp files exist until both finished; readers→0 and worker done trigger cleanup once |
| streaming error after headers | no substituted JSON/false full Content-Length delivery; abort/log; native exact-selector range remains independently usable after healthy restart/read |
| no network/science side effects | fake backend only; no real profile/daemon, SSH/Slurm, solver, install/build/full suite |

Exact existing controls to preserve:
`test_aiida_profile_operations_and_artifact_handles_have_one_thread_owner`,
`test_asgi_disconnect_closes_reader_and_releases_export_lease`,
`test_shutdown_cancels_preparation_at_the_next_progress_callback`.
Для первого existing ASGI control реализация **обязана** согласовать fake producer
в `tests/test_stream_disconnect.py` с существующим exact-child producer; его baseline SimpleNamespace45–57
не имеет новых selection/inventory methods и selectors у get_export_plan.
Это source-only несовместимость, не observed failing runtime. Exact fake seam:

- `get_export_selection(run, execution_id, *, attempt=None, calcjob_uuid=None)`
  returns ровно `{execution_id: "point_1", attempt: 1, calcjob_uuid: CODE,
  status: "unknown"}`; asserts known RUN/execution и согласованность заданных
  selectors, omitted selectors resolve один раз.
- `list_execution_artifacts(run, execution_id, *, attempt=None, calcjob_uuid=None)`
  asserts frozen selectors `(1,CODE)` и returns exact selected-child rows для
  `result/native.bin` size4 и `raw.bin` size len(payload), каждый с теми же
  execution_id/attempt/calcjob_uuid. Нельзя fallback на list_artifacts всех attempts.
- `get_export_plan(run, execution_id, *, attempt=None, calcjob_uuid=None)` asserts
  frozen selectors и returns exact plan bytes `b"{}"`, source `aiida.input.plan`,
  matching SHA256 и bytes2; existing open_artifact продолжает принимать selectors.
  Source/plan/selector witnesses сохраняются, а export остаётся real create→prepare
  lifecycle с synthetic exporter; не подменять ready запись вручную.

Все **4** ASGI cells (2.3/2.4 × artifact/export) сохраняют retained response
reference против GC, nonempty body/первый chunk65536, len(responses)==1 и actual
artifact-reader.closed либо export-entry.readers==0 assertions. Stalled actor
случай добавляется отдельно с Event start/release, не заменяет эти finite cells.

Дополнительный mandatory filesystem Event oracle (synthetic FS, не real NFS):

- Inject `disk_usage` Event-stall отдельно в `_storage` и create admission,
  observer подтверждает FS call started **и old store RLock занят**. Пока release
  unset, отдельный caller выполняет close(D): наблюдаемый real monotonic return
  в injected D с scheduling envelope, stopping, counts=None/error metadata_pending.
  No root removal/no reservations released при stalled writer/active reader.
- Inject `rmtree` Event-stall в coalesced TTL cleanup, triggered через get/create:
  FS исполняется existing worker вне caller/store/lifecycle locks. Repeated
  get/create/close не добавляют maintenance tasks; count scheduled/running<=1.
  Close возвращает stopping до FS release; leased directory не выбирается.
- Release FS, затем independently release download reader; проверить retained
  file/reservation до actual safe deletion, expiry claim deletion_count1 и
  root cleanup_count1, terminal completed0/0. Отдельный cleanup-error control
  возвращает failed, сохраняет accounting и не делает identical automatic retry.
- Через lifespan witness actor.close_requested возникает даже если export close
  вернул stopping, failed либо raised; оба получили тот же absolute D. Каждый
  Event releases в finally; helpers bounded-joined. Ни fake clock alone, ни
  sleep-only ordering не заменяют real monotonic/owner/count witness.

Целевые имена fault-тестов:
`test_blocked_call_returns_unknown_without_duplicate_submit`,
`test_close_does_not_claim_backend_completion`,
`test_disconnect_releases_handle_exactly_once_after_backend_returns`.
Fault fixtures не доказывают responsiveness настоящего AiiDA/NFS/backend,
hard process exit, cancellation Slurm или physical acceptance.
