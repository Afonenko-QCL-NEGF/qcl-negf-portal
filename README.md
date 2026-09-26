# QCL-NEGF Portal

A web interface for quantum transport calculations run by [AiiDA](https://www.aiida.net/) and Slurm. The portal submits frozen scientific plans to the QCL-NEGF AiiDA plugin and reads execution state and provenance from AiiDA.

## Supported workflow

1. Resolve a scientific configuration with `qcl-negf plan CONFIG > plan.json` using the solver environment that will execute it.
2. Open the portal, enter its access token, upload the plan and select an approved solver Code.
3. Request resources within the configured limits. Each execution receives its own Slurm allocation through AiiDA.
4. Follow workflow state, inspect per-execution scientific results and process reports, download retrieved artifacts, or request cancellation.

The AiiDA plugin accepts ordinary frozen plans. Campaign plans and evidence-gated campaigns are not exposed by this interface. Solver configuration editing and numerical analysis are performed with the solver and `qcl-negf-results` tools. A finished process can still have an unsuccessful scientific outcome: inspect the result status and diagnostics.

The repository contains two modules with one release cycle: `frontend/` builds a static React/TypeScript application, and `src/qcl_negf_api/` provides the authenticated FastAPI gateway. The compiled browser bundle is included in the Python wheel and served from the same origin. There is one service in production; Node.js is a build dependency. The gateway owns authentication and request admission, while the AiiDA plugin owns execution and provenance. A dedicated thread owns AiiDA's profile, event loop and repository handles; HTTP worker threads do not share mutable AiiDA state. The portal has no scheduler, SSH client, execution queue or mutable result cache.

## Install

Python 3.14 and an initialized AiiDA profile are required. Install a coordinated wheel set containing `qcl-negf-contracts`, `aiida-qcl-negf` and `qcl-negf-api` from the same platform release:

```console
uv venv --python 3.14
uv pip install --python .venv/bin/python --find-links /path/to/release/wheels qcl-negf-api==0.2.0
```

The portal wheel must contain the compiled browser assets. The build instructions below generate them. Package source metadata has no dependency on a sibling checkout. For NixOS deployment, use [qcl-negf-platform](https://github.com/AfonenkoA/qcl-negf-platform), which provides the service identity, runtime credential configuration and AiiDA profile setup. Configure a TLS reverse proxy or an SSH tunnel for access to the loopback listener.

Create a token file readable only by the service account. Supply its path as `QCL_NEGF_API_TOKEN_FILE`; the token must contain at least 32 ASCII characters and should be generated randomly. Register the solver Code using the AiiDA plugin's setup instructions, then configure its UUID:

```console
export QCL_NEGF_API_TOKEN_FILE=/run/credentials/qcl-negf-api/token
export QCL_NEGF_ALLOWED_CODES=YOUR_REGISTERED_CODE_UUID
export QCL_NEGF_AIIDA_PROFILE=qcl-negf
qcl-negf-api --host 127.0.0.1 --port 8080
```

Run the portal as the same operating-system user that owns the AiiDA profile. `AIIDA_PATH` locates that profile if it is outside the default directory. The AiiDA daemon must be running to execute submitted workflows. The service fails at startup when authentication or approved-code configuration is absent.

An administrator can set `QCL_NEGF_SCRATCH_ROOT` to an absolute normalized path such as `/scratch/qcl-negf`. It designates worker-local scratch storage for QCLNEGFRunner and must be writable on every eligible compute node. The path is site configuration, is recorded by the AiiDA execution, and cannot be supplied by an HTTP client. The API host does not need this worker-local directory.

Place a TLS reverse proxy in front of the loopback listener. API requests require the bearer token, including read operations and the OpenAPI document. The browser keeps the token in memory only. This is a shared trusted-research-group interface: a token grants access to all QCL-NEGF workflows in the configured AiiDA profile, including submission and cancellation. Use separate profiles and service instances when separate access domains are needed.

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
| `QCL_NEGF_MAX_DOWNLOAD_BYTES` | 200000000 | Maximum single retrieved-artifact download |

These are admission limits; Slurm performs resource allocation. Each execution uses one machine and one process; these two limits must remain one. Parallel work is distributed as independent executions across Slurm nodes. Configure core, memory and time ceilings for the cluster partition. The initial memory request is 4 GiB, is configurable on the control host and can be adjusted in the submission form. Match these defaults and ceilings to the actual compute-node memory.

Only artifacts registered in the workflow's AiiDA retrieved repositories can be downloaded. Arbitrary host paths are not accepted. Large physical arrays remain available through the scientific result artifacts and `qcl-negf-results`; the browser displays structured result summaries and reports.

For complete datasets exceeding the browser limit, expand **Export complete repositories** in the workflow view. It gives the retrieved repository UUID for each execution. Run the displayed command on the control host as the AiiDA profile owner:

```console
verdi -p qcl-negf node repo dump RETRIEVED_UUID ./retrieved-run
qcl-negf-results export ./retrieved-run/result ./exports --profile science
```

The output directory for `verdi node repo dump` must not exist. Choose a different directory for each execution. `qcl-negf-results export` creates checksummed archive parts of at most 200,000,000 bytes without changing the native scientific values. Its default status is `running`; supply `--status completed` only when that status is established by the scientific result. The `full-state` profile also retains full physics and recovery state. Copy the completed part set using your normal file-transfer tool and verify it with `qcl-negf-receive` on the receiving machine.

## HTTP API

All `/api/v1/` routes require `Authorization: Bearer TOKEN`. The authenticated OpenAPI schema is at `/api/v1/openapi.json`.

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
| GET | `/api/v1/runs/{uuid}/artifact?execution_id=…&path=…` | Download an inventoried file |

The `plan` field is a string containing the original frozen JSON file, not a parsed nested object. This preserves the exact numerical representation and fingerprint across the browser, API and AiiDA repository.

Submission returns HTTP 202 once AiiDA accepts the workflow. Invalid plans and resource requests return 422; an unapproved Code returns 403; broker unavailability returns 503. Submission is not automatically retried: submitting the same plan again creates a new provenance record. A cancellation request is asynchronous; refresh the workflow to see its eventual state.

## Development and build

The [qcl-negf integration repository](https://github.com/AfonenkoA/qcl-negf) provides a flat set of component submodules and one generated `uv.lock` for the Python 3.14 workspace. Component versions are declared in their package metadata. Git submodule entries select component sources; Python dependencies are resolved once in the integration workspace. This repository does not carry a second Python lock or internal Git revision manifest.

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

- [QCLNEGF.jl](https://github.com/AfonenkoA/QCLNEGF.jl): numerical quantum transport library.
- [QCLNEGFRunner.jl](https://github.com/AfonenkoA/QCLNEGFRunner.jl): scientific configuration, frozen plans and solver execution.
- [qcl-negf-aiida](https://github.com/AfonenkoA/qcl-negf-aiida): execution, provenance and Slurm integration.
- [qcl-negf-results](https://github.com/AfonenkoA/qcl-negf-results): scientific artifact validation and bounded exports.
- [qcl-negf-platform](https://github.com/AfonenkoA/qcl-negf-platform): NixOS services and integration releases.

Released under the MIT license. See [LICENSE](LICENSE).
