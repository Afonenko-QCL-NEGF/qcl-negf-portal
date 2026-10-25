# Active history and retained performance

The authenticated, no-store `POST /api/v1/runs/{run_uuid}/live-results/`
operations share the existing saved-results admission gate, actor, isolated
child, deadline, disconnect handling and owned-spool cleanup. No polling,
registration, solver, profiling or new orchestration is performed.

Every body requires `execution_id`, positive input `attempt` and canonical
`calcjob_uuid`. Extra keys, query parameters and implicit selections are refused.

| Operation | Additional exact fields | Result |
| --- | --- | --- |
| `live-source` | `point_id`, positive native `source_attempt` | Verified actual descriptor/index closed prefix; no HDF payload opened |
| `live-history` | same point fields, `index_sha256`, `generation`, `phase`, `selection` | Selected native rows, coordinates, units, masks and discontinuities |
| `performance` | none | Independently verified retained close sidecar, or unavailable with original reason |

History `selection` is `{start,stop,step,columns}` with exclusive stop, at most
512 rows and 16 explicitly named value columns. The producer descriptor does
not advertise column metadata by opening payload; names are supplied explicitly
and validated by Results. A changed index returns `source_changed`/409 and needs
an explicit descriptor refresh. There is no automatic retry.

The AiiDA live DTO establishes only exact graph selection and trusted relative
mapping. `descriptor_verified=false` is never sufficient for a usable response.
Only the child checks the configured namespace, storage/Computer identity and
existing read-only NFS mount policy, then opens `live/current.json` (16 KiB cap)
and the pinned native index. Nofollow ancestry and same-open stable metadata are
enforced by Results primitives. Actual index identity, SHA, generation and
cutoffs must agree. Each referenced leaf also passes the existing NFS submount
policy. Results copies only selected closed leaves from held nofollow FDs to an
empty owned local spool and validates SHA/size before local HDF decoding. Active
access does not use L1 registration, writer-stop predicates or a lease proof.
The existing L1 sealed-source behavior is unchanged.

Descriptor and extra index reads are deducted from the same 128 MiB I/O and
1 MiB metadata ceilings; Results retains its 64 MiB spool, 1 MiB selected data
and 2 MiB JSON limits. The existing one-CPU child, 512 MiB address-space limit
and 10 second request deadline remain. Only live-history raises the child file
size ceiling to 64 MiB for selected leaves; JSON stdout remains capped at 2 MiB.
Timeout/cancel waits for actual child reap. Unconfirmed cleanup retains the
spool and disables the optional adapter, as for saved results.

Performance reads exactly the raw retained sidecar and independently supplied
binding from AiiDA. Results verifies embedded native commit/summary bytes,
selection, point pins and runner result SHA. No current/newest/catalog/Parquet
lookup occurs. Missing old data remains unavailable with a reason; CPU capacity,
RSS, coverage gaps and unknown values are preserved. The summary scope is the
enclosing execution attempt and root Julia process including threads. It is
never attributed to each point or to a borrowed point source; total scheduler
grant and terminal global peak are not inferred. Inclusive producer phase
measurements are not job wall time. Scientific acceptance stays separate.

The light interface offers explicit execution, point, native-attempt and row
selection. Context changes invalidate in-flight responses and clear old views.
Tables preserve ExactScalar, native units and invalid masks without converting
missing values to zero. Full coverage, binding and unknown reasons remain
inspectable. No historical node is mutated.

Tiny fixtures in `tests/test_live_performance.py` and
`frontend/tests/live-performance.test.ts` cover the intended seams. Their source
alone does not establish runtime, real NFS mapping, browser rendering,
deployment, performance overhead or scientific acceptance; these require their
separately admitted verification gates.
