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
