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
