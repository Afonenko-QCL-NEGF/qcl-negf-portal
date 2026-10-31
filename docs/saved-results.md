# Native5 retained point reads

Светлый экран **«Сохранённые результаты точки»** использует явный выбор run →
исполнение / транспортная попытка / CalcJob → точка исходного плана. Нажмите
**«Обновить сведения выбранной точки»**: исходные UUID/SHA и полный StateRef приходят
из public AiiDA getter; ручной ввод pins для обычного пути не нужен.

Refresh не читает NFS и не обещает, что сохранённый payload сейчас доступен.
`absent` показывает исходную причину и отключает чтение; `checkpoint` позволяет
явно запросить диагностику/историю без final maps; `final` позволяет запросить
описание представлений. Наличие конкретной history/view capability устанавливает
только Results descriptor при отдельном чтении. `retained_source` сохраняет
исходного creator и причину, отдельно от transport CalcJob/attempt.

UUID/SHA/full StateRef и обе integrity scopes доступны в read-only details
**«Исходные идентификаторы и область проверки»**. Первого/latest source по умолчанию
нет. Повторный refresh той же точки снимает старые сведения, карты, диагностику и
cursor до нового ответа. Смена token/run/tuple/point или исчезновение выбранной
tuple/point после обновления run также снимает контекст; запоздалые success/error
не возвращают старое состояние. Обычный неизменный run poll не запускает refresh.

## Точные метаданные точки

`GET /api/v1/runs/{run_uuid}/saved-results/point-source` принимает ровно обязательные
`execution_id`, positive safe integer `attempt`, полный `calcjob_uuid` и `point_id`.
Bearer authentication и no-store обязательны. Лишние/повторные параметры, непустое
GET body, path, binding/hash claims и некорректные selectors отвергаются (422).
Вызов actor: `get_native_result_source(..., point_id=..., binding_uuid=None)` — один
public owner вызов; не обход каталога R1 и не дополнительная работа на run poll.

Закрытая проекция `qcl-negf.portal-point-source.v1` содержит:

- `status=available`, `scope=original_owner_metadata` и exact `provider_selection`;
- `point={point_id,point_attempt,plan_point_id}`;
- `source={kind,relation,reason}` и полный исходный `source_state` (null только absent);
- original `retained.native_result`, `native_binding`, nullable `creator` и отдельные
  `integrity.readback` / `integrity.original_binding`.

Это доступность **метаданных**, не current RO или научная приёмка. Binding storage,
artifact_root, raw result/полный binding и host paths не выдаются. Неверный original
CREATE/point/StateRef — ошибка, не искусственный absent. Полный owner DTO и ответ
ограничены прежними 2 MiB, без усечения. Metadata доступна без local RO binding при
включённом feature; чтение payload затем может честно отказать unavailable_storage.
Owner accounting320MiB metadata/256MiB I/O не означает маленькую цену refresh или
измеренный RSS. Сохраняются одна actual future, WALL10s, actor timeout30s и cleanup
gate/quarantine; прекращение HTTP-ожидания не доказывает завершение actor getter.

The original AiiDA getter is
`get_native_result_source(run_uuid, execution_id, *, attempt, calcjob_uuid,
point_id, binding_uuid=None)`. It runs on the existing profile owner and returns
bounded detached metadata. The actor rechecks original node/raw relations. Its
`current_access=not_checked` and `native_contexts=not_reverified` markers remain
unchanged; an available historical DTO does not grant current storage access.

Native `Identity` has exactly `run_id`, `execution_id`, `point_id`, decimal-string
`attempt`, `branch_id`, `model_sha256`, `plan_sha256`. It is distinct from AiiDA's
run/CalcJob UUIDs and integer input attempt. Native attempt is never stamped from,
or ordered against, the transport attempt. A full immutable `StateRef` pins its
identity, state ID/sequence and manifest FileRef. Counts and byte sizes remain
canonical decimal strings.

## Existing authenticated routes

All routes require bearer authentication and use no-store. The payload-reader metadata GETs
accept exactly `execution_id`, positive `attempt`, `calcjob_uuid`,
`provider=native_nfs`, `point_id`, `native_binding_uuid`,
`state_manifest_sha256`. Unknown or duplicate selectors are refused.

- `GET /api/v1/runs/{run_uuid}/saved-results/manifest` describes stored views.
- `GET /api/v1/runs/{run_uuid}/saved-results/diagnostics` describes native history.
- `POST /api/v1/runs/{run_uuid}/saved-results/slice` adds the complete authorized
  `source_state`, explicit `view_id`, and native `selection` (one index or positive
  start/stop/step range per logical axis).
- `POST /api/v1/runs/{run_uuid}/saved-results/history` adds `source_state`,
  `stage=scba|outer`, direct IEEE `fields`, `maximum_records` (1–256), and nullable
  canonical `cursor`. This is consumed event count, not filtered display row count.

Responses use `qcl-negf.portal-selected-reader.v2`: exact `provider_selection`,
`provider=native_nfs`, `selection_mode=retained`, full `source_state`, original
retained node pins/creator and two distinct integrity projections:
`retained.integrity.readback` copies the getter limitations, while
`retained.integrity.original_binding` copies the historical binding marker
(`selected_metadata`, native contexts/transport owner verified, full core payloads
not assessed, current access not checked). Neither scope upgrades the other.
A separate request-local `access` observation,
and the unmodified owning `view`. Native `views` is a dictionary. State echoes
are compared in full against the metadata StateRef, not only by hash. A refreshed
context never rebases an earlier history cursor. Old commit/index/phase row-range request
shapes are refused, with no scientific v4 adapter.

The child uses public `StateReader`, exact final-observations FileRef and
`AnalysisReader` facades. Diagnostics/history use `DiagnosticReader` and
`HistoryViewRequest`; catalog import is explicit. A checkpoint-only source can
have diagnostics/history while final views remain unavailable. No first
`physics.analysis` role match chooses between final diagnostics and observations.

## Access and lifetime

Only the existing isolated child opens scientific bytes. Server-configured
immutable binding fixes the RO NFS4 alias and allowed storage/computer/retention
identities. The parent compares the original execution-relative root and exact
source path against that binding; paths are never HTTP inputs or outputs.

The child checks the actual mount namespace and mountinfo, nofollow ancestry,
and the entire selected artifact-root mount subtree. Descendant or intervening
submounts are refused, including apparently compatible mounts. It holds parent
and root directory FDs, then rechecks mount records, namespace and FD/path
identities after the public readers have closed and before publishing a response.
This relies on trusted mount administration and requires separate native
qualification; immutable config and synthetic mount fixtures do not prove it.

The existing one-request gate, ten-second deadline, bounded subprocess streams,
CPU/AS/file limits, cancellation, kill/reap and deferred cleanup remain. Child
imports bind the same selected Portal/Results/Contracts package roots as its
parent. Cleanup uncertainty preserves the primary error and permanently closes
further admission; a caller timeout is not successful backend termination.

Owner read limits bound metadata/catalog/array allocation, I/O, object inventory,
cells, lines and state subsets. A small slice may still require a whole selected
owner SHA pass. No arrays/grids are reduced to fit. Logical/process limits are
not a claim about measured RSS, native allocator cost or cluster resources.

## Scientific presentation

Signed native values, negative zero, null with exact invalid masks, native axes,
quadrature/scales, basis/gauge, raw assessments and original source identities
remain visible. Population fractions use only owning saved inputs. History keeps
original canonical records, IEEE bits, exact decimal coordinates, missing reasons,
unit changes, witness updates and owning breaks. Unsafe numerical display
coordinates stay in the raw table; the UI never rounds them for a curve.

Readiness, process exit, convergence, physics, discretization and experimental
validation remain separate. GET and graphs do not run solve, derive or scientific
acceptance. Static or configurator preview data never replaces a retained used
snapshot.

## Execution-wide каталог и J(V)

После явного выбора execution / transport attempt / CalcJob нажмите
**«Прочитать каталог»**. Он включает frozen declared points, в том числе
необсчитанные. Prev/next читают только выбранную страницу; **«Прочитать J(V) этой
страницы»** отдельно читает её final sources. Начальный render и обычный неизменный
run poll не читают каталог или NFS. Нет автоматического обхода всех страниц.

```text
GET /api/v1/runs/{run_uuid}/saved-results/sources
    ?execution_id=exec&attempt=2&calcjob_uuid=<uuid>&limit=8
GET /api/v1/runs/{run_uuid}/saved-results/sources
    ?execution_id=exec&attempt=2&calcjob_uuid=<uuid>&cursor=<next_cursor>
GET /api/v1/runs/{run_uuid}/saved-results/brief
    ?execution_id=exec&attempt=2&calcjob_uuid=<uuid>&page_ref=<page_ref>
```

Bearer/no-store обязательны. Selectors canonical, execution portable, attempt —
positive safe integer, UUID полные. Sources допускает либо limit1..8 (default8),
либо cursor; brief требует page_ref. Лишние/повторные параметры, body и query>8192B
дают 422 до owner dispatch. Cursor/page_ref — unpadded canonical base64url,
duplicate-free closed JSON≤2048 decoded bytes: schema
`qcl-negf.portal-native-page.v1`, provider_selection, genuine native_result,
native_binding, input_plan, source_graph NodePins, u64 decimal offset и limit.
Token не даёт authority: каждый request ровно один раз вызывает owning
`get_native_result_source(...,point_id=None,binding_uuid=...)` и сверяет исходные
pins. Изменение pin даёт source_changed409; нет legacy/first-point fallback.

Closed `qcl-negf.portal-native-sources.v1` содержит scope=original_owner_metadata,
provider_selection, retained four NodePins + раздельную integrity, pagination
(offset/limit/total/page_ref/previous_cursor/next_cursor), points. CatalogPoint:
point_id, declared_coordinates, row_status, point_attempt decimal|null,
source kind/relation/reason|null, полный source_state, source_invocation,
observations_ref, creator, recorded_statuses и missing_reason. Final
observations_ref — **исходный полный O1 FileRef**, иначе null. Это read-only pin,
не путь/разрешение доступа. Artifact root и полный binding/raw не выдаются.
Row point_attempt сохраняет действующий owning u64 binder contract и передаётся
точной decimal string, включая u64 max за пределом JS safe integer. Это отдельный
counter от transport attempt и native StateRef.identity.attempt; между ними не
вводится равенство или отношение порядка. Произвольный decimal без u64 cap ниже
относится к declared T/V/order.

Declared JSON integer T/V передаётся как `{kind:"json_integer",decimal:"..."}`,
исходный float — неизменённый `{kind:"ieee",type:"f64",bits:"..."}`. Declared
order — canonical nonnegative decimal string без нового u64/Int64/model cap;
допустимость исходного числа устанавливает frozen scientific-plan schema.
Произвольная integer declaration, включая 2001 digits, не преобразуется в
JavaScript Number/Infinity. Общий JSON cap ограничивает размер, без truncation.
Branch/order сохраняют исходный plan order. Declared coordinates **не** used
snapshot и не заменяют actual O1 request coordinates.
Join с recorded T/V повторяет owning binder binary64 bit comparison: например,
frozen integer70 и recorded float70.0 сопоставимы по этому join, но declared
integer tag сохраняется. Это проверка source relation, не физическая приёмка или
доказательство сопоставимости разных расчётов; branch/order сравниваются точно.

No result row — not_recorded/result_row_absent, без выдуманного native absent;
recorded absent и checkpoint сохраняют исходные причины и StateRef. Closed
`qcl-negf.portal-native-brief.v1` повторяет ту же metadata страницу и добавляет
read/access/view на каждой строке и page credits. У no-row/absent/checkpoint
view/access=null. Final view — public `AnalysisReader.brief()` без scientific
переинтерпретации. Parent, child и frontend сравнивают полный StateRef/7 Identity,
source invocation, все 5 O1 FileRef fields, original pins и порядок строк.
Failure/change/credit/cancel у admitted final прекращает всю page; частичный brief
не публикуется, metadata и исходные scientific statuses не переписываются.

Один existing isolated child последовательно открывает final source domains.
До следующего источника public reader/facade и domain FDs закрываются; domain
проверяется снова после reader close. Для n final sources≤8 заранее резервируются
два nonrefundable этапа на источник: `share=floor(128MiB/(2*n))` для public
StateReader constructor и отдельно AnalysisReader brief+close. Initialization и
brief_and_close — разные actual counter epochs; page actual sum включает оба.
Unused credit не возвращается/не перераспределяется. Metadata≤1MiB/owner≤64MiB и
существующие object/open-file/process limits сохраняются. До spool/root reads
резервируются равные output slots из остатка aggregate2MiB; public brief JSON cap
оставляет в каждом slot 1024B для wrapper. Большая page может честно потребовать
явного меньшего limit. Logical credits не равны измеренным disk I/O/RSS.
Страница без final sources не запускает child и не требует local RO binding.

График — scatter actual V per period (V) против **сохранённой знаковой physical J
(A/m²)**. Нет линий через gaps, abs/J0 повторно, преобразования J в I без area или
вывода физической сопоставимости по flags/T/branch. IEEE -0, отрицательные и
предельные finite значения сохраняются; ±Inf/NaN/null не становятся zero и не
рисуются, raw tags/reasons остаются в таблице/details. Original/current/restoration
evidence, process, convergence, physical gates, discretization и scientific
acceptance различаются; null/not_measured не pass. Page/refresh/token/run/tuple
change/removal немедленно снимает старый brief и инвалидирует late success/error.

## Remaining producer gates

Native current bootstrap (R3), genuine retrieved rooted snapshot (R4), native
performance (R5), derive/comparison и actual used-value help остаются отдельными
контрактами. Human/agent report read/attach сохраняется.

Synthetic tests exercise public native5 readers, actual isolated child cleanup,
API transport and light source/SSR controls with explicitly synthetic RO facts.
They do not establish actual NFS/profile/installed/browser or scientific acceptance.
