# QCL-NEGF Portal

A web interface for quantum transport calculations run by [AiiDA](https://www.aiida.net/) and Slurm. The portal submits frozen scientific plans to the QCL-NEGF AiiDA plugin and reads execution state and provenance from AiiDA.

## Supported workflow

1. Resolve a scientific configuration with `qcl-negf plan CONFIG > plan.json` using the solver environment that will execute it.
2. Open the portal, enter its access token, upload the plan and select an approved solver Code.
3. Request resources within the configured limits. Each execution receives its own Slurm allocation through AiiDA.
4. Follow workflow state, inspect per-execution scientific results and process reports, download retrieved artifacts, or request cancellation.

The AiiDA plugin accepts ordinary frozen plans. Campaign plans and evidence-gated campaigns are not exposed by this interface. Solver configuration editing and numerical analysis are performed with the solver and `qcl-negf-results` tools. A finished process can still have an unsuccessful scientific outcome: inspect the result status and diagnostics.

The repository contains two modules with one release cycle: `frontend/` builds a static React/TypeScript application, and `src/qcl_negf_api/` provides the authenticated FastAPI gateway. The compiled browser bundle is included in the Python wheel and served from the same origin. There is one service in production; Node.js is a build dependency. The gateway owns authentication and request admission, while the AiiDA plugin owns execution and provenance. A dedicated thread owns AiiDA's profile, event loop and repository handles; HTTP worker threads do not share mutable AiiDA state. The portal has no scheduler, SSH client or execution queue. One preparation worker copies an immutable retrieved repository to temporary storage and asks `qcl-negf-results` to build a verified archive. Artifact selection and validation remain in results and its contracts.

The [actor lifecycle contract](docs/actor-lifecycle.md) defines the accepted target for caller deadlines, mutation outcomes, artifact/export leases and deferred cleanup. Implementation and fault-fixture verification remain separate: a caller deadline is distinct from actual backend completion.

## Install

Python 3.14 and an initialized AiiDA profile are required. Install a coordinated wheel set containing `qcl-negf-contracts`, `aiida-qcl-negf` and `qcl-negf-api` from the same platform release:

```console
uv venv --python 3.14
uv pip install --python .venv/bin/python --find-links /path/to/release/wheels qcl-negf-api==0.2.0
```

The portal wheel must contain the compiled browser assets. The build instructions below generate them. Package source metadata has no dependency on a sibling checkout. For NixOS deployment, use [qcl-negf-platform](https://github.com/Afonenko-QCL-NEGF/qcl-negf-platform), which provides the service identity, runtime credential configuration and AiiDA profile setup. Configure a TLS reverse proxy or an SSH tunnel for access to the loopback listener.

Create a token file readable only by the service account. Supply its path as `QCL_NEGF_API_TOKEN_FILE`; the token must contain at least 32 ASCII characters and should be generated randomly. Register the solver Code using the AiiDA plugin's setup instructions, then configure its UUID:

```console
export QCL_NEGF_API_TOKEN_FILE=/run/credentials/qcl-negf-api/token
export QCL_NEGF_ALLOWED_CODES=YOUR_REGISTERED_CODE_UUID
export QCL_NEGF_AIIDA_PROFILE=qcl-negf
qcl-negf-api --host 127.0.0.1 --port 8080
```

Run the portal as the same operating-system user that owns the AiiDA profile. `AIIDA_PATH` locates that profile if it is outside the default directory. The AiiDA daemon must be running to execute submitted workflows. The service fails at startup when authentication or approved-code configuration is absent.

An administrator can set `QCL_NEGF_SCRATCH_ROOT` to an absolute normalized path such as `/scratch/qcl-negf`. It designates worker-local scratch storage for QCLNEGFRunner and must be writable on every eligible compute node. The path is site configuration, is recorded by the AiiDA execution, and cannot be supplied by an HTTP client. The API host does not need this worker-local directory.

Place a TLS reverse proxy in front of the loopback listener. API requests require the bearer token, including read operations and the OpenAPI document. The browser keeps the token in memory only. Native browser downloads use a one-hour HttpOnly cookie capability bound to the exact artifact or archive; it cannot authorize other API operations. Cookies have SameSite=Strict and use Secure over HTTPS. Their URLs never contain the bearer token or the capability. This is a shared trusted-research-group interface: a token grants access to all QCL-NEGF workflows in the configured AiiDA profile, including submission and cancellation. Use separate profiles and service instances when separate access domains are needed.

## Versioned source documentation

Optional source documentation is served publicly on the same origin under
`/docs/<source_revision>/`. Configure both `QCL_NEGF_DOCS_BUNDLE_DIR` (an absolute
immutable bundle directory, normally a selected Nix output) and
`QCL_NEGF_DOCS_BUNDLE_SHA256` (the exact `bundle.json` SHA-256 from the retained
DOC04 artifact receipt). The source revision comes from the verified manifest.
The service checks the existing Contracts schema, source graph identities,
public inventory, payload hashes and fragment identities once at application
creation, and retains the admitted bytes. It serves only that revision and
inventory; historical revisions never fall back to the configured bundle.
Manifest size is limited to 2 MiB; at most 10000 files, 16 MiB per file and
256 MiB combined; fragments are limited to 256 entries and 32 KiB each.
Symlinks, private paths and traversal are refused. No source URLs are fetched.

`GET /api/v1/config` includes typed `documentation` availability and exact
researcher/developer/operator links. Missing or invalid optional documentation
leaves the API and AiiDA service available. Reserved valid docs requests then
return 503 with no-store; malformed/unlisted/unknown-version paths return 404
when applicable. Available GET/HEAD responses use exact media types, SHA-256
ETags, conditional 304 and immutable versioned caching. Existing CSP and bearer
requirements for `/api/v1/` remain in force. The UI shows source documentation
availability; it does not claim those docs describe a selected run or establish
publication, browser rendering, convergence or scientific acceptance.

## Resource and data limits

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `QCL_NEGF_MAX_MACHINES` | 1 | Maximum machines per execution |
| `QCL_NEGF_MAX_PROCESSES_PER_MACHINE` | 1 | Maximum MPI processes per machine |
| `QCL_NEGF_MAX_CORES_PER_PROCESS` | 64 | Maximum cores per process |
| `QCL_NEGF_MAX_WALLCLOCK_SECONDS` | 604800 | Maximum wall time per execution |
| `QCL_NEGF_DEFAULT_MEMORY_KB` | 4194304 | Initial memory request (KiB), also used when omitted in API requests |
| `QCL_NEGF_MAX_MEMORY_KB` | 134217728 | Maximum requested memory per execution (KiB) |
| `QCL_NEGF_MAX_BODY_BYTES` | 2000000 | Request body limit, including chunked requests |
| `QCL_NEGF_MAX_DOWNLOAD_BYTES` | 200000000 | Maximum individual retrieved-file download; complete archives have separate storage admission |
| `QCL_NEGF_EXPORT_DISK_BYTES` | 107374182400 | Export storage admission budget, including preparation and retained archives (100 GiB) |
| `QCL_NEGF_EXPORT_TTL_SECONDS` | 86400 | Ready archive retention after preparation (24 hours) |

These are admission limits; Slurm performs resource allocation. Each execution uses one machine and one process; these two limits must remain one. Parallel work is distributed as independent executions across Slurm nodes. Configure core, memory and time ceilings for the cluster partition. The initial memory request is 4 GiB, is configurable on the control host and can be adjusted in the submission form. Match these defaults and ceilings to the actual compute-node memory.

Only artifacts registered in the workflow's AiiDA retrieved repositories can be downloaded. Arbitrary host paths are not accepted. Large physical arrays remain available through the scientific result artifacts and `qcl-negf-results`; the browser displays structured result summaries and reports.

Use **Complete result archives** to prepare one `.tar.xz` file for an execution with the `science` or `full-state` profile. The portal displays preparation progress, finalized size, SHA-256 and snapshot identity. Downloads go directly through the browser's download manager; JavaScript never accumulates the complete file in a Blob. The authenticated endpoint supports one HTTP byte range and a SHA-256 ETag for `If-Range`. Browser transfer progress and resume controls depend on the browser. If a resume needs fresh authorization, click **Download archive** again. Renewing authorization does not prepare a new archive.

Each new archive includes the exact frozen plan bytes. The AiiDA actor reads the retrieved worker plan and checks it against the workflow input; only an absent worker plan permits fallback to the file-backed input. The archive manifest records its source, SHA-256 and size. A missing, oversized, corrupt or mismatched plan refuses preparation before artifact copying. Numerical text is preserved without JSON reserialization.

Preparation refuses the complete retrieved dataset before copying when the conservative reservation (four times its inventoried bytes plus 256 MiB) exceeds the configured admission budget or observed free temporary storage after the results exporter's 64 MiB filesystem reserve. After retrieving, the portal forwards the remaining reservation to the results exporter's write guard, bounded by other live export reservations and current free space. This guard counts pinned/derived files, compressed output and the receipt tail together. Progress checks the retrieved copy plus reported spool bytes; final accounting includes every retained output file, including the receipt, before publishing `ready`. The portal refuses an oversized export without discarding scientific files to fit it. Only one archive is prepared at a time. Retained files count against subsequent admission; expired exports are removed on the next export operation when no download holds them open.

These are logical-byte guards and free-space observations, not a filesystem quota. Filesystem metadata, allocation rounding and concurrent external writers can still exhaust physical storage; preparation then fails and cleans temporary output. Deployments requiring a hard aggregate limit should apply a filesystem quota to the service's temporary storage. Service restart clears this temporary cache and invalidates capabilities, so a transfer cannot resume across a restart.

The control-host fallback remains under **Export complete repositories**. It gives the retrieved repository UUID for each execution. Run the displayed command as the AiiDA profile owner:

```console
verdi -p qcl-negf node repo dump RETRIEVED_UUID ./retrieved-run
qcl-negf-results export ./retrieved-run/result ./exports --profile science
```

The output directory for `verdi node repo dump` must not exist. Choose a different directory for each execution. `qcl-negf-results export` creates one checksummed archive without changing native scientific values. Its default status is `running`; supply `--status completed` only when that status is established by the scientific result. The `full-state` profile also retains full physics and recovery state. Copy the archive and its receipt using your normal file-transfer tool and verify them with `qcl-negf-receive` on the receiving machine. Export success establishes archive integrity, not numerical convergence or scientific acceptance.

## HTTP API

Active closed-prefix history and exact retained execution performance are
documented in [live-performance.md](docs/live-performance.md). These optional
read-only views preserve coverage gaps and unknown measurements independently
of scientific acceptance.

All `/api/v1/` operations require `Authorization: Bearer TOKEN`, except GET/HEAD of an exact download previously authorized by its short-lived cookie. The authenticated OpenAPI schema is at `/api/v1/openapi.json`.

| Method | Path | Operation |
| --- | --- | --- |
| GET | `/healthz` | Process liveness and version |
| GET | `/api/v1/config` | Approved Code UUIDs and admission limits |
| GET | `/api/v1/runs?limit=25&offset=0` | Paginated QCL-NEGF workflows |
| POST | `/api/v1/runs` | Submit `{plan, code_uuid, resources, label}` |
| GET | `/api/v1/runs/{uuid}` | State, scientific results and child calculations |
| GET | `/api/v1/runs/{uuid}/report` | Process report entries |
| POST | `/api/v1/runs/{uuid}/agent-reports` | Store `{report: "exact UTF-8 JSON text"}`; return 201 stored file receipt |
| GET | `/api/v1/runs/{uuid}/agent-reports?limit=20&offset=0` | Paginated stored agent report receipts |
| GET | `/api/v1/runs/{uuid}/agent-reports/{report_uuid}` | Exact `agent-report.json` attachment |
| POST | `/api/v1/runs/{uuid}/kill` | Request cancellation |
| GET | `/api/v1/runs/{uuid}/artifacts` | Retrieved file inventory |
| POST | `/api/v1/runs/{uuid}/artifact/authorize?execution_id=…&path=…` | Authorize a native browser download of one inventoried file |
| GET/HEAD | `/api/v1/runs/{uuid}/artifact?execution_id=…&path=…` | Stream an inventoried file, with single-byte-range support |
| POST | `/api/v1/runs/{uuid}/exports` | Prepare `{execution_id, profile, attempt?, calcjob_uuid?}`; default profile `science`, alternative `full-state` |
| GET | `/api/v1/exports/{export_id}` | Preparation state/progress, or v3 archive receipt |
| POST | `/api/v1/exports/{export_id}/authorize` | Set a scoped download cookie and return its credential-free URL |
| GET/HEAD | `/api/v1/exports/{export_id}/download` | Stream the archive, with Range and If-Range support |

Artifact authorize and GET/HEAD accept optional positive `attempt` and full
`calcjob_uuid` query selectors. Both selectors constrain the same retrieved child.
Bearer requests and authorize retain the selected-attempt shortcut, including
legacy single-attempt workflows. Authorize resolves it once and returns an exact
URL containing both selectors. The native browser download must follow that
returned URL: changing or removing either selector invalidates its scoped cookie
(401 before artifact metadata or payload access). Reusing the old selector-less
URL with a new cookie is therefore refused. Exact metadata, download limits,
HEAD and Range headers, and streamed bytes all refer to the same child even if
published selection changes. HEAD reads cached metadata without opening payload.
Missing exact children/inventory/path return 404; ambiguous selection, incomplete
inventory and malformed selectors/path return 422. New Portal requires the
coordinated AiiDA `get_artifact_metadata` service operation.

Agent reports are separate immutable UTF-8 JSON files, with a 262144-byte raw limit. POST accepts only the strict `report` string envelope, preserves the validated bytes, and returns 201 only after AiiDA stores the file. Repeated POST can create another UUID; submission is not retried automatically. Collections contain receipts and use limits 1–100 with nonnegative offsets. File reads return the original JSON bytes with a fixed attachment filename and Content-Length. These three operations require the bearer token and use no-store responses; missing/wrong-anchor files return 404, invalid/conflicting/corrupt reports 422, and backend unavailability 503. They do not change workflow outputs, process reports or scientific assessments.

The browser displays **Agent reports for this run**, with author question text, exact run/definition/variant/attempt/CalcJob references, and conclusion, reasoning and limitations as escaped text. Empty used-run references are shown explicitly. Author prose does not change machine scientific statuses. This partial run view has no stable research card or canonical question relation; those require the R01 card/catalog interface. The author question snapshot is not proof of a canonical research question. Existing bearer credentials remain in memory only, and the complete interface retains its light theme.

The `plan` field is a string containing the original frozen JSON file, not a parsed nested object. This preserves the exact numerical representation and fingerprint across the browser, API and AiiDA repository.

Submission returns HTTP 202 once AiiDA accepts the workflow. Invalid plans and resource requests return 422; an unapproved Code returns 403; broker unavailability returns 503. Submission is not automatically retried: submitting the same plan again creates a new provenance record. A cancellation request is asynchronous; refresh the workflow to see its eventual state.

Export requests accept an optional strict positive integer `attempt` and full UUID string `calcjob_uuid` (omitted or null means unspecified). Both selectors must describe the same child. Omitted selectors use the producer's published selection or the single legacy child; ambiguity or malformed metadata is refused. Invalid or mismatched selectors return 422; unavailable children return 404. Selection is resolved once before admission and pinned through inventory, exact frozen plan and payload reads. The browser sends the attempt and CalcJob UUID from the chosen child and displays the returned identity and recorded scientific status.

Export requests return 202 with an `export_id`, preparation `state`, selected `attempt`, canonical `calcjob_uuid` and frozen scientific `status`. The exact run/execution/attempt/CalcJob/profile reuses an unexpired preparation or ready archive only when the newly observed status also matches. A changed child or status requires a new export; already issued IDs retain their original status and bytes until expiry. A busy preparation worker returns 409; insufficient storage admission returns 507. Failed preparation is reported explicitly and never publishes a download. No numerical solver is invoked during export. Status comes from the selected child's recorded scientific result, independently of process exit status; missing result metadata is `unknown`, and present invalid metadata is refused. `running`, `paused` and `unknown` remain nonterminal for export completeness. A ready archive establishes transport integrity, independently of scientific acceptance.

Deploy the coordinated AiiDA `get_export_selection` and `list_execution_artifacts` operations before upgrading this consumer, then restart Portal. Missing operations return 503; there is no fallback to all-attempt inventory. The cache is process-local and restart clears old entries and download capabilities. The producer contract is documented in [export-selection.md](https://github.com/Afonenko-QCL-NEGF/qcl-negf-aiida/blob/codex/review-closure-export-selection/docs/export-selection.md).

Failed preparation can be retried by an explicit request; the portal never retries it automatically. Shutdown is cooperative: copying and compression notice cancellation at progress callbacks, while validation or compaction may continue until their next callback. The target lifecycle contract requires cleanup after actual writer and download-lease completion, with an explicit `stopping` result when the close response deadline expires. A close timeout leaves cleanup pending and does not establish that the backend or process has stopped.

## Development and build

The [qcl-negf integration repository](https://github.com/Afonenko-QCL-NEGF/qcl-negf) provides a flat set of component submodules and one generated `uv.lock` for the Python 3.14 workspace. Component versions are declared in their package metadata. Git submodule entries select component sources; Python dependencies are resolved once in the integration workspace. This repository does not carry a second Python lock or internal Git revision manifest.

Use Node.js 24 for the browser build. From the integration checkout, synchronize its locked environment, build the frontend, then run the backend checks:

```console
uv sync --locked --all-packages --all-groups --all-extras
cd components/qcl-negf-portal
deno task test:frontend
cd ../..
uv run --locked --package qcl-negf-api python -m pytest components/qcl-negf-portal/tests
uv run --locked --package qcl-negf-api ruff check components/qcl-negf-portal/src components/qcl-negf-portal/tests components/qcl-negf-portal/hatch_build.py
uv run --locked --package qcl-negf-api python -m build --no-isolation components/qcl-negf-portal
```

The frontend can also be checked independently from this repository with `deno task test:frontend`. It installs `frontend/package-lock.json` with `npm ci`, runs browser client tests and TypeScript checks, and builds the static bundle. It does not install unpublished Python dependencies. npm receives only `PATH`, `HOME`
and the optional `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` variables and their lowercase
variants. The task reads those named variables with scoped permissions and clears the
child environment, keeping runner-specific native loader settings out of Node.
Other npm configuration should be supplied through the project's or user's npm config,
not arbitrary inherited environment variables. This does not change the runner or the
Python environment. The command-contract regression uses a local npm substitute:

```console
python3 -m unittest discover -s tests -p test_ci_environment.py
```

It checks argument order, working directory, the explicit environment and exit propagation
without installing packages, contacting a registry or building frontend assets.

`npm run build` writes assets to `src/qcl_negf_api/static/`, which the wheel includes. The current release hook rejects missing index/JS files; it does not establish that existing assets match the current TypeScript, lock or build recipe. For a standalone source checkout with prepared locked dependencies, build the frontend immediately before the wheel:

```console
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
uv build --wheel --no-sources --out-dir dist
```

These are build-stage commands; dependency preparation and build cost need their own admission. Retain the exact source/lock identities and wheel SHA-256. The proposed [package identity contract](docs/package-identity.md) specifies the DEV20 generated receipt and fail-closed stale/output checks. Its wrapper independently clears and recreates only the selected, safely owned generated output before TypeScript/Vite; a zero-exit build without new files in that root must fail. Its generator and stronger wheel guard are not implemented by this documentation change. Until that implementation and route-specific verification exist, a successful direct wheel build alone is not source-freshness evidence.

Editable development installation permits the API to run before the browser is built and makes no release browser-asset claim. Workspace editable imports, standalone wheels/editables, Julia Core tag/path resolution and Nix closures require separate source/import receipts; the shared `0.2.0` version does not transfer acceptance. The package identity document defines the required fields and boundaries.

Generated assets and dependency directories are ignored by Git. `nix/frontend.nix` builds browser assets with the verified npm dependency hash committed alongside the recipe, replacing only Vite's output directory with `dist`. Its source filter excludes `node_modules`/`dist` basenames recursively and applies `cleanSourceFilter`. The platform copies the complete output into the API wheel build from the same root `uv.lock`; Python dependencies are not declared again in a separate Nix package definition. The proposed receipt has an explicit `nix-dist` mode for this existing transform. Compatibility is limited to source trees whose full input inventory survives the Nix projection; a source file lost by filtering must fail verification rather than be silently ignored. Native acceptance is separate from the standalone route. After updating `frontend/package-lock.json`, run `deno task lock:nix` in the Nix development shell to regenerate `nix/generated-npm-hash.json`; do not edit its hash manually. Root release construction continues to call its own frontend build before packaging. All routes retain local same-origin assets and the fixed light theme.

For local browser development run the API on port 8080 and `npm --prefix frontend run dev`; Vite proxies `/api` to the local API. Production serves the built files directly; it does not run the Vite development server.

The component CI checks the frontend on trusted pushes to `main` and manual dispatch, using a self-hosted runner labelled `qcl-negf-ci`. Backend tests and package integration run in the root repository's locked Python workspace. Pull requests from unknown contributors must be reviewed before running their code on the private runner.

## Repositories

- [QCLNEGF.jl](https://github.com/Afonenko-QCL-NEGF/QCLNEGF.jl): numerical quantum transport library.
- [QCLNEGFRunner.jl](https://github.com/Afonenko-QCL-NEGF/QCLNEGFRunner.jl): scientific configuration, frozen plans and solver execution.
- [qcl-negf-aiida](https://github.com/Afonenko-QCL-NEGF/qcl-negf-aiida): execution, provenance and Slurm integration.
- [qcl-negf-results](https://github.com/Afonenko-QCL-NEGF/qcl-negf-results): scientific artifact validation and bounded exports.
- [qcl-negf-platform](https://github.com/Afonenko-QCL-NEGF/qcl-negf-platform): NixOS services and integration releases.

Released under the MIT license. See [LICENSE](LICENSE).

## Retained plan dependencies (read only)

Run detail consumes optional `plan_structure` from the existing authenticated
`GET /api/v1/runs/{uuid}`. It displays the exact run UUID/fingerprint, inclusion
tree (shared execution membership allowed), execution/definition/variant and
operation, and only explicit `predecessor → point` edges with branch, order,
initialization, temperature in K and voltage per period in V. Independent
points have no edge. Point tables show at most 100 rows per execution/page,
with totals and local previous/next controls; all rows remain reachable.

Observed attempts retain separate UUID/attempt/process/exit entries and local
anchors. The flattened descendant inventory is not a set of direct CALL links.
A planned execution without a child says `attempt не создан`; point execution
and scientific assessments stay unknown without exact retained evidence.
Selected execution results are never assigned to all attempts or points.
Missing/unsupported/malformed structure shows an availability reason while
ordinary detail, observed attempts, results and help remain usable. Run UUID
keys reset local page state and the existing refresh sequence rejects stale
requests. Presentation uses React escaping and the fixed light theme.

This source slice adds no submission, resolver, actor method, HTTP endpoint,
workflow editor, physics or result derivation. Stage/variant forms, research
card association, saved plots, resource admission and native browser acceptance
remain separate work. Source fixtures and server rendering do not establish
browser keyboard/theme/async-race acceptance or production activation.


## Native research cards

The light **Исследования** panel creates and edits canonical title/goal/question
metadata as immutable native AiiDA file versions. History and exact original JSON
downloads retain each version UUID/hash. A stable card UUID is its first native
version UUID; identical text creates distinct cards. Existing frozen plans and
scientific results are unchanged. Plain text preserves Markdown source and
escapes author HTML. Scientific acceptance is independent of all card metadata.

An explicit confirmation attaches the **whole** selected saved run with its
frozen fingerprint and all variants/observed attempts, including failures.
Reports are selected by exact anchor/report UUID and attached with their immutable
hash. The AiiDA owner rechecks every used run's explicit membership and current
frozen selectors before storing. Different card versions may compare runs within
one stable card. Historical runs/reports without an explicit relation remain
unassociated; labels, matching questions and `question_snapshot` infer nothing.
Use the existing workflow view for variants, attempts and scientific summaries.

Authenticated no-store `/api/v1/research-cards` exposes paginated heads, versions,
file downloads, runs and analyses. Writes require a canonical client request UUID
and exact pinned version/base. The strict JSON envelope carries the original
card JSON as a string; Contracts validates its exact UTF-8 bytes (≤65536) before
dispatch and again in the owner. Responses contain detached bounded receipts
(≤1 MiB), never arrays. GET `/api/v1/research-card-requests/{request_uuid}` reads
stored evidence. Same key/meaning replays; changed meaning conflicts. Known
pre-store refusal is 404/409/422; known store is 201 and replay 200. Unexpected
started write failure/deadline remains unknown with the existing actor mutation
fence. The panel retains request/diagnostic IDs and offers explicit readback;
not_found does not prove no effect, and readback does not unlock or retry writes.

This source slice does not configure/resolve scientific plans, create variants,
compute new reports, run solvers, or establish native persistence/browser/science
acceptance. The fake-ORM/API/actor/UI controls and isolated SQLite persistence
check require their separately booked prepared-environment gates.

## Bounded scientific configurator

The submission panel also offers a short template form, one explicit controlled
variant, and the complete `qcl-negf-study-v2` YAML editor. The authenticated
`GET /api/v1/configurator/catalog` and explicit
`POST /api/v1/configurator/preview` use the configured Runner resolver. Empty
form fields retain the template; the browser supplies no scientific defaults.
YAML is authoritative and can be downloaded with its original text. Returning
to the short form requires an explicit draft reset when YAML cannot be preserved.

Preview shows exact Research protocol version/file hashes, planned points and
operations, variant overrides, complete Runner used/default/input projections
including per-point sections, and owning rule sources. Missing runtime identity
or input origin remains unknown. Resource estimates are separate from resource
admission and scientific acceptance. Large integer metadata is displayed using
Runner's lossless decimal tag; frozen plan bytes are never reformatted.

Review the preview, confirm the selected Code/resources/label, and explicitly
transfer its exact frozen plan to the existing submit form. Submission checks its
SHA-256 and sends the original string. Draft, template, source, Code, resource or
label changes invalidate that review; no automatic submit or retry occurs.
Unknown submission outcomes retain an in-memory mutation fence with operation
diagnostics. Inspect inventory with the operator before any further write.
After a known successful submit, the existing research-card action can explicitly
attach the exact run UUID. No card association is inferred from a label.

Unavailable planner/catalog configuration leaves raw plan upload and historical
run reads available. Runtime availability comes from `/api/v1/config`; an absent
planner version is shown as unknown. This interface does not alter the active
SCI4 run, install a planner, grant resources, or establish scientific acceptance.
