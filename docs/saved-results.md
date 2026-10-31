# Native5 retained point reads

The light **Сохранённый транспорт native5** panel reads an explicitly selected
retained native point. Select the exact execution/transport attempt/CalcJob and
frozen point, then supply its original native binding UUID and selected manifest
SHA-256 from the owning provenance record. These are constraints, not access
authority. There is no first/latest binding or native attempt default.

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

All routes require bearer authentication and use no-store. Retained metadata GETs
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
are compared in full, not only by hash. Old commit/index/phase row-range request
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

## Remaining producer gates

This increment implements R2 retained explicit-point reads only. Execution-wide
sources/brief (R1), native current bootstrap (R3), genuine retrieved rooted
snapshot (R4), and available native performance (R5) retain their separate owning
producer contracts. Sources/brief return explicit unsupported-contract staging;
no old-format fallback fills these gaps. Human/agent report read/attach remains
available. Explicit derive/comparison and actual used-value help remain separate.

Synthetic tests exercise public native5 readers, actual isolated child cleanup,
API transport and light source/SSR controls with explicitly synthetic RO facts.
They do not establish actual NFS/profile/installed/browser or scientific acceptance.
