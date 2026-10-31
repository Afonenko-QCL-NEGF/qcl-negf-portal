"""Bounded HTTP adapter. AiiDA is the sole owner of execution state."""

import asyncio
import hmac
import json
import logging
import re
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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError
from qcl_negf_contracts.research_cards import validate_research_card
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from .cluster_status import ClusterStatus, ClusterStatusError, query_token
from .configurator import Configurator, ConfiguratorError
from .documentation import Documentation
from .exports import ExportBusy, ExportStorage, ExportStore
from .model_help import MalformedModelHelp, model_help_view, selectors
from .saved_results import SavedResultError, SavedResults
from .service import ActorDeadline, ActorOutcomeUnknown, ActorUnavailable, AiidaService
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
        quantum = scope.get('path') == '/api/v1/configurator/quantum-preview'
        if quantum:
            scope['quantum_deadline'] = time.monotonic() + 190
        while True:
            try:
                if quantum:
                    message = await asyncio.wait_for(receive(),max(0,scope['quantum_deadline']-5-time.monotonic()))
                    if message['type']!='http.disconnect' and time.monotonic()>=scope['quantum_deadline']-5:
                        raise asyncio.TimeoutError
                else:
                    message = await receive()
            except asyncio.TimeoutError:
                await JSONResponse({'status':'error','error':{'code':'timeout'}},504,
                                   headers={'Cache-Control':'no-store'})(scope,receive,send)
                return
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
    resources_mode: Literal["manual", "automatic"] = "manual"
    resources: Resources = Field(default_factory=Resources)


class AgentReportSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    report: StrictStr


class CardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_uuid: StrictStr
    card: StrictStr


class CardEdit(CardCreate):
    base_version_uuid: StrictStr


class CardRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_uuid: StrictStr
    card_version_uuid: StrictStr
    run_uuid: StrictStr
    plan_fingerprint: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class CardAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_uuid: StrictStr
    card_version_uuid: StrictStr
    anchor_run_uuid: StrictStr
    report_uuid: StrictStr


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    execution_id: str = Field(min_length=1, max_length=128)
    profile: Literal["science", "full-state"] = "science"
    attempt: int | None = Field(default=None, ge=1)
    calcjob_uuid: UUID | None = Field(default=None, strict=False)


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


def create_app(settings: Settings | None = None, service: Any = None,
               *, configurator: Any = None, saved_results: Any = None,
               cluster_status: Any = None) -> FastAPI:
    """Create the server; inject a service only in tests or an embedding application."""
    settings = settings or Settings.from_environment()
    documentation = Documentation.load(settings)
    configurator = configurator if configurator is not None else Configurator(settings)
    cluster_status = cluster_status if cluster_status is not None else ClusterStatus(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is None:
            app.state.service = AiidaService(settings.profile, settings=settings)
        else:
            app.state.service = service
        app.state.exports = ExportStore(app.state.service, settings)
        app.state.configurator = configurator
        app.state.cluster_status = cluster_status
        app.state.saved_results = (saved_results if saved_results is not None else
                                   SavedResults(app.state.service, settings))
        try:
            yield
        finally:
            try:
                try:
                    await app.state.saved_results.close()
                finally:
                    try:
                        await cluster_status.close()
                    finally:
                        await configurator.close()
            finally:
                deadline = time.monotonic() + settings.actor_close_timeout_seconds
                try:
                    app.state.exports.close(deadline=deadline)
                finally:
                    if service is None:
                        app.state.service.close(deadline=deadline)

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

    @app.get("/api/v1/cluster/status", dependencies=protected)
    async def cluster_view(request: Request):
        pairs = list(request.query_params.multi_items())
        if (
            any(key not in {"cursor", "limit"} for key, _ in pairs)
            or len({key for key, _ in pairs}) != len(pairs)
            or await request.body()
        ):
            return JSONResponse({"detail": {"code": "cluster_cache_query_invalid"}}, 422)
        cursor = request.query_params.get("cursor")
        raw_limit = request.query_params.get("limit", "64")
        if not raw_limit.isascii() or not raw_limit.isdecimal() or len(raw_limit) > 3:
            return JSONResponse({"detail": {"code": "cluster_cache_query_invalid"}}, 422)
        limit = int(raw_limit)
        try:
            query_token(cursor=cursor, limit=limit)
            result = await request.app.state.cluster_status.read(cursor=cursor, limit=limit)
        except ClusterStatusError as error:
            status = (
                422
                if error.code == "cluster_cache_query_invalid"
                else 409
                if error.code == "stale_cursor"
                else 503
            )
            return JSONResponse({"detail": {"code": error.code}}, status)
        return JSONResponse(result)

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

    def actor_error(exc: ActorUnavailable) -> HTTPException:
        receipt = exc.receipt
        headers = {
            "X-QCL-Operation-ID": receipt.operation_id,
            "X-QCL-Operation-Outcome": receipt.outcome,
            "X-QCL-Actor-State": receipt.actor_state,
            "X-QCL-Auto-Retry": "false",
            "Cache-Control": "no-store",
        }
        if receipt.unresolved_operation_id is not None:
            headers["X-QCL-Unresolved-Operation-ID"] = receipt.unresolved_operation_id
        if isinstance(exc, ActorOutcomeUnknown):
            status = exc.backend_status or 503
            detail = ("Mutation outcome is unknown. Do not submit or repeat the mutation; "
                      "reconcile existing provenance. Operation: " + receipt.operation_id + ".")
            if status == 422:
                detail = "Backend validation was refused. " + detail
        elif exc.code == "mutation_unresolved":
            status = 503
            detail = ("An earlier mutation is unresolved. Do not repeat it; reconcile provenance. "
                      "Operation: " + receipt.unresolved_operation_id + ".")
        elif isinstance(exc, ActorDeadline):
            status = 504
            if receipt.outcome == "not_started":
                detail = ("AiiDA operation did not start before its response deadline. "
                          "Do not retry automatically. Operation: " + receipt.operation_id + ".")
            else:
                detail = ("AiiDA response deadline expired; the backend operation may still be "
                          "running. Operation: " + receipt.operation_id + ".")
        else:
            status = 503
            detail = "AiiDA service cannot accept this operation (" + exc.code + ")."
        return HTTPException(status, detail, headers=headers)

    def export_call(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(request.app.state.exports, method)(*args, **kwargs)
        except ActorUnavailable as exc:
            raise actor_error(exc) from exc
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
            detail = ("Exact export selection service is unavailable"
                      if str(exc) == "Exact export selection service is unavailable"
                      else "Archive service is unavailable")
            raise HTTPException(503, detail) from exc
    resource_defaults = {
        **Resources().model_dump(),
        "max_memory_kb": settings.default_memory_kb,
        "max_wallclock_seconds": min(3600, settings.max_wallclock_seconds),
    }

    def invoke(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(request.app.state.service, method)(*args, **kwargs)
        except ActorUnavailable as exc:
            raise actor_error(exc) from exc
        except LookupError as exc:
            raise HTTPException(404, "Workflow or artifact was not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            LOG.exception("AiiDA service unavailable")
            raise HTTPException(503, "AiiDA service is unavailable") from exc

    def invoke_agent_report(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return invoke(request, method, *args, **kwargs)
        except HTTPException as exc:
            if exc.status_code == 422 and not (exc.headers or {}).get("X-QCL-Operation-ID"):
                # Backend validation can include a decoded lone-surrogate key.
                # Keep report refusals safe without changing existing API errors.
                raise HTTPException(422, "Agent report validation was refused") from exc
            raise

    def card_uuid(value: str) -> str:
        try:
            if str(UUID(value)) != value:
                raise ValueError()
        except (TypeError, ValueError, AttributeError) as exc:
            raise HTTPException(422, "Canonical UUID required") from exc
        return value

    def card_invoke(request: Request, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            result = invoke(request, method, *args, **kwargs)
        except HTTPException as exc:
            if exc.status_code == 422 and not (exc.headers or {}).get("X-QCL-Operation-ID"):
                raise HTTPException(422, "Research-card validation was refused") from exc
            raise
        if not isinstance(result, bytes):
            if (
                len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                > 1048576
            ):
                raise HTTPException(422, "Research-card response exceeds 1 MiB")
        return result

    async def card_body(request: Request, model):
        def unique(pairs):
            data = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError("Duplicate field")
                data[key] = value
            return data

        try:
            payload = json.loads(
                (await request.body()).decode("utf-8", "strict"), object_pairs_hook=unique
            )
            value = model.model_validate(payload)
            for key, item in value.model_dump().items():
                if key.endswith("_uuid"):
                    card_uuid(item)
            if hasattr(value, "plan_fingerprint"):
                fingerprint = value.plan_fingerprint
                if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
                    raise ValueError("Invalid fingerprint")
            if hasattr(value, "card"):
                validate_research_card(value.card.encode("utf-8", "strict"))
            return value
        except (ValueError, UnicodeError, RecursionError, HTTPException) as exc:
            raise HTTPException(422, "Research-card envelope or content was refused") from exc

    def card_page(request: Request, limit: int, offset: int):
        for key, number in (("limit", limit), ("offset", offset)):
            text = request.query_params.get(key, str(number))
            if (
                len(request.query_params.getlist(key)) > 1
                or not text.isascii()
                or not text.isdigit()
            ):
                raise HTTPException(422, "Research-card pagination requires integers")

    def card_mutation(result):
        if result.get("status") == "rejected":
            reason = result.get("reason")
            codes = {
                "not_found": 404,
                "version_conflict": 409,
                "request_conflict": 409,
                "invalid_binding": 422,
                "unsupported_plan": 422,
            }
            if reason not in codes:
                raise HTTPException(503, "Research-card receipt is unavailable")
            # Fixed returned DTO: known refusal, no actor unknown-effect claim.
            return JSONResponse(result, status_code=codes[reason])
        if result.get("status") not in ("stored", "replayed"):
            raise HTTPException(503, "Research-card receipt is unavailable")
        return JSONResponse(result, status_code=201 if result["status"] == "stored" else 200)

    @app.post("/api/v1/research-cards", dependencies=protected)
    async def create_card(request: Request):
        body = await card_body(request, CardCreate)
        return card_mutation(
            await run_in_threadpool(
                card_invoke,
                request,
                "create_research_card",
                body.request_uuid,
                body.card.encode("utf-8"),
            )
        )

    @app.get("/api/v1/research-cards", dependencies=protected)
    def cards(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        card_page(request, limit, offset)
        return {"cards": card_invoke(request, "list_research_cards", limit=limit, offset=offset)}

    @app.get("/api/v1/research-cards/{identifier}", dependencies=protected)
    def card(request: Request, identifier: str):
        return card_invoke(request, "get_research_card", card_uuid(identifier))

    @app.post("/api/v1/research-cards/{identifier}/versions", dependencies=protected)
    async def edit_card(request: Request, identifier: str):
        card_uuid(identifier)
        body = await card_body(request, CardEdit)
        return card_mutation(
            await run_in_threadpool(
                card_invoke,
                request,
                "edit_research_card",
                identifier,
                body.request_uuid,
                body.base_version_uuid,
                body.card.encode("utf-8"),
            )
        )

    @app.get("/api/v1/research-cards/{identifier}/versions", dependencies=protected)
    def card_versions(
        request: Request,
        identifier: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        card_page(request, limit, offset)
        return {
            "versions": card_invoke(
                request,
                "list_research_card_versions",
                card_uuid(identifier),
                limit=limit,
                offset=offset,
            )
        }

    @app.get("/api/v1/research-cards/{identifier}/versions/{version_uuid}", dependencies=protected)
    def card_version(request: Request, identifier: str, version_uuid: str):
        return card_invoke(
            request, "read_research_card_version", card_uuid(identifier), card_uuid(version_uuid)
        )

    @app.get(
        "/api/v1/research-cards/{identifier}/versions/{version_uuid}/file", dependencies=protected
    )
    def card_file(request: Request, identifier: str, version_uuid: str):
        raw = card_invoke(
            request,
            "read_research_card_version",
            card_uuid(identifier),
            card_uuid(version_uuid),
            raw=True,
        )
        if not isinstance(raw, bytes) or len(raw) > 65536:
            raise HTTPException(422, "Research-card file exceeds its byte limit")
        return Response(
            raw,
            media_type="application/json",
            headers={
                "Content-Disposition": 'attachment; filename="research-card.json"',
                "Content-Length": str(len(raw)),
                "Cache-Control": "no-store",
            },
        )

    @app.post("/api/v1/research-cards/{identifier}/runs", dependencies=protected)
    async def attach_card_run(request: Request, identifier: str):
        card_uuid(identifier)
        body = await card_body(request, CardRun)
        return card_mutation(
            await run_in_threadpool(
                card_invoke,
                request,
                "attach_research_card_run",
                identifier,
                body.request_uuid,
                body.card_version_uuid,
                body.run_uuid,
                body.plan_fingerprint,
            )
        )

    @app.get("/api/v1/research-cards/{identifier}/runs", dependencies=protected)
    def card_runs(
        request: Request,
        identifier: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        card_page(request, limit, offset)
        return {
            "runs": card_invoke(
                request,
                "list_research_card_runs",
                card_uuid(identifier),
                limit=limit,
                offset=offset,
            )
        }

    @app.post("/api/v1/research-cards/{identifier}/analyses", dependencies=protected)
    async def attach_card_analysis(request: Request, identifier: str):
        card_uuid(identifier)
        body = await card_body(request, CardAnalysis)
        return card_mutation(
            await run_in_threadpool(
                card_invoke,
                request,
                "attach_research_card_report",
                identifier,
                body.request_uuid,
                body.card_version_uuid,
                body.anchor_run_uuid,
                body.report_uuid,
            )
        )

    @app.get("/api/v1/research-cards/{identifier}/analyses", dependencies=protected)
    def card_analyses(
        request: Request,
        identifier: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        card_page(request, limit, offset)
        return {
            "analyses": card_invoke(
                request,
                "list_research_card_reports",
                card_uuid(identifier),
                limit=limit,
                offset=offset,
            )
        }

    @app.get("/api/v1/research-card-requests/{request_uuid}", dependencies=protected)
    def card_request(request: Request, request_uuid: str):
        return card_invoke(request, "get_research_card_request", card_uuid(request_uuid))

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/openapi.json", dependencies=protected, include_in_schema=False)
    def schema() -> dict:
        return app.openapi()

    @app.get("/api/v1/config", dependencies=protected)
    def configuration() -> dict:
        return {
            "documentation": documentation.availability.model_dump(),
            "configurator": configurator.availability(),
            "quantum_preview": configurator.quantum_availability() if hasattr(configurator,'quantum_availability') else {'available':False,'reason':'capability_not_provided'},
            "codes": settings.allowed_codes,
            "profile": settings.profile,
            "defaults": resource_defaults,
            "ordinary_resources": {
                "request_selection": "configured_request_ceiling",
                "allocation_authority": "slurm",
                "availability": "not_measured",
                "forecast": "advisory",
                "runtime_overhead": "unknown",
                "reserve": "from_frozen_runner_policy",
                "confidence": "uncalibrated",
                "allocated_cpus": "not_measured",
                "allocated_memory": "not_measured",
            },
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

    async def configurator_call(request: Request, *, preview: bool) -> JSONResponse:
        try:
            if preview:
                result = await configurator.preview(await request.body(), request.is_disconnected)
            else:
                result = await configurator.catalog(request.is_disconnected)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ConfiguratorError as exc:
            status = (409 if exc.code == "busy" else 503 if exc.code in
                      {"unavailable", "cleanup_unconfirmed", "runner_failed"} else
                      504 if exc.code == "timeout" else 422)
            return JSONResponse({"status": "error", "error": {
                "code": exc.code, "path": "", "message": "Configurator request refused"}},
                status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/configurator/catalog", dependencies=protected)
    async def configurator_catalog(request: Request):
        return await configurator_call(request, preview=False)

    @app.post("/api/v1/configurator/preview", dependencies=protected)
    async def configurator_preview(request: Request):
        return await configurator_call(request, preview=True)

    @app.post('/api/v1/configurator/quantum-preview',dependencies=protected)
    async def quantum_preview(request: Request):
        deadline=request.scope['quantum_deadline']
        try:
            # Five seconds remain for owned shielded cgroup kill/reap/EOF cleanup.
            async with asyncio.timeout_at(deadline-5):
                raw=await request.body()
                result=await configurator.quantum(raw,request.is_disconnected,deadline=deadline-5)
            return Response(result,media_type='application/json',headers={'Cache-Control':'no-store'})
        except (ConfiguratorError,asyncio.TimeoutError) as error:
            code=error.code if isinstance(error,ConfiguratorError) else 'timeout'
            status=409 if code=='busy' else 504 if code=='timeout' else 503 if code in {'unavailable','cleanup_unconfirmed','runner_failed'} else 422
            payload=getattr(error,'response',None) or {'status':'error','error':{'code':code,'message':'Quantum preview refused'}}
            return JSONResponse(payload,
                                status_code=status,headers={'Cache-Control':'no-store'})

    @app.get("/api/v1/recovery-executions", dependencies=protected)
    def recovery_executions(
        request: Request,
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return {"recovery_executions": invoke(
            request, "list_recovery_executions", limit=limit, offset=offset,
        )}

    @app.get("/api/v1/recovery-executions/{recovery_uuid}", dependencies=protected)
    def recovery_execution(request: Request, recovery_uuid: UUID) -> dict:
        return invoke(request, "get_recovery_execution", str(recovery_uuid))

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
        if submission.resources_mode == "automatic":
            if {"num_cores_per_mpiproc", "max_memory_kb"} & submission.resources.model_fields_set:
                raise HTTPException(422, "Automatic mode forbids client CPU/RAM overrides")
            resources.update(
                num_machines=1,
                num_mpiprocs_per_machine=1,
                num_cores_per_mpiproc=settings.max_cores_per_process,
                max_memory_kb=settings.max_memory_kb,
            )
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

    @app.get("/api/v1/runs/{run_uuid}/model-help", dependencies=protected)
    def model_help(request: Request, run_uuid: str, fragment_id: str,
                   execution_id: str, point_id: str, attempt: str | None = None,
                   calcjob_uuid: str | None = None):
        allowed = {"fragment_id", "execution_id", "point_id", "attempt", "calcjob_uuid"}
        try:
            if (set(request.query_params) - allowed or any(
                    len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
                raise ValueError("Unexpected model help selectors")
            selectors(run_uuid, fragment_id, execution_id, point_id, attempt, calcjob_uuid)
        except ValueError as exc:
            raise HTTPException(422, "Invalid model help selectors") from exc
        try:
            dto = request.app.state.service.get_model_help_input(
                run_uuid, execution_id, point_id,
                attempt=int(attempt) if attempt is not None else None,
                calcjob_uuid=calcjob_uuid)
        except ActorUnavailable as exc:
            raise actor_error(exc) from exc
        except LookupError as exc:
            raise HTTPException(404, "Model help context was not found") from exc
        except ValueError as exc:
            raise HTTPException(422, "Model help validation was refused") from exc
        except Exception as exc:
            raise HTTPException(503, "Model help service is unavailable") from exc
        if dto == {"error": "selection_conflict"}:
            raise HTTPException(409, "Model help selection conflicts")
        try:
            view = model_help_view(dto)
            context = view["context"]
            if (context["run_uuid"] != run_uuid or context["execution_id"] != execution_id
                    or context["point_id"] != point_id
                    or (attempt is not None and context["attempt"] != int(attempt))
                    or (calcjob_uuid is not None and context["calcjob_uuid"] != calcjob_uuid)):
                raise ValueError("Model help selected context mismatch")
            return view
        except MalformedModelHelp as exc:
            raise HTTPException(503, "Model help service is unavailable") from exc
        except (ValueError, SchemaValidationError) as exc:
            raise HTTPException(422, "Model help validation was refused") from exc
        except Exception as exc:
            raise HTTPException(503, "Model help service is unavailable") from exc

    @app.get("/api/v1/runs/{run_uuid}/report", dependencies=protected)
    def report(request: Request, run_uuid: UUID) -> dict:
        return {"entries": invoke(request, "get_run_report", str(run_uuid))}

    @app.post(
        "/api/v1/runs/{run_uuid}/agent-reports", dependencies=protected, status_code=201,
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": AgentReportSubmission.model_json_schema()},
        }}},
    )
    async def save_agent_report(request: Request, run_uuid: UUID) -> dict:
        # Validate the UTF-8 string before Pydantic can echo invalid Unicode in
        # an error response. Only this endpoint uses this safe constant refusal.
        try:
            payload = await request.json()
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise HTTPException(422, "Agent report requires a valid JSON envelope") from exc
        try:
            if isinstance(payload, dict) and isinstance(payload.get("report"), str):
                payload["report"].encode("utf-8", "strict")
            submission = AgentReportSubmission.model_validate(payload)
            raw = submission.report.encode("utf-8", "strict")
        except (ValidationError, UnicodeError) as exc:
            raise HTTPException(422, "Agent report requires only a UTF-8 report string") from exc
        if len(raw) > 262144:
            raise HTTPException(422, "Agent report exceeds the 262144-byte limit")
        return await run_in_threadpool(
            invoke_agent_report, request, "save_agent_report", str(run_uuid), raw
        )

    @app.get("/api/v1/runs/{run_uuid}/agent-reports", dependencies=protected)
    def agent_reports(
        request: Request, run_uuid: UUID,
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    ) -> dict:
        return {"reports": invoke_agent_report(
            request, "list_agent_reports", str(run_uuid), limit=limit, offset=offset
        )}

    @app.get("/api/v1/runs/{run_uuid}/agent-reports/{report_uuid}", dependencies=protected)
    def agent_report_file(request: Request, run_uuid: UUID, report_uuid: UUID) -> Response:
        raw = invoke_agent_report(request, "read_agent_report", str(run_uuid), str(report_uuid))
        if not isinstance(raw, bytes) or len(raw) > 262144:
            raise HTTPException(422, "Agent report payload is unavailable within its byte limit")
        return Response(raw, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="agent-report.json"',
            "Content-Length": str(len(raw)), "Cache-Control": "no-store",
        })

    @app.post("/api/v1/runs/{run_uuid}/kill", dependencies=protected)
    def kill(request: Request, run_uuid: UUID) -> dict:
        return invoke(request, "kill_run", str(run_uuid))

    async def saved_call(request: Request, run_uuid: UUID, operation: str, selection: dict,
                         query: dict) -> JSONResponse:
        try:
            result = await request.app.state.saved_results.read(operation,
                {"run_uuid": str(run_uuid), **selection}, query, request.is_disconnected)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except SavedResultError as exc:
            status = (409 if exc.code in {"busy", "identity_mismatch", "source_changed"} else
                      503 if exc.code in {"unavailable_storage", "stopping"} else 422)
            return JSONResponse({"detail": {"code": exc.code,
                "reason": "Saved result is unavailable for this exact selection"}},
                status_code=status, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/runs/{run_uuid}/saved-results/point-source", dependencies=protected)
    async def saved_point_source(
        request: Request,
        run_uuid: UUID,
        execution_id: str = Query(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
        attempt: int = Query(..., ge=1, le=9007199254740991),
        calcjob_uuid: UUID = Query(...),
        point_id: str = Query(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
    ):
        allowed = {"execution_id", "attempt", "calcjob_uuid", "point_id"}
        if (
            set(request.query_params) != allowed
            or len(request.query_params.multi_items()) != len(allowed)
            or await request.body()
        ):
            raise HTTPException(422, "Only exact point metadata selectors are supported")
        return await saved_call(
            request,
            run_uuid,
            "point-source",
            dict(execution_id=execution_id, attempt=attempt, calcjob_uuid=str(calcjob_uuid)),
            dict(point_id=point_id),
        )

    @app.get("/api/v1/runs/{run_uuid}/saved-results/sources", dependencies=protected)
    @app.get("/api/v1/runs/{run_uuid}/saved-results/brief", dependencies=protected)
    async def saved_page(request: Request, run_uuid: str):
        from .saved_nfs import _catalog_query

        try:
            params = request.query_params
            operation = request.url.path.rsplit("/", 1)[1]
            required = {"execution_id", "attempt", "calcjob_uuid"}
            allowed = required | ({"limit", "cursor"} if operation == "sources" else {"page_ref"})
            if (
                len(request.scope.get("query_string", b"")) > 8192
                or not required <= set(params)
                or set(params) - allowed
                or len(params.multi_items()) != len(set(params))
                or request.headers.get("content-length", "0") != "0"
                or request.headers.get("transfer-encoding")
                or await request.body()
                or str(UUID(run_uuid)) != run_uuid
                or str(UUID(params["calcjob_uuid"])) != params["calcjob_uuid"]
                or not re.fullmatch(r"[1-9][0-9]{0,15}", params["attempt"])
            ):
                raise SavedResultError("selection_required")
            selection = dict(
                run_uuid=run_uuid,
                execution_id=params["execution_id"],
                attempt=int(params["attempt"]),
                calcjob_uuid=params["calcjob_uuid"],
            )
            query = {k: params[k] for k in set(params) - required}
            if "limit" in query:
                if not re.fullmatch(r"[1-8]", query["limit"]):
                    raise SavedResultError("selection_required")
                query["limit"] = int(query["limit"])
            _catalog_query(operation, selection, query)
        except SavedResultError, ValueError, KeyError, TypeError:
            return JSONResponse(
                {
                    "detail": {
                        "code": "selection_required",
                        "reason": "Exact finite page selectors required",
                    }
                },
                status_code=422,
                headers={"Cache-Control": "no-store"},
            )
        return await saved_call(
            request,
            run_uuid,
            operation,
            {k: v for k, v in selection.items() if k != "run_uuid"},
            query,
        )

    @app.get("/api/v1/runs/{run_uuid}/saved-results/manifest", dependencies=protected)
    @app.get("/api/v1/runs/{run_uuid}/saved-results/diagnostics", dependencies=protected)
    async def saved_metadata(
        request: Request,
        run_uuid: UUID,
        execution_id: str | None = None,
        attempt: int | None = Query(None, ge=1),
        calcjob_uuid: UUID | None = None,
        point_id: str | None = None,
        provider: str | None = None,
        native_binding_uuid: str | None = None,
        state_manifest_sha256: str | None = None,
    ):
        allowed = {
            "execution_id",
            "attempt",
            "calcjob_uuid",
            "point_id",
            "provider",
            "native_binding_uuid",
            "state_manifest_sha256",
        }
        if set(request.query_params) - allowed or len(request.query_params.multi_items()) != len(
            set(request.query_params)
        ):
            raise HTTPException(422, "Only exact retained native selectors are supported")
        query = {
            k: v
            for k, v in dict(
                point_id=point_id,
                provider=provider,
                native_binding_uuid=native_binding_uuid,
                state_manifest_sha256=state_manifest_sha256,
            ).items()
            if v is not None
        }
        return await saved_call(
            request,
            run_uuid,
            request.url.path.rsplit("/", 1)[1],
            dict(
                execution_id=execution_id,
                attempt=attempt,
                calcjob_uuid=str(calcjob_uuid) if calcjob_uuid else None,
            ),
            query,
        )

    @app.post("/api/v1/runs/{run_uuid}/saved-results/derive-history", dependencies=protected)
    @app.post("/api/v1/runs/{run_uuid}/saved-results/compare-history", dependencies=protected)
    async def saved_history_action(request: Request, run_uuid: str):
        from .saved_nfs import history_query, uuid
        from .saved_results import _json

        try:
            uuid(run_uuid)
            if request.query_params:
                raise SavedResultError("selection_required")
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 65536:
                    raise SavedResultError("budget_exceeded")
                raw.extend(chunk)
            payload = _json(raw)
            operation = request.url.path.rsplit("/", 1)[1]
            if type(payload) is not dict:
                raise SavedResultError("selection_required")
            if operation == "derive-history":
                selection = {k: payload[k] for k in ("execution_id", "attempt", "calcjob_uuid")}
                query = {k: v for k, v in payload.items() if k not in selection}
            else:
                selection = {
                    k: payload["left"][k] for k in ("execution_id", "attempt", "calcjob_uuid")
                }
                query = payload
            history_query(operation, {"run_uuid": run_uuid, **selection}, query)
        except SavedResultError, ValueError, TypeError, KeyError, UnicodeError:
            return JSONResponse(
                {
                    "detail": {
                        "code": "selection_required",
                        "reason": "Exact retained sources and explicit reduction required",
                    }
                },
                status_code=422,
                headers={"Cache-Control": "no-store"},
            )
        return await saved_call(request, run_uuid, operation, selection, query)

    @app.post("/api/v1/runs/{run_uuid}/saved-results/slice", dependencies=protected)
    @app.post("/api/v1/runs/{run_uuid}/saved-results/history", dependencies=protected)
    async def saved_slice(request: Request, run_uuid: UUID):
        try:
            from .saved_results import _json

            payload = _json(await request.body())
            operation = request.url.path.rsplit("/", 1)[1]
            allowed = {
                "execution_id",
                "attempt",
                "calcjob_uuid",
                "point_id",
                "provider",
                "native_binding_uuid",
                "state_manifest_sha256",
                "source_state",
            }
            allowed |= (
                {"view_id", "selection"}
                if operation == "slice"
                else {"stage", "fields", "maximum_records", "cursor"}
            )
            if not isinstance(payload, dict) or set(payload) != allowed or request.query_params:
                raise SavedResultError("selection_required")
            selection = {k: payload.pop(k) for k in ("execution_id", "attempt", "calcjob_uuid")}
        except SavedResultError:
            return JSONResponse(
                {
                    "detail": {
                        "code": "selection_required",
                        "reason": "Use exact native provenance and explicit selection",
                    }
                },
                422,
                headers={"Cache-Control": "no-store"},
            )
        return await saved_call(request, run_uuid, operation, selection, payload)

    @app.post("/api/v1/runs/{run_uuid}/live-results/{operation}", dependencies=protected)
    async def live_results(request: Request, run_uuid: UUID, operation: str):
        if operation not in {"live-source", "live-history", "performance"}:
            raise HTTPException(404, "Unknown live operation")
        try:
            from .saved_results import _json
            payload = _json(await request.body())
            required = {"execution_id", "attempt", "calcjob_uuid"}
            if operation != "performance":
                required |= {"point_id", "source_attempt"}
            if operation == "live-history":
                required |= {"index_sha256", "generation", "phase", "selection"}
            if not isinstance(payload, dict) or set(payload) != required or request.query_params:
                raise SavedResultError("selection_required")
            selection = {k: payload.pop(k) for k in ("execution_id", "attempt", "calcjob_uuid")}
        except SavedResultError:
            return JSONResponse({"detail": {"code": "selection_required",
                "reason": "Exact execution, point and native prefix selectors required"}}, 422,
                headers={"Cache-Control": "no-store"})
        return await saved_call(request, run_uuid, operation, selection, payload)

    @app.get("/api/v1/runs/{run_uuid}/artifacts", dependencies=protected)
    def artifacts(request: Request, run_uuid: UUID) -> dict:
        return {"artifacts": invoke(request, "list_artifacts", str(run_uuid))}

    def artifact_entry(request: Request, run_uuid: UUID, execution_id: str, path: str,
                       *, attempt=None, calcjob_uuid=None) -> dict:
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                or str(relative) != path):
            raise HTTPException(422, "Artifact path must be normalized and relative")
        entry = invoke(
            request, "get_artifact_metadata", str(run_uuid), execution_id, path,
            attempt=attempt,
            calcjob_uuid=str(calcjob_uuid) if calcjob_uuid is not None else None,
        )
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

    def artifact_target(run_uuid: UUID, execution_id: str, path: str,
                        *, attempt=None, calcjob_uuid=None) -> str:
        values = {"execution_id": execution_id, "path": path}
        if attempt is not None:
            values["attempt"] = attempt
        if calcjob_uuid is not None:
            values["calcjob_uuid"] = str(calcjob_uuid)
        return f"/api/v1/runs/{run_uuid}/artifact?{urlencode(values)}"

    @app.post("/api/v1/runs/{run_uuid}/artifact/authorize", dependencies=protected)
    def authorize_artifact(
        request: Request, run_uuid: UUID,
        execution_id: str = Query(min_length=1, max_length=128),
        path: str = Query(min_length=1, max_length=1024),
        attempt: int | None = Query(default=None, ge=1),
        calcjob_uuid: UUID | None = Query(default=None),
    ) -> JSONResponse:
        entry = artifact_entry(request, run_uuid, execution_id, path,
                               attempt=attempt, calcjob_uuid=calcjob_uuid)
        target = artifact_target(run_uuid, execution_id, path,
                                 attempt=entry["attempt"], calcjob_uuid=entry["calcjob_uuid"])
        return grant_download(request, target)

    @app.get("/api/v1/runs/{run_uuid}/artifact")
    @app.head("/api/v1/runs/{run_uuid}/artifact", include_in_schema=False)
    def artifact(
        request: Request, run_uuid: UUID,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        execution_id: str = Query(min_length=1, max_length=128),
        path: str = Query(min_length=1, max_length=1024),
        attempt: int | None = Query(default=None, ge=1),
        calcjob_uuid: UUID | None = Query(default=None),
    ) -> Response:
        target = artifact_target(run_uuid, execution_id, path,
                                 attempt=attempt, calcjob_uuid=calcjob_uuid)
        download_auth(request, target, credentials)
        entry = artifact_entry(request, run_uuid, execution_id, path,
                               attempt=attempt, calcjob_uuid=calcjob_uuid)
        return stream_file(
            request, lambda: request.app.state.service.open_artifact(
                str(run_uuid), execution_id, path,
                attempt=entry["attempt"], calcjob_uuid=entry["calcjob_uuid"],
            ), entry["size"], PurePosixPath(path).name,
        )

    @app.post("/api/v1/runs/{run_uuid}/exports", dependencies=protected, status_code=202)
    def prepare_export(request: Request, run_uuid: UUID, body: ExportRequest) -> dict:
        return export_call(
            request, "create", str(run_uuid), body.execution_id, body.profile,
            attempt=body.attempt,
            calcjob_uuid=str(body.calcjob_uuid) if body.calcjob_uuid is not None else None,
        )

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

    @app.api_route("/docs", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/docs/{tail:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def source_documentation(request: Request) -> Response:
        return documentation.response(request, request.path_params.get("tail", ""))

    static = Path(__file__).parent / "static"
    if static.is_dir():
        if (static / "index.html").is_file():
            @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
            @app.api_route("/index.html", methods=["GET", "HEAD"], include_in_schema=False)
            def browser_entry() -> FileResponse:
                # StaticFiles stat validators may collide across packaged releases.
                return FileResponse(
                    static / "index.html", media_type="text/html",
                    headers={"Cache-Control": "no-cache, must-revalidate"},
                )

        app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
