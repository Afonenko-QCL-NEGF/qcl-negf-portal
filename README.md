# QCL-NEGF Portal

A web interface for quantum transport calculations run by [AiiDA](https://www.aiida.net/) and Slurm. The portal submits frozen scientific plans to the QCL-NEGF AiiDA plugin and reads execution state and provenance from AiiDA.

## Supported workflow

1. Resolve a scientific configuration with `qcl-negf plan CONFIG > plan.json` using the solver environment that will execute it.
2. Open the portal, enter its access token, upload the plan and select an approved solver Code.
3. Request resources within the configured limits. Each execution receives its own Slurm allocation through AiiDA.
4. Follow workflow state, inspect per-execution scientific results and process reports, download retrieved artifacts, or request cancellation.

The AiiDA plugin accepts ordinary frozen plans. Campaign plans and evidence-gated campaigns are not exposed by this interface. Solver configuration editing and numerical analysis are performed with the solver and `qcl-negf-results` tools. A finished process can still have an unsuccessful scientific outcome: inspect the result status and diagnostics.

The repository contains two modules with one release cycle: `frontend/` builds a static React/TypeScript application, and `src/qcl_negf_api/` provides the authenticated FastAPI gateway. The compiled browser bundle is included in the Python wheel and served from the same origin. There is one service in production; Node.js is a build dependency. The gateway owns authentication and request admission, while the AiiDA plugin owns execution and provenance. A dedicated thread owns AiiDA's profile, event loop and repository handles; HTTP worker threads do not share mutable AiiDA state. The portal has no scheduler, SSH client or execution queue. One preparation worker copies an immutable retrieved repository to temporary storage and asks `qcl-negf-results` to build a verified archive. Artifact selection and validation remain in results and its contracts.

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

Preparation refuses the complete retrieved dataset before copying when the conservative reservation (four times its inventoried bytes plus 256 MiB) exceeds the configured admission budget or free temporary storage. This reservation accounts for the retrieved copy, exporter spool, derived objects and final archive; it is not a filesystem quota. Archive-byte progress also aborts if the reserved space is exceeded. Other disk users or a derived object's expansion can still exhaust the filesystem, which fails preparation and removes temporary output. Deployments requiring a hard aggregate limit should apply a filesystem quota to the service's temporary storage. The portal does not discard source files to fit the budget. Only one archive is prepared at a time. Retained archives count against subsequent admission; expired archives are removed on the next export operation when no download holds them open. Service restart clears this temporary cache and invalidates capabilities, so a transfer cannot resume across a restart.

The control-host fallback remains under **Export complete repositories**. It gives the retrieved repository UUID for each execution. Run the displayed command as the AiiDA profile owner:

```console
verdi -p qcl-negf node repo dump RETRIEVED_UUID ./retrieved-run
qcl-negf-results export ./retrieved-run/result ./exports --profile science
```

The output directory for `verdi node repo dump` must not exist. Choose a different directory for each execution. `qcl-negf-results export` creates one checksummed archive without changing native scientific values. Its default status is `running`; supply `--status completed` only when that status is established by the scientific result. The `full-state` profile also retains full physics and recovery state. Copy the archive and its receipt using your normal file-transfer tool and verify them with `qcl-negf-receive` on the receiving machine. Export success establishes archive integrity, not numerical convergence or scientific acceptance.

## HTTP API

All `/api/v1/` operations require `Authorization: Bearer TOKEN`, except GET/HEAD of an exact download previously authorized by its short-lived cookie. The authenticated OpenAPI schema is at `/api/v1/openapi.json`.

| Method | Path | Operation |
| --- | --- | --- |
| GET | `/healthz` | Process liveness and version |
| GET | `/api/v1/config` | Approved Code UUIDs and admission limits |
| GET | `/api/v1/runs?limit=25&offset=0` | Paginated QCL-NEGF workflows |
| POST | `/api/v1/runs` | Submit `{plan, code_uuid, resources, label}` |
| GET | `/api/v1/runs/{uuid}` | State, scientific results and child calculations |
| GET | `/api/v1/runs/{uuid}/report` | Process report entries |
| POST | `/api/v1/runs/{uuid}/kill` | Request cancellation |
| GET | `/api/v1/runs/{uuid}/artifacts` | Retrieved file inventory |
| POST | `/api/v1/runs/{uuid}/artifact/authorize?execution_id=…&path=…` | Authorize a native browser download of one inventoried file |
| GET/HEAD | `/api/v1/runs/{uuid}/artifact?execution_id=…&path=…` | Stream an inventoried file, with single-byte-range support |
| POST | `/api/v1/runs/{uuid}/exports` | Prepare `{execution_id, profile}`; default profile `science`, alternative `full-state` |
| GET | `/api/v1/exports/{export_id}` | Preparation state/progress, or v3 archive receipt |
| POST | `/api/v1/exports/{export_id}/authorize` | Set a scoped download cookie and return its credential-free URL |
| GET/HEAD | `/api/v1/exports/{export_id}/download` | Stream the archive, with Range and If-Range support |

The `plan` field is a string containing the original frozen JSON file, not a parsed nested object. This preserves the exact numerical representation and fingerprint across the browser, API and AiiDA repository.

Submission returns HTTP 202 once AiiDA accepts the workflow. Invalid plans and resource requests return 422; an unapproved Code returns 403; broker unavailability returns 503. Submission is not automatically retried: submitting the same plan again creates a new provenance record. A cancellation request is asynchronous; refresh the workflow to see its eventual state.

Export requests return 202 with an `export_id` and state. The same run/execution/profile reuses an unexpired preparation or ready archive. A busy preparation worker returns 409; insufficient storage admission returns 507. Failed preparation is reported explicitly and never publishes a download. No numerical solver is invoked during export. Scientific completion is supplied only from a terminal scientific result status, never from AiiDA's process exit code. Unsupported or missing scientific status remains `running` for export completeness.

Failed preparation can be retried by an explicit request; the portal never retries it automatically. During service shutdown, copying and compression cancel at the next progress callback and temporary output is removed. Operations within a validator or compaction phase finish until their next callback; shutdown is cooperative.

## Development and build

The [qcl-negf integration repository](https://github.com/Afonenko-QCL-NEGF/qcl-negf) provides a flat set of component submodules and one generated `uv.lock` for the Python 3.14 workspace. Component versions are declared in their package metadata. Git submodule entries select component sources; Python dependencies are resolved once in the integration workspace. This repository does not carry a second Python lock or internal Git revision manifest.

Use Node.js 24 for the browser build. From the integration checkout, synchronize its locked environment, build the frontend, then run the backend checks:

```console
uv sync --locked --all-packages --all-groups --all-extras
cd components/qcl-negf-portal
deno run --allow-run=npm scripts/ci.ts
cd ../..
uv run --locked --package qcl-negf-api python -m pytest components/qcl-negf-portal/tests
uv run --locked --package qcl-negf-api ruff check components/qcl-negf-portal/src components/qcl-negf-portal/tests components/qcl-negf-portal/hatch_build.py
uv run --locked --package qcl-negf-api python -m build --no-isolation components/qcl-negf-portal
```

The frontend can also be checked independently from this repository with `deno run --allow-run=npm scripts/ci.ts`. It installs `frontend/package-lock.json` with `npm ci`, runs browser client tests and TypeScript checks, and builds the static bundle. It does not install unpublished Python dependencies.

`npm run build` writes assets to `src/qcl_negf_api/static/`, which the wheel includes. Release wheel construction rejects missing browser assets. Editable development installation permits the API to run before the browser is built. Generated assets and dependency directories are ignored by Git. `nix/frontend.nix` builds browser assets with the verified npm dependency hash committed alongside the recipe. The platform uses this derivation when building the API wheel from the same root `uv.lock`; Python dependencies are not declared again in a separate Nix package definition. After updating `frontend/package-lock.json`, run `deno task lock:nix` in the Nix development shell to regenerate `nix/generated-npm-hash.json`; do not edit its hash manually.

For local browser development run the API on port 8080 and `npm --prefix frontend run dev`; Vite proxies `/api` to the local API. Production serves the built files directly; it does not run the Vite development server.

The component CI checks the frontend on trusted pushes to `main` and manual dispatch, using a self-hosted runner labelled `qcl-negf-ci`. Backend tests and package integration run in the root repository's locked Python workspace. Pull requests from unknown contributors must be reviewed before running their code on the private runner.

## Repositories

- [QCLNEGF.jl](https://github.com/Afonenko-QCL-NEGF/QCLNEGF.jl): numerical quantum transport library.
- [QCLNEGFRunner.jl](https://github.com/Afonenko-QCL-NEGF/QCLNEGFRunner.jl): scientific configuration, frozen plans and solver execution.
- [qcl-negf-aiida](https://github.com/Afonenko-QCL-NEGF/qcl-negf-aiida): execution, provenance and Slurm integration.
- [qcl-negf-results](https://github.com/Afonenko-QCL-NEGF/qcl-negf-results): scientific artifact validation and bounded exports.
- [qcl-negf-platform](https://github.com/Afonenko-QCL-NEGF/qcl-negf-platform): NixOS services and integration releases.

Released under the MIT license. See [LICENSE](LICENSE).
