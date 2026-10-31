# Explicit available performance (U1)

Authenticated, no-store `POST /api/v1/runs/{run_uuid}/live-results/performance`
accepts exactly `execution_id`, positive safe-integer `attempt`, canonical
`calcjob_uuid`, `mode` (`retained` or `current`) and an explicit lowercase
64-character SHA-256 `anchor_key`. The run UUID is canonical and execution ID is
portable. No query parameters, caller paths, automatic anchor selection, retries,
attempt fallback or mode switching are accepted. Anchor discovery is separate;
this view requires a previously selected exact anchor.

The response has exactly ten fields: `schema=qcl-negf.portal-performance.v2`,
`operation=performance`, `status`, `provider_selection`, `mode`, `anchor_key`,
`source_metadata`, `access`, `view`, `owner_runtime`. `source_metadata` preserves
the whole owning AiiDA `qcl-negf.performance-source.v2` DTO, including original
input/CREATE pins, retained node associations, candidates and refusal reasons.
Those origin assertions, current RO access and Results reader integrity are
separate evidence. A borrowed native StateRef/source CalcJob can differ from the
enclosing provider attempt; the canonical plan comes from the source selection.

For `available`, `view` is the unchanged public
`qcl-negf.performance-summary-view.v2` for the exact selected StateRef. A scientific
manifest with `closed=false` can still provide an immutable selected prefix.
Neither legacy `closed=true` nor process exit status establishes scientific
acceptance. `access` records the selected scientific and technical roots' current
RO mount/domain checks and trusted local config SHA. All descriptors in the whole
index, including unselected roots, must match the trusted storage configuration;
this does not claim fresh payload access to every unselected root.

For `staging` and `unavailable`, `access` and `view` are null; the original source
DTO and reason remain visible. The internal current locator is probed and
requalified before a response; it is never presented as a ready view. Child,
access, source-change, budget and cleanup failures return the existing closed
HTTP errors (409 selection/identity/source-change/busy, 422 malformed/budget,
503 storage/stopping/backend), without partial data or a manufactured owner DTO.
Technical unavailability is independent of scientific history and acceptance.

## Calls, capacities and lifetime

Retained makes at most one public source call, one original-plan getter and one
summary child. Current makes at most two source calls, one pointer/index probe,
one plan getter and one summary child. There is no extra context read, metadata
replay, collector, scheduler operation, solve or source-tree scan. Exact original
plan bytes, including whitespace, must match the getter SHA, whole original
NodePin, canonical FileRef and source transport before a parent writes them to
its owned `plan/plan.json`. No native-workdir mount is opened for the plan.

| Boundary | Admission bound |
| --- | --- |
| AiiDA T4 source | Owner's 640 MiB gross per call; no actual I/O/heap/RSS report |
| Original plan getter | Owner's 16 MiB original/retrieved read bounds |
| Current pointer / immutable index | 64 KiB / 1 MiB raw, bounded nofollow reads |
| Results StateReader | 1 MiB metadata; 128 MiB I/O; 4096 catalog entries/pages; four open files |
| Public performance read | 1 MiB metadata; 4 MiB I/O; 2 MiB JSON; 16384 objects; 16 sessions |
| Local spool forecast | 16 MiB plan + 2 MiB reusable request; owner 64 MiB, temporary 128 MiB, fresh free-space guard |
| Existing child | One CPU; CPU 10 s; address space 512 MiB; file/stdout 2 MiB; stderr 4096 bytes |
| Whole HTTP body | 2 MiB, counted incrementally before final full encoding |

These capacities do not measure native allocation peaks or total HTTP cost.
The adapter preserves `view.io_counters` and `view.observer_self_cost` with their
actual units, scopes and unknowns. Inclusive counters are not added together.
Observer cost describes the collector; sampled Julia RSS does not measure Portal
child RSS. `owner_runtime` is always the literal `not_measured` projection for
`aiida_public_source_getters`, with `actual_io_bytes`, `heap_bytes` and
`peak_rss_bytes` null and reason `not_reported_by_public_source_getters`.
There is no cost schema, reserved-credit response, epoch ledger or self-count.
Native controller/child RSS, reserve fit and latency remain separate gates.

The existing SavedResults carrier provides one 10 s response-wait/check lifetime;
every new phase checks the same deadline and cancellation flag. The actor keeps
its original default 30 s call policy. An issued actor future, kernel close or
reap can finish later; this is not a hard end-to-end deadline. ASGI cancellation
does not release the actual future/gate. Reader close, final domain checks and
FD close, actual worker reap/pipe close and owned spool cleanup precede a normal
view. Unknown actor/close/reap/unlink outcomes quarantine the optional reader and
retain its owned spool. Existing child error `stopping` carries U1 close
uncertainty through the generic error wire without partial results. Known
completed errors clean up normally. There is no scientific HDF spool copy or
selected-leaf-copy guarantee: this is direct public RO reader access.

## Interface and verification limits

The fixed light interface requires mode and anchor, preserves Decimal/ExactScalar,
UInt64 strings, IEEE bits, units, coverage, retention and unknown measurements,
and displays whole source details. Provider/mode/anchor/source-StateRef changes
reject stale replies. Cancelling waiting clears old views and invalidates late
success/error; it does not assert that the backend has already stopped.

The retired legacy live-history adapter fails explicitly with
`capability_unavailable`; independent U2 work is required to replace it. Stored
P10 history remains on its separate API. U1 does not claim that live history was
repaired. Prepared source/HTTP/ORM/public-reader fixtures do not establish
installed service, real RO NFS, browser interaction, resource fit or scientific
acceptance. No scientific model, tolerance, reference implementation or solver
is changed by this adapter.
