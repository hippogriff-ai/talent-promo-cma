"""FastAPI app factory. Errors use the contract §2 envelope:
{"type":"error","error":{"type","message"},"request_id"}."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tp_gateway.config import Settings
from tp_gateway.db import Database
from tp_gateway.relay import RunManager
from tp_gateway.routers import coach, memory

logger = logging.getLogger(__name__)

_ERROR_TYPES = {
    400: "invalid_request",
    404: "not_found",
    409: "conflict",
    422: "invalid_request",
    500: "internal_error",
}


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = "req_" + uuid.uuid4().hex[:12]
        request.state.request_id = rid
    return rid


def _error_response(request: Request, status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "type": "error",
            "error": {"type": _ERROR_TYPES.get(status, "error"), "message": message},
            "request_id": _request_id(request),
        },
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await app.state.manager.shutdown()
    app.state.db.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="tp-gateway", lifespan=_lifespan)
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.manager = RunManager(settings, app.state.db)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        rid = _request_id(request)
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error_response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        message = f"invalid request: {loc}: {first.get('msg', 'validation failed')}"
        return _error_response(request, 422, message)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", exc_info=exc)
        return _error_response(request, 500, "internal error")

    app.include_router(coach.router)
    app.include_router(memory.router)
    return app


app = create_app()
