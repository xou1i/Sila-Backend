"""Sila (صِلة) API: modular monolith entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.jobs.scheduler import build_scheduler, refresh_prices_job
from app.modules.admin.router import router as admin_router
from app.modules.ai.router import router as ai_router
from app.modules.alerts.router import router as alerts_router
from app.modules.identity.router import router as identity_router
from app.modules.interest.router import router as interest_router
from app.modules.listings.router import router as listings_router
from app.modules.market.router import router as market_router
from app.modules.notifications.router import router as notifications_router
from app.modules.orders.router import router as orders_router
from app.modules.ownership.router import router as ownership_router
from app.modules.subscription.router import router as subscription_router
from app.modules.system.router import router as system_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

TAGS = [
    {
        "name": "Identity & Security",
        "description": "Signup, login, JWT refresh, profile, mock KYC (توقيعك)",
    },
    {"name": "Market Data", "description": "Cached live gold/USD prices and chart history"},
    {"name": "Listing & Asset", "description": "Seller listings, browsing, promotion"},
    {"name": "AI Engine", "description": "Smart matching, risk analysis, premium insights"},
    {"name": "Order & Transaction", "description": "Checkout preview/confirm and history"},
    {
        "name": "Subscription & Mock Payment",
        "description": "Premium subscription (internal mock payment)",
    },
    {"name": "Ownership", "description": "Signed fractional-ownership balance"},
    {"name": "System", "description": "Health and public configuration"},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        # One synchronous refresh so the cache is never empty on first request.
        await run_in_threadpool(refresh_prices_job)
        scheduler = build_scheduler()
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Sila (صِلة) API",
        description=(
            "Gold marketplace for the Iraqi market. All errors use "
            "`{error_code, message, status}`. Money and weights are decimal strings."
        ),
        version="1.0.0",
        openapi_tags=TAGS,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=["Retry-After"],
    )
    install_error_handlers(app)
    for router in (
        identity_router,
        market_router,
        listings_router,
        ai_router,
        orders_router,
        subscription_router,
        ownership_router,
        system_router,
        notifications_router,
        alerts_router,
        interest_router,
        admin_router,
    ):
        app.include_router(router)
    return app


app = create_app()
