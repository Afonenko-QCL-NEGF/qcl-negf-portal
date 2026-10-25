# Bounded Runner configurator transport

The optional adapter invokes one trusted `qcl-negf configure` command. Runner
owns the scientific parser, units, defaults, variant validation and resolver.
The Portal does not parse YAML or regenerate plans. This feature does not admit,
submit or solve a run. A preview is not scientific acceptance.

Configure all four administrator settings (no HTTP equivalents):

| Environment setting | Binding |
| --- | --- |
| `QCL_NEGF_CONFIGURATOR_EXECUTABLE` | Exact absolute prepared Runner executable |
| `QCL_NEGF_CONFIGURATOR_CATALOG_ROOT` | Exact absolute immutable Research catalog root |
| `QCL_NEGF_CONFIGURATOR_CATALOG_SOURCE_ID` | Exact selected Research source identity |
| `QCL_NEGF_CONFIGURATOR_CGROUP_ROOT` | Delegated v2 root of the existing API unit |

The release owner supplies matching executable/catalog/source bindings. The
adapter does not infer installed revisions from PATH or claim an independently
measured Runner version. Missing bindings or runtime controls leave existing
reads available and advertise `configurator: {available:false,
catalog_source_id:null,reason:...}` in authenticated `/api/v1/config`.

The existing systemd API unit must use `Delegate=cpu memory pids`,
`DelegateSubgroup=api` and `KillMode=control-group`. The configured parent must
be empty, writable and expose those controllers; `/proc/self/cgroup` must match
its `api` subgroup on a unified cgroup2 mount. A stale configurator leaf refuses
availability. No other writable subtree is searched. CFG-D owns unit binding;
source implementation alone does not establish production readiness.

Each explicit request creates a unique sibling leaf. Before spawning the stdlib
launcher, the adapter writes and reads back `cpu.max=100000 100000`,
`memory.max=2147483648`, `memory.swap.max=0`, `memory.oom.group=1`, and
`pids.max=64`, and requires writable kill/placement and readable events controls.
The launcher joins its PID, verifies placement and execs the fixed Runner.
`cpu.max` limits CPU capacity, not core affinity; pids includes threads. Memory
is cgroup charged memory, not an RSS-only measurement. There is no RLIMIT_AS,
preexec hook, sudo, shell, new service, queue or automatic retry.

A single process-local gate covers authenticated, no-store
`GET /api/v1/configurator/catalog` and `POST /api/v1/configurator/preview`.
Production uses the existing single Uvicorn worker. Concurrent calls return 409.
Preview receives exact UTF-8 JSON bytes with `mode`, `template_id`,
`catalog_source_id`, and exactly `fields` or `yaml`. Host paths, executable,
environment and arbitrary command root keys are refused. Runner owns inner
scientific syntax and safe catalog-relative sources. Fixed argv is:

```text
EXECUTABLE configure --catalog-root ROOT --catalog-source-id ID [--catalog]
```

The launcher uses an explicit environment containing only UTF-8 locale,
Julia/BLAS/OMP thread counts of one and Julia offline mode. The selected immutable
wrapper must bind its prepared offline depot and fixed source environment; there
is no install/bootstrap fallback. This source-bound no-URI/offline contract is
not a kernel network sandbox.

The deadline starts before spawn: 60 seconds, stdout 8 MiB, stderr 64 KiB,
catalog 1 MiB, request 256 KiB, YAML 128 KiB, JSON depth 64/nodes 100000,
points/executions at most 100. Independent chunk readers continue draining after
an overflow without retaining additional bytes. The complete response refuses
malformed/duplicate/nonfinite JSON and unsafe untagged integers; Runner's
`{kind:"integer",decimal:"..."}` values pass unchanged. The raw `plan` string
passes unchanged and its UTF-8 SHA-256 must match. Its size must fit both Runner's
4 MiB cap and advertised `/config.limits.plan_bytes`. No arrays or grids are
reduced to fit. Generic refusal messages do not expose stderr or tracebacks.

Timeout, disconnect, cancellation, spawn failure, shutdown and successful exit
all enter shielded cleanup: close stdin, kill the leaf and pre-placement process
group, reap the direct child, drain both streams, verify populated zero and remove
the leaf before releasing the gate. Spawn ownership survives cancellation. If
cleanup is not confirmed within five seconds, the adapter becomes unavailable
and retains its busy gate; its owned cleanup task continues. A kernel stall is
not represented as successful termination. Unit stop/restart remains systemd's
whole-control-group responsibility.

Tests in `tests/test_configurator.py` and `tests/test_configurator_api.py` use
injectable synthetic processes/cgroups. They are source fixtures, not evidence of
native Runner readiness, actual kernel resource enforcement or scientific
acceptance. Native cgroup/Runner and release validation require their separately
authorized prepared environment and finite budget.

## C07: слои и явный физический variant

Новый additive Runner `structure_editor` (`qcl-negf.structure-editor.v1`)
содержит canonical inputs, one-period geometry, полные material tuples и source
binding каждой execution. Portal проверяет schema/status, finite browser-safe
scalars, ≤256 layers/interfaces, material label≤256 UTF-8 bytes, projection≤1MiB,
execution/template/catalog/model SHA и preset bindings. Старый response без поля
означает capability unavailable; прежние YAML/preview/plan остаются доступны.

Сначала получите preview template без нового variant. В короткой форме выберите
structure editor и явно загрузите нужную execution/variant. Это копирует canonical
Runner inputs в отдельный черновик; refresh preview не заменяет введённые значения.
Смена token/catalog/template/model сбрасывает bound draft. Explicit выбор execution
заменяет draft; numerical/structure переключение сохраняет оба буфера и отправляет
ровно выбранный editor. Combined expert variant остаётся в полном YAML. YAML→form
по-прежнему требует existing explicit reset; original YAML и frozen plan не
парсятся и не переписываются клиентом.

Форма поддерживает add-as-explicit-copy, remove, reorder, duplicate layer,
толщину, полный tuple, doped mask, global donor sheet density (m^-2), ionization,
position origin, reference energy и явные interfaces. «Набор выбранной модели»
копирует весь tuple из этой bound execution. Изменение x_Al не пересчитывает E_c,
masses или epsilon; custom edits имеют unknown/research origin. Assigning histories
относятся к resolving inputs; ссылка на геометрию не доказывает происхождение
каждого material constant. Это не универсальная material database.

One-period rectangles и таблица показывают submitted piecewise controls с меткой
«черновик, Runner ещё не проверил». JS складывает только заданные толщины для
геометрии до position-origin/bias sampling. Interfaces остаются заданным списком,
не заменяются границами после reorder/thickness edit; внепериодные/неполные значения
получают предупреждение и требуют явного исправления либо Runner refusal. Doped
mask вместе с N_sheet не превращается в volume-density profile в UI. Unit/material
resolution, нормировка donor profile на сетке и effective field при новом period
остаются у owning Runner/Core. Для точного expert quantity text используйте YAML.

Structure editor сериализует один existing `fields.variant` с
`comparison_kind:physical_parameter` и шестью whole-leaf controlled paths:
`physical.layers`, `physical.interfaces`, `physical.donor_sheet_density`,
`physical.ionization_fraction`, `physical.position_origin`, `physical.reference_energy`.
Никаких indexed paths, новых HTTP request keys или implicit combined variants.
Новый ID не должен совпадать с template variant; authoritative collision/scope/
physical validity проверяет Runner. После любой правки preview/plan confirmation
снимаются. Только новый validated preview можно передать в существующую отдельную
форму submit с исходными exact plan bytes/SHA.

Это no-solve structure input editor. Новые quantum levels/wavefunctions, live
histories, scientific acceptance и installed browser/native usability им не
доказаны и автоматически не запускаются. Execution, convergence, physical checks,
discretization и experiment остаются отдельными axes с unknown для preview.

## Bare quantum preview

The additive authenticated, no-store
`POST /api/v1/configurator/quantum-preview` accepts only Runner's
`qcl-negf.quantum-preview-request.v1` envelope: exact frozen plan string/SHA,
expected Runner/Core IDs, explicit unique execution/point/variant and fixed
`quantum-preview-reference-v1` policy. The browser cannot choose executable,
filesystem paths, environment, budget, URI or scientific defaults. Runner owns
all scientific validation, model loading, basis/eigen operators and normalization.
Existing catalog/metadata configure calls retain their60s budget; the same single
try-acquire slot/cgroup lifecycle serves all three operations without a queue.

`/config.quantum_preview` reports actual installed source-context availability,
Core/Runner IDs and request-time memory admission. This is not a promise of
numerical fit or currently reserved capacity. The parent getter uses the existing
fixed `/var/lib/qcl-negf-admin/runtime/release.json`: nofollow, regular≤64KiB,
root-owned/no022 with protected root-owned ancestors; ready and exact application/
solver profiles, service.env release/solver/context digest, canonical source graph
and exact refs/build/output/NAR context. Only the server builds capped10field
child context environment. Delivery verified graph→actual build/derivation/output/
NAR/Manifest; the child rechecks that protected record and actual loaded pkgdir/
Project/Manifest/rules. Labels/version alone do not establish binding. No new
record/protocol/CLI flags or per-request depot/NAR scans are introduced. Missing
or mismatched installed delivery remains unavailable. Closed SCI gate does not
veto this basis-only diagnostic.

Before spawn a fresh≤5s guest snapshot checks kernel boot_id and actual cgroup hierarchy stability,
MemAvailable≥2684354560bytes, all measured ancestor memory.max/high−current at
least that amount, CPU quota/affinity, no swap credit and the short-window PSI
avg10 gate. Cumulative memory.events and historical PSI avg60/avg300 are optional
private snapshot observations: missing, unreadable or malformed history is unknown
with a private reason, never zero or an admission veto. Nonzero historical totals do not
permanently veto a healthy current snapshot. Unlimited literal `max` is retained as known unlimited.
A global cgroup-v2 root has no parent controls on some kernels: only mountinfo
root `/` and walking to its mountpoint permits those **root-only** missing controls
only for the approved existing API unit whose initial guest cgroup namespace/full
hierarchy is confirmed by native DEPLOYBIND and whose `RestrictNamespaces=true`
(or verified equivalent unshare/setns denial) prevents namespace changes.
NoNewPrivileges/User alone do not establish this prerequisite. A request measures
self namespace before/after; it never reads root PID1 namespace or requests CAP/
permission changes/new proof records. Guest MemAvailable and measured
global PSI remain mandatory; finite ancestors are still checked. Any other
missing control, hidden namespace ancestry, malformed/unknown fact, changed boot/
hierarchy or old snapshot holds preview. Root-only DMI product_uuid is deliberately
not required: local memory admission needs readable boot/cgroup/memory facts;
release/binary authority is separately anchored by the protected record/context.
No root publisher, permissions change or new identity file is introduced. No fallback/offload/coarsening or job
cancellation. This guest observation does not prove host/PVE reserve or future
availability against concurrent workloads.

Deadline starts in body middleware: absolute API190s includes body/admission;
body/work cutoff185s leaves five seconds for cleanup. Late body completion and
already expired work deadlines refuse before binding/decode/admission/spawn;
deadline is rechecked between these synchronous steps.
Quantum `_run`180s spans source/parse/admission/spawn/load/basis/serialization and
response validation; it is not the old60s clock. API reserves five seconds for
shielded cgroup.kill/direct child reap/stream EOF/populated0/remove. Unknown
cleanup poisons the common slot; no success or reuse is claimed. Disconnect,
cancel and shutdown retain spawn ownership through cleanup. Child caps remain
1CPU,2GiB memory.max,swap0,64pids, Julia/GC/BLAS/OMP1, offline immutable runtime.
Eligibility (W≤1536 and existing dimension/screen guards) is not a measured
180s/2GiB fit guarantee. The scientific grid/model remains unchanged on refusal.

Runner writes a new server-owned private output plus atomic temporary; the
launcher joins the prepared leaf before exact exec and applies2MiB FILESIZE.
Stdout≤2MiB/stderr≤64KiB; private output readback must exactly equal stdout bytes.
Temporary storage≤8MiB (one output+atomic temporary; no HDF/checkpoint/depot write).
The API returns the original bytes, not a reserialized success DTO. Known owner
refusals retain only safe reason/numeric actual+limit/unchanged plan hashes;
unknown diagnostics and host paths are not returned. Busy409, timeout504,
unavailable503, owner refusal422. No retry or alternate scientific task.

The light UI offers explicit execution/point/variant selection and an explicit
preview action, never automatic computation. Client195s AbortSignal covers
request/response; edit/context/token/source/point changes invalidate and abort,
late successes/errors are fenced. Download preserves exact producer JSON bytes.
All Nb energies, Re/Im ψ, density and bare material band overlay use the recorded
nonuniform z/units. Plot mapping is display-only and remains finite for signed
extrema/singletons; physical values/order are retained. No UI normalization,
energy shifting, density/scattering or physical interpolation. κ0, Hartree0,
no T±, zero-field window/Dirichlet and degenerate-state ambiguity are explicit.
This is not a run/attempt, SCBA/Poisson solve, transport convergence, discretization
validation or scientific acceptance. Missing elapsed/peak/JIT remains unknown.
Native exact-reference target/installed binding/browser qualification are separate
finite gates; source/fixtures alone do not accept them.
