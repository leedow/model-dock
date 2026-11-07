import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import text
from starlette.exceptions import HTTPException

from modeldock.api import inference, management, model_downloads, providers
from modeldock.config import get_settings
from modeldock.db import Session
from modeldock.entities import CallLog, uid
from modeldock.errors import DockError
from modeldock.infrastructure.runtime import RedisRuntimeClient
from modeldock.infrastructure.storage import LocalFileStorage
from modeldock.schemas import ErrorResponse
from modeldock.security import Principal

logger = logging.getLogger("modeldock.api")


def create_app(settings: Any = None, sessions: Any = None, redis: Any = None) -> FastAPI:
    settings = settings or get_settings()
    owns_redis = redis is None
    redis = (
        redis
        if redis is not None
        else Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        if owns_redis:
            await app.state.redis.aclose()

    app = FastAPI(
        title="ModelDock API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        description="Local multimodal gateway with explicit Mock and optional provider adapters. GPU metrics are simulated.",
        lifespan=lifespan,
        responses={
            code: {"model": ErrorResponse} for code in [400, 401, 403, 404, 409, 413, 422, 429, 503, 504]
        },
    )
    app.state.settings, app.state.sessions, app.state.redis = settings, sessions or Session, redis
    app.state.storage = LocalFileStorage(settings.data_dir)
    app.state.runtime = RedisRuntimeClient(redis, app.state.sessions)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request.state.request_id = supplied if re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", supplied) else uid()
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_failed", extra={"request_id": request.state.request_id})
            response = JSONResponse(
                {
                    "error": {
                        "message": "Internal service error",
                        "type": "server_error",
                        "param": None,
                        "code": "internal_error",
                    }
                },
                status_code=503,
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        logger.info(
            "http_request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - start) * 1000),
            },
        )
        # Rejected inference calls do not have a Task; retain metadata without request content.
        principal: Principal | None = getattr(request.state, "principal", None)
        if (
            response.status_code >= 400
            and principal
            and not getattr(request.state, "task_id", None)
            and request.url.path
            in {
                "/v1/chat/completions",
                "/v1/images/generations",
                "/v1/audio/speech",
                "/v1/audio/transcriptions",
                "/v1/tasks",
            }
        ):
            try:
                async with app.state.sessions() as session:
                    session.add(
                        CallLog(
                            user_id=principal.user_id,
                            username=principal.username,
                            key_id=principal.key_id,
                            model=getattr(request.state, "model_name", "-"),
                            capability=getattr(request.state, "capability", "request"),
                            status="rejected",
                            duration_ms=round((time.monotonic() - start) * 1000),
                            usage={},
                            error_code=getattr(request.state, "error_code", "request_rejected"),
                            request_id=request.state.request_id,
                        )
                    )
                    await session.commit()
            except Exception:
                logger.warning("rejection_log_failed", extra={"request_id": request.state.request_id})
        return response

    @app.exception_handler(DockError)
    async def dock_error(request: Request, exc: DockError):
        request.state.error_code = exc.error["code"]
        headers = {"Retry-After": "60"} if exc.status == 429 else None
        return JSONResponse({"error": exc.error}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def validation_error(request: Request, exc: Any):
        errors = (
            exc.errors(include_input=False, include_url=False)
            if isinstance(exc, ValidationError)
            else exc.errors()
        )
        messages = [".".join(str(x) for x in e["loc"]) + ": " + e["msg"] for e in errors]
        request.state.error_code = "validation_error"
        return JSONResponse(
            {
                "error": {
                    "message": "; ".join(messages),
                    "type": "invalid_request_error",
                    "param": ".".join(str(x) for x in errors[0]["loc"]) if errors else None,
                    "code": "validation_error",
                }
            },
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse(
            {
                "error": {
                    "message": str(exc.detail),
                    "type": "invalid_request_error",
                    "param": None,
                    "code": "http_error",
                }
            },
            status_code=exc.status_code,
        )

    app.include_router(management.router)
    app.include_router(providers.router)
    app.include_router(model_downloads.router)
    app.include_router(inference.router)

    @app.get("/health/live", tags=["Health"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    async def ready():
        try:
            async with app.state.sessions() as session:
                await session.execute(text("SELECT 1"))
            await redis.ping()
            runtime = await app.state.runtime.snapshot()
            if not runtime.get("online"):
                raise RuntimeError("worker offline")
            return {"status": "ready", "database": True, "redis": True, "worker": True}
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    swagger_dir = settings.static_dir / "swagger"
    if swagger_dir.exists():
        app.mount("/swagger", StaticFiles(directory=swagger_dir), name="swagger-assets")

    @app.get("/docs", include_in_schema=False)
    async def docs():
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="ModelDock API",
            swagger_js_url="/swagger/swagger-ui-bundle.js",
            swagger_css_url="/swagger/swagger-ui.css",
            swagger_favicon_url="/favicon.svg",
            swagger_ui_parameters={"validatorUrl": None, "persistAuthorization": False},
        )

    # Explicit dependency in OpenAPI gives API consumers a usable Bearer Authorize control.
    schema_generator = app.openapi

    def openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = schema_generator()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["APIKeyBearer"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "mdk_...",
        }
        for path, methods in schema["paths"].items():
            if path.startswith("/v1/"):
                for method in methods.values():
                    if isinstance(method, dict):
                        method["security"] = [{"APIKeyBearer": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]

    # Static SPA fallback must never swallow an unknown API endpoint.
    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.startswith(("api/", "v1/", "health/")):
            raise DockError("Endpoint not found", "not_found", 404)
        root = settings.static_dir.resolve()
        target = (root / path).resolve()
        if target.is_relative_to(root) and target.is_file():
            return FileResponse(target)
        if (root / "index.html").exists():
            return FileResponse(root / "index.html")
        return JSONResponse(
            {"service": "ModelDock", "message": "Build web/ to serve the management console", "docs": "/docs"}
        )

    return app


app = create_app()


def run() -> None:
    import uvicorn

    from modeldock.logging_config import configure_logging

    configure_logging()
    # Access logging avoids request query strings that may contain application input.
    uvicorn.run("modeldock.main:app", host="0.0.0.0", port=get_settings().port, access_log=False)
