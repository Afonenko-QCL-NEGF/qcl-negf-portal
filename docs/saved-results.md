# Saved transport repository views

The run detail includes **Сохранённый транспорт**. Select an exact execution,
attempt and CalcJob, then explicitly choose a provider. Retrieved repository
keeps the original flow; registered NFS requires loading the retained catalogue
and choosing its exact source UUID/SHA (even a single entry has no default).
Then read metadata, select a point/commit,
describe semantic views and request native indices or ranges. Historical failed
attempts remain selectable. No first-child, latest-file or matching-title default
is used. Owning Results accepts exact positive source row attempts up to the
selected provider attempt, retaining older completed rows and explicit borrowed
checkpoint sources without rewriting their attempt metadata. Plan definition/variant mapping comes from the retained source plan.
No solver, optical calculation, export archive or derived scientific job starts.

The retrieved provider uses existing AiiDA repository owner-thread operations.
The optional registered NFS provider consumes retained AiiDA L1 source records
and uses the same bounded Results child on the Platform read-only alias. It
never registers sources or copies the analysis owner into temporary storage.
Actual namespace BIND, read access, source quiescence and retained closure hold
remain administrative/native prerequisites; source implementation does not
establish these facts or close L2/R04. Existing retrieval and protected
calculations are untouched. There is no browser host path or provider URI input.

## Authenticated semantic API

All endpoints require bearer authentication and use no-store:

- `GET /api/v1/runs/{run_uuid}/saved-results/sources` (retained catalogue)
- `GET /api/v1/runs/{run_uuid}/saved-results/brief`
- `GET /api/v1/runs/{run_uuid}/saved-results/manifest`
- `POST /api/v1/runs/{run_uuid}/saved-results/slice`

Catalogue requires only the exact four-field selection and rejects other query keys.
It returns a complete bounded list (at most 256) with actual copy status and
access `not_checked`; corrupt/oversized catalogues fail as a whole.

GETs require explicit `execution_id`, positive `attempt`, full `calcjob_uuid`.
Brief accepts an optional opaque `cursor`. Manifest additionally requires
`point_id`, `source_commit_sha256`, `metadata_sha256`. Slice accepts only these
seven provenance selectors plus semantic `view_id` and native `selection`; run
UUID stays in the URL. Selection names every semantic axis as a zero-based
integer or `{start,stop,step}` (exclusive stop, positive step). No clipping,
sampling choice, unit conversion or numerical default is introduced by Portal.
Complex real/imaginary components are distinct explicit view IDs from Results.

Responses retain the owning Results DTO and add `provider_selection` (exact
retrieved attempt), `metadata_sha256`, `source_attempt`, transport limits/copy
counters and page metadata. The nested slice `selection.attempt` is the saved
source attempt: a borrowed checkpoint is not relabelled as the provider attempt.
All recorded native identities must match the expected binding when present;
genuinely missing legacy provider fields are disclosed by Results. Source
revisions are retained provenance, never the current Portal checkout revision.

The provider pins exact plan and `result/series_result.json` bytes plus selection
in `metadata_sha256`. It follows only the row's explicit `data.result_commit`,
then the selected commit's single declared `physics.analysis` and its declared
JSON dependency closure. If the series omits a commit SHA, brief captures only
that page's declared small commit bytes and labels the resulting digest
`provider_captured_sha256`; a recorded digest is labelled `series_recorded`.
This is a transport pin, not scientific acceptance. A changed metadata digest
or commit SHA returns identity_mismatch and requires refreshing the selection.
No directory scan chooses an analysis file; no full-state fallback is opened.

Brief is an owning Results metadata projection and never calls the HDF reader.
Its point pages contain at most 256 rows and `next_cursor`, `total`,
`complete_page`. Cursors contain the same metadata digest and explicit offset;
a later page cannot silently use new plan/series bytes. Commit pins are separate
from this series snapshot. Pages are shown individually; J(V) is never joined
across page boundaries. Manifest's finite semantic view list also reports page
fields (the current allowlist fits one page). A partial page is labelled partial.

## Bounds and lifetime

One analysis request is active at a time per existing single API worker. A busy
request refuses without queue/retry. The ten-second monotonic deadline includes
selection, metadata, owner copy and analysis; cancellation/disconnect/timeout
signals cooperative repository-copy cancellation. Existing actor I/O can outlive
a caller deadline: its owner-thread lease must close before cleanup and gate
release, so this is not a guaranteed ten-second kernel termination promise.
Caller cleanup waits at most one additional second; shutdown also uses this
bounded wait. If storage/actor I/O remains stalled, the actual concurrent future,
gate, leases and temporary inputs remain owned until real completion and further
saved reads fail closed. No cancelled asyncio waiter is mistaken for completion.
A cleanup failure disables further saved reads; existing run/configurator/card
routes remain separate. Kernel D-state termination cannot be promised.

Wrapper future completion does not prove actor lease cleanup. The Portal actor's
optional close observer reports every actual CloseResult even if an original
read error is preserved, and unknown artifact acquisition is also disclosed.
Any pending, failed or unknown close permanently disables this optional consumer
and retains its busy marker. Later actual actor cleanup cannot automatically
reenable it. The actor continues to own exact-once physical context exit; the
consumer adds no polling of ORM state, force-close or second execution owner.
This also covers a quick metadata read whose context exit exceeds the actor's
short close-response deadline while the saved future itself has already ended.

The provider checks inventory size and declaration before copying payloads:
metadata totals at most 1 MiB (retained plan, series, selected commit/dependencies),
selected analysis owner at most 64 MiB, logical temporary admission at most
128 MiB, final JSON at most 2 MiB. Only that owner and its required small JSON
closure enter a private temporary directory. Each copied owner/dependency is
verified against declared size and SHA before local analysis. Repository leases
close in context managers and temporary files are removed after child completion,
including cancellation and exceptions. These are ordinary logical-byte guards,
not filesystem quotas or measured physical I/O.

Describe/slice use a fixed internal one-shot Python process in the existing
prepared package. Before loading the owning Results reader, it sets one allowed
CPU affinity, CPU-time limit ten seconds, virtual address-space limit 512 MiB
and finite output-file limit. Its environment contains only locale/PATH and
one-thread BLAS/OMP settings. Parent concurrently drains stdout/stderr with
2 MiB/4 KiB bounds and kills/reaps the process group on cancellation, deadline,
overflow or exit before deleting temporary inputs. No shell, preexec hook,
sudo, service, installation or dependency fallback is involved. AS is not RSS;
this cap may refuse a prepared runtime whose mappings exceed it. No cap is
raised automatically. Kernel I/O/RSS remain `not_measured` in disclosure.

Owning `SavedViewLimits` additionally enforce selected allocation 1 MiB including
coordinates/weights, cells 65,536, lines 4,096, wave/population states 12, matrix
subsets 64×64 and finite native object inventory. The Results reader separately
charges owner SHA and HDF delivery. `repository_copied_bytes` reports provider
copy cost; it must not be confused with only the returned selected payload or
with Results' I/O counters. A small slice can require a whole selected-owner
copy/SHA pass. Oversized data returns budget_exceeded and may require an explicit
smaller selection; Portal never reduces a scientific grid or modifies raw data.

## Display and scientific boundaries

J(V) segments are supplied by Results from the complete frozen point order.
Missing J, missing planned rows, failed/unaccepted rows and branch changes retain
gaps; unaccepted/unknown currents appear as isolated diamond markers. Portal
does not recompute acceptance. Current convention is positive electron flow
along +z; no monotonicity or bias symmetry is asserted.

Potential/density plots retain saved units, signed finite values and native axes.
Spatial-energy density is n(E,z), not spectral A(z,E). Trace spectra keep an
explicit k selection. Heatmap cell boundaries are presentation midpoints between
nonuniform saved coordinate centers, not integration weights. Negative/positive
values use signed colors; finite zero differs from native NaN/+Inf/-Inf (null
with an exact invalid mask/reason). Raw native cell tables are available in pages. SVG positions divide by a finite
domain scale before subtraction, so opposite signed extremes and huge singleton
line/bar values do not overflow their display coordinates. Midpoint edges use
half-sums; any unrepresentable/indistinguishable cell boundary gives an explicit
Graph unavailable message with the unchanged ordered value/axis/mask table.
Every generated position is checked finite before SVG creation. This changes
only screen geometry, never native values, units, selected cells or physical
normalization.

Wavefunctions keep saved state IDs, explicit real/imag component, basis and gauge.
Their curves do not match degenerate states across variants. Effective levels
are saved projected k=0 Hamiltonian levels, not interacting spectral peaks.
Population bars/table show saved real diagonals in the named basis; complex
matrix components remain separately selectable. Fractions and their denominator
are displayed only as returned by Results from saved ionized target-doping
inputs. Portal never normalizes by a selected subset or invents a donor density.
Missing target inputs leave raw populations available and fractions unavailable.

Raw process/point/converged/quality/assessment fields remain separate from
metadata/SHA integrity, rendering, discretization and experimental validation.
Missing fields stay unknown. `scientific_accepted:false` is not reinterpreted as
proof of a physics failure. Renderer readiness and exact transport do not
establish scientific acceptance.

`QCL_NEGF_SAVED_RESULTS_ENABLED=false` disables this optional consumer; the default
is enabled and unsupported/missing producer/storage returns an honest refusal.
No server activation or historical artifact readiness is established by source.
Focused synthetic backend/frontend controls live in `tests/test_saved_results*.py`
and `frontend/tests/saved-results*.test.ts`; run only in the separately prepared,
finite shared verification gate, without package installation or production HDF.


## Registered NFS L1 binding and identities

Old requests without `provider` retain `aiida_retrieved` behavior and the exact
old metadata digest. Explicit retrieved provider rejects source UUID/SHA fields.
`nfs_registered` requires both canonical lowercase `source_uuid` and
`source_sha256`. These accompany every brief/manifest query and slice body.
Every response repeats `provider_source`; source swaps, including equal record
SHA with different UUID, invalidate metadata SHA/cursor and frontend generation.
No source, provider or repository fallback follows an identity/access error.

Catalogue and NFS brief use only retained AiiDA records/UTF-8 plan and metadata.
They do not stat/resolve/open the data alias, inspect mountinfo or open HDF.
Exact bytes and descriptor hashes are verified before the owning Results brief.
NFS metadata SHA is SHA256 of the `qcl-negf.portal-saved-nfs.v1\0` domain followed
by 8-byte big-endian length-framed canonical selection, canonical provider_source,
original input-plan bytes and retained series bytes. Raw plan is not reserialized.
Registered incomplete snapshots show every frozen scalar/status/gap; only captured
record points receive analysis action pins. `snapshot_complete` is independent
of page completeness and scientific acceptance. Reserved `_aiida/` metadata
participates in source digest but never becomes an NFS pathname/dependency.

`provider_selection` remains the selected current AiiDA child. The source native
identity and explicit source CalcJob relation are projected separately. Expected
identity for Results comes from the registered native commit, with its SHA.
Imported/borrowed source attempt1 stays attempt1 under provider attempt2. Missing
native run/CalcJob UUID stays null in slice selection; it is never stamped from
the current child. Frontend checks native identity against the pinned brief and
manifest while retaining strict retrieved selection validation.

`QCL_NEGF_SAVED_RESULTS_BINDING_FILE` is optional. At startup the API snapshots
only an immutable canonical regular root-owned `/nix/store/...` JSON file, no
symlink, no group/other write, at most 16 KiB. The exact eight fields are
`namespace_id`, `storage_identity`, `root`, `allowed_computer_uuids`,
`retention_policy_id`, `retention_owner_id`, `hold_ref`, `access_binding_ref`.
Root is exactly `/run/qcl-negf-saved`. Missing/invalid binding disables NFS payload
access while catalogue, retained brief and retrieved views remain independent.
All six identity/retention refs and computer allowlist are matched before spawn.
Private binding refs and absolute paths are not sent to the browser.

Only the bounded child probes mountinfo and requested path ancestry. It requires
an exact read-only NFSv4 alias, rejects symlinks and foreign/RW nested mappings,
and gives the actual committed path to owning Results `AnalysisReader` with its
final nofollow regular-file checks. Request JSON lives in a private local `/tmp`
spool; data root is the server-built alias plus stored relative result root.
Repository copied bytes and payload temp copied bytes are zero. Small request
spool bytes and Results logical delivered I/O counters are distinct from storage
copy, network or kernel accounting. Slice can hash the full <=64 MiB owner even
for a small selection; manifest does not claim owner SHA/full native validation.

Catalogue, brief and both payload providers share one admission slot. Unknown
actor completion or unconfirmed child cleanup permanently disables this optional
consumer. Unreaped child ownership retains its local spool; a completed waiter
never proves cleanup. Hard NFS kernel I/O can outlive SIGKILL/HTTP deadline, and
no replacement child is admitted. Limits remain wall10s, one CPU, CPU10s,
AS512MiB (virtual address space, not RSS), metadata1MiB, owner64MiB, delivered
I/O128MiB, selected data1MiB/65536 cells, response2MiB and stderr4KiB. Actual native
mount/ACL/storage witness, no writer/rename/prune and whole closure/borrowed root
hold require a separate BIND packet. No native NFS, full GUI or science acceptance
follows from source controls.
