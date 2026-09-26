"""Bounded HTTP adapter. AiiDA is the sole owner of execution state."""

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .service import AiidaService
from .settings import Settings

LOG = logging.getLogger(__name__)


class BodyLimit:
    """Read request bodies once with a strict bound, including chunked transfer."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                await JSONResponse({"detail": "Request body exceeds configured limit"}, 413)(
                    scope, receive, send
                )
                return
            if not message.get("more_body", False):
                break
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    num_machines: int = Field(default=1, ge=1, le=1)
    num_mpiprocs_per_machine: int = Field(default=1, ge=1, le=1)
    num_cores_per_mpiproc: int = Field(default=1, ge=1)
    max_wallclock_seconds: int = Field(default=3600, ge=60)
    max_memory_kb: int = Field(default=4194304, ge=1)


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan: str = Field(min_length=2)
    code_uuid: UUID = Field(strict=False)
    label: str = Field(default="", max_length=120)
    resources: Resources = Field(default_factory=Resources)


def create_app(settings: Settings | None = None, service: Any = None) -> FastAPI:
    """Create the server; inject a service only in tests or an embedding application."""
    settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is None:
            app.state.service = AiidaService(settings.profile)
        else:
            app.state.service = service
        try:
            yield
        finally:
            if service is None:
                app.state.service.close()

    app = FastAPI(
        title="QCL-NEGF API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BodyLimit, limit=settings.max_body_bytes)
    bearer = HTTPBearer(auto_error=False)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.token.encode()
        ):
            raise HTTPException(
                401, "A valid bearer token is required", headers={"WWW-Authenticate": "Bearer"}
            )

    protected = [Depends(authenticate)]
    resource_defaults = {
        **Resources().model_dump(),
        "max_memory_kb": settings.default_memory_kb,
        "max_wallclock_seconds": min(3600, settings.max_wallclock_seconds),
    }

    def invoke(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(request.app.state.service, method)(*args, **kwargs)
        except LookupError as exc:
            raise HTTPException(404, "Workflow or artifact was not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            LOG.exception("AiiDA service unavailable")
            raise HTTPException(503, "AiiDA service is unavailable") from exc

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/openapi.json", dependencies=protected, include_in_schema=False)
    def schema() -> dict:
        return app.openapi()

    @app.get("/api/v1/config", dependencies=protected)
    def configuration() -> dict:
        return {
            "codes": settings.allowed_codes,
            "profile": settings.profile,
            "defaults": resource_defaults,
            "limits": {
                "num_machines": settings.max_machines,
                "num_mpiprocs_per_machine": settings.max_processes_per_machine,
                "num_cores_per_mpiproc": settings.max_cores_per_process,
                "max_wallclock_seconds": settings.max_wallclock_seconds,
                "max_memory_kb": settings.max_memory_kb,
                "plan_bytes": settings.max_body_bytes - 1024,
                "artifact_bytes": settings.max_download_bytes,
            },
        }

    @app.get("/api/v1/runs", dependencies=protected)
    def runs(
        request: Request,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return {"runs": invoke(request, "list_runs", limit=limit, offset=offset)}

    @app.post("/api/v1/runs", dependencies=protected, status_code=202)
    def submit(request: Request, submission: Submission) -> dict:
        code = str(submission.code_uuid)
        if code not in settings.allowed_codes:
            raise HTTPException(403, "Code is not approved for this portal")
        resources = submission.resources.model_dump()
        for key, value in resource_defaults.items():
            if key not in submission.resources.model_fields_set:
                resources[key] = value
        limits = {
            "num_machines": settings.max_machines,
            "num_mpiprocs_per_machine": settings.max_processes_per_machine,
            "num_cores_per_mpiproc": settings.max_cores_per_process,
            "max_wallclock_seconds": settings.max_wallclock_seconds,
            "max_memory_kb": settings.max_memory_kb,
        }
        if any(resources[key] > value for key, value in limits.items()):
            raise HTTPException(422, "Requested resources exceed portal limits")
        return invoke(
            request,
            "submit_plan",
            submission.plan,
            code,
            resources,
            label=submission.label,
            scratch_root=settings.scratch_root,
        )

    @app.get("/api/v1/runs/{run_uuid}", dependencies=protected)
    def run(request: Request, run_uuid: UUID) -> dict:
        return invoke(request, "get_run", str(run_uuid))

    @app.get("/api/v1/runs/{run_uuid}/report", dependencies=protected)
    def report(request: Request, run_uuid: UUID) -> dict:
        return {"entries": invoke(request, "get_run_report", str(run_uuid))}

    @app.post("/api/v1/runs/{run_uuid}/kill", dependencies=protected)
    def kill(request: Request, run_uuid: UUID) -> dict:
        return invoke(request, "kill_run", str(run_uuid))

    @app.get("/api/v1/runs/{run_uuid}/artifacts", dependencies=protected)
    def artifacts(request: Request, run_uuid: UUID) -> dict:
        return {"artifacts": invoke(request, "list_artifacts", str(run_uuid))}

    @app.get("/api/v1/runs/{run_uuid}/artifact", dependencies=protected)
    def artifact(
        request: Request,
        run_uuid: UUID,
        execution_id: str = Query(min_length=1, max_length=128),
        path: str = Query(min_length=1, max_length=1024),
    ) -> StreamingResponse:
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path:
            raise HTTPException(422, "Artifact path must be relative")
        entries = invoke(request, "list_artifacts", str(run_uuid))
        entry = next(
            (
                item
                for item in entries
                if (item["execution_id"] == execution_id and item["path"] == path)
            ),
            None,
        )
        if entry is None:
            raise HTTPException(404, "Artifact was not found")
        if entry["size"] > settings.max_download_bytes:
            raise HTTPException(
                413,
                "Artifact exceeds the browser download limit. Export the retrieved repository "
                "with verdi node repo dump on the control host, then use qcl-negf-results export.",
            )

        def chunks():
            with request.app.state.service.open_artifact(
                str(run_uuid), execution_id, path
            ) as stream:
                remaining = settings.max_download_bytes
                while data := stream.read(min(65536, remaining + 1)):
                    if len(data) > remaining:
                        raise RuntimeError("Artifact exceeded configured download limit")
                    remaining -= len(data)
                    yield data

        # An ASCII fallback avoids header injection and filesystem disclosure.
        filename = "".join(c for c in relative.name if c.isascii() and (c.isalnum() or c in "._-"))
        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename or "artifact"}"',
                "Content-Length": str(entry["size"]),
                "X-Content-Type-Options": "nosniff",
            },
        )

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
