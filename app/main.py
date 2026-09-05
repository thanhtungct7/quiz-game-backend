import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.dependencies import close_avatar_storage
from app.api.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.session import AsyncSessionFactory, engine
from app.repository.auth.user_repository import UserRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.monster_repository import MonsterRepository
from app.repository.game.season_repository import SeasonRepository
from app.services.content.admin_bootstrap_service import AdminSeedService, seed_first_admin
from app.services.duo.housekeeping import abandon_orphaned_matches, run_housekeeping
from app.services.duo.match_runtime import engine as duo_engine
from app.services.game.catalog import (
    seed_game_catalog,
    seed_item_catalog,
    seed_monster_catalog,
)
from app.services.game.season_service import ensure_active_season
from app.services.pve.battle_runtime import engine as battle_engine
from app.services.pve.housekeeping import abandon_orphaned_battles

configure_logging(settings.debug)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s in %s mode", settings.app_name, settings.environment)
    async with AsyncSessionFactory() as db:
        await seed_first_admin(AdminSeedService(UserRepository(db)))
        # Upsert by code, so this is safe to run on every start. Ultimates
        # bind to the opening units of the learn path and simply do not appear
        # until the question bank has been imported.
        await seed_game_catalog(CatalogRepository(db))
        await seed_item_catalog(ItemRepository(db))
        # Monsters are mapped onto lessons by position, so seeding the
        # catalog is all it takes for every gate on the path to be guarded.
        await seed_monster_catalog(MonsterRepository(db))
        await ensure_active_season(SeasonRepository(db))

    # Duo match state lives in this process, so anything left IN_PROGRESS
    # belongs to a previous run and can never be resumed.
    stranded = await abandon_orphaned_matches()
    if stranded:
        logger.info(
            "Closed duo matches orphaned by a previous run and refunded %d player(s)",
            stranded,
        )
    # Same story for lesson battles, minus the refund: a battle charges
    # nothing, so an orphaned one only needs closing.
    orphaned_battles = await abandon_orphaned_battles()
    if orphaned_battles:
        logger.info(
            "Closed %d lesson battle(s) orphaned by a previous run", orphaned_battles
        )
    housekeeping = asyncio.create_task(run_housekeeping())

    yield

    housekeeping.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await housekeeping
    await duo_engine.shutdown()
    await battle_engine.shutdown()
    await close_avatar_storage()
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
    # no-store by default, but never override a route that opted into caching:
    # the public course-content endpoints serve ETag-revalidated responses.
    response.headers.setdefault("Cache-Control", "no-store")
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
