import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory, engine
from app.repository.user_repository import UserRepository
from app.services.admin_bootstrap_service import AdminSeedService, seed_first_admin

configure_logging(settings.debug)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s in %s mode", settings.app_name, settings.environment)
    async with AsyncSessionFactory() as db:
        await seed_first_admin(AdminSeedService(UserRepository(db)))
    yield
    await engine.dispose()
    logger.info("Application stopped")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id_header = request.headers.get("X-Request-ID", "")
    try:
        request_id = str(UUID(request_id_header))
    except ValueError:
        request_id = str(uuid4())

    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    logger.exception("Unhandled error request_id=%s", request_id, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}


app.include_router(api_router, prefix=settings.api_v1_prefix)
