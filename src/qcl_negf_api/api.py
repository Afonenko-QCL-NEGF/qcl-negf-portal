"""Bounded HTTP adapter. AiiDA is the sole owner of execution state."""

import hmac
import logging
import secrets
import time
from collections.abc import Generator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlencode
from uuid import UUID

import anyio
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .exports import ExportBusy, ExportStorage, ExportStore
from .service import AiidaService
from .settings import Settings

LOG = logging.getLogger(__name__)


class ClosingStreamingResponse(StreamingResponse):
    """Release a synchronous reader even when ASGI cancels the response."""

    def __init__(self, content: Generator[bytes], **kwargs: Any) -> None:
        self._content = content
        super().__init__(content, **kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette's threadpool iterator waits for an in-flight next() on
            # cancellation, but does not close the underlying generator.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._content.close)


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


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    execution_id: str = Field(min_length=1, max_length=128)
    profile: Literal["science", "full-state"] = "science"


def byte_range(request: Request, size: int, etag: str | None = None) -> tuple[int, int, int]:
    """Accept one byte range; never silently stream an incorrect resumed offset."""
    value = request.headers.get("range")
    if not value or (request.headers.get("if-range") and request.headers["if-range"] != etag):
        return 0, size, 200
    try:
        if not value.startswith("bytes=") or "," in value:
            raise ValueError
        first, last = value[6:].split("-")
        if not first:
            suffix = int(last)
            if not last.isdigit() or suffix <= 0:
                raise ValueError
            start, stop = max(0, size - suffix), size
        else:
            if not first.isdigit() or (last and not last.isdigit()):
                raise ValueError
            start, stop = int(first), min(size, int(last) + 1) if last else size
        if not 0 <= start < stop <= size:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(416, "Requested range is unavailable", headers={
            "Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes",
        }) from exc
    return start, stop, 206


def create_app(settings: Settings | None = None, service: Any = None) -> FastAPI:
    """Create the server; inject a service only in tests or an embedding application."""
    settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is None:
            app.state.service = AiidaService(settings.profile)
        else:
            app.state.service = service
        app.state.exports = ExportStore(app.state.service, settings)
        try:
            yield
        finally:
            app.state.exports.close()
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
    capability_key = secrets.token_bytes(32)

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

    def valid_bearer(credentials: HTTPAuthorizationCredentials | None) -> bool:
        return credentials is not None and hmac.compare_digest(
            credentials.credentials.encode(), settings.token.encode()
        )

    def authenticate(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if not valid_bearer(credentials):
            raise HTTPException(
                401, "A valid bearer token is required", headers={"WWW-Authenticate": "Bearer"}
            )

    protected = [Depends(authenticate)]

    def grant_download(request: Request, target: str) -> JSONResponse:
        expires = str(int(time.time()) + 3600)
        signature = hmac.new(capability_key, f"{target}\n{expires}".encode(), "sha256").hexdigest()
        response = JSONResponse({"url": target, "expires_unix": int(expires)})
        response.set_cookie(
            "qcl_download", f"{expires}.{signature}", max_age=3600,
            path=target.split("?", 1)[0], httponly=True, samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    def download_auth(request: Request, target: str, credentials) -> None:
        if valid_bearer(credentials):
            return
        value = request.cookies.get("qcl_download", "")
        try:
            expires, signature = value.split(".")
            expected = hmac.new(
                capability_key, f"{target}\n{expires}".encode(), "sha256"
            ).hexdigest()
            if int(expires) > time.time() and hmac.compare_digest(signature, expected):
                return
        except (ValueError, TypeError):
            pass
        raise HTTPException(401, "Authorize this download again", headers={
            "WWW-Authenticate": "Bearer",
        })

    def export_call(request: Request, method: str, *args: Any) -> Any:
        try:
            return getattr(request.app.state.exports, method)(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ExportBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        except ExportStorage as exc:
            raise HTTPException(507, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            LOG.exception("Archive service unavailable")
            raise HTTPException(503, "Archive service is unavailable") from exc
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
            "exports": {
                "disk_bytes": settings.export_disk_bytes,
                "ttl_seconds": settings.export_ttl_seconds,
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

    def artifact_entry(request: Request, run_uuid: UUID, execution_id: str, path: str) -> dict:
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or str(relative) != path):
            raise HTTPException(422, "Artifact path must be normalized and relative")
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
                "Artifact exceeds the individual file limit. Prepare a complete result archive.",
            )
        return entry

    def stream_file(request: Request, opener, size: int, name: str, etag=None) -> Response:
        start, stop, status = byte_range(request, size, etag)
        filename = "".join(c for c in name if c.isascii() and (c.isalnum() or c in "._-"))
        disposition = f'attachment; filename="{filename or "artifact"}"'
        if name != filename:
            disposition += f"; filename*=UTF-8''{quote(name, safe='')}"
        headers = {
            "Content-Disposition": disposition,
            "Content-Length": str(stop - start),
            "Accept-Ranges": "bytes",
        }
        if etag:
            headers["ETag"] = etag
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{stop - 1}/{size}"
        if request.method == "HEAD":
            return Response(
                status_code=status, headers=headers, media_type="application/octet-stream"
            )

        def chunks():
            with opener() as stream:
                stream.seek(start)
                remaining = stop - start
                while remaining:
                    data = stream.read(min(65536, remaining))
                    if not data or len(data) > remaining:
                        raise RuntimeError("Artifact differs from the declared byte length")
                    remaining -= len(data)
                    yield data
        return ClosingStreamingResponse(
            chunks(), status_code=status, media_type="application/octet-stream", headers=headers,
        )

    def artifact_target(run_uuid: UUID, execution_id: str, path: str) -> str:
        query = urlencode({"execution_id": execution_id, "path": path})
        return f"/api/v1/runs/{run_uuid}/artifact?{query}"

    @app.post("/api/v1/runs/{run_uuid}/artifact/authorize", dependencies=protected)
    def authorize_artifact(
        request: Request, run_uuid: UUID,
        execution_id: str = Query(min_length=1, max_length=128),
        path: str = Query(min_length=1, max_length=1024),
    ) -> JSONResponse:
        artifact_entry(request, run_uuid, execution_id, path)
        return grant_download(request, artifact_target(run_uuid, execution_id, path))

    @app.get("/api/v1/runs/{run_uuid}/artifact")
    @app.head("/api/v1/runs/{run_uuid}/artifact", include_in_schema=False)
    def artifact(
        request: Request, run_uuid: UUID,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        execution_id: str = Query(min_length=1, max_length=128),
        path: str = Query(min_length=1, max_length=1024),
    ) -> Response:
        download_auth(request, artifact_target(run_uuid, execution_id, path), credentials)
        entry = artifact_entry(request, run_uuid, execution_id, path)
        return stream_file(
            request, lambda: request.app.state.service.open_artifact(
                str(run_uuid), execution_id, path
            ), entry["size"], PurePosixPath(path).name,
        )

    @app.post("/api/v1/runs/{run_uuid}/exports", dependencies=protected, status_code=202)
    def prepare_export(request: Request, run_uuid: UUID, body: ExportRequest) -> dict:
        return export_call(request, "create", str(run_uuid), body.execution_id, body.profile)

    @app.get("/api/v1/exports/{export_id}", dependencies=protected)
    def export_status(request: Request, export_id: UUID) -> dict:
        return export_call(request, "get", str(export_id))

    @app.post("/api/v1/exports/{export_id}/authorize", dependencies=protected)
    def authorize_export(request: Request, export_id: UUID) -> JSONResponse:
        entry = export_call(request, "get", str(export_id))
        if entry["state"] != "ready":
            raise HTTPException(409, "The archive is not ready for download")
        return grant_download(request, f"/api/v1/exports/{export_id}/download")

    @app.get("/api/v1/exports/{export_id}/download")
    @app.head("/api/v1/exports/{export_id}/download", include_in_schema=False)
    def export_download(
        request: Request, export_id: UUID,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> Response:
        download_auth(request, f"/api/v1/exports/{export_id}/download", credentials)
        entry = export_call(request, "get", str(export_id))
        if entry["state"] != "ready":
            raise HTTPException(409, "The archive is not ready for download")
        receipt = entry["receipt"]
        return stream_file(
            request, lambda: request.app.state.exports.open(str(export_id)),
            receipt["bytes"], receipt["filename"], f'"{receipt["sha256"]}"',
        )

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
