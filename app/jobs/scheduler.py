"""Background jobs (APScheduler): market prices every ~minute, subscription expiry daily."""

import logging
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.modules.alerts import service as alerts
from app.modules.market import service as market
from app.modules.subscription import service as subscription

logger = logging.getLogger("sila.jobs")


def refresh_prices_job() -> None:
    with SessionLocal() as db:
        try:
            snapshot = market.refresh_prices(db)
        except Exception:
            logger.exception("price refresh job failed")
            return
        if snapshot is None:
            return
        # Premium price alerts are checked against every fresh price
        try:
            fired = alerts.check_alerts(db, snapshot)
            if fired:
                logger.info("price alerts: %d fired", fired)
        except Exception:
            logger.exception("price alert check failed")


def expire_subscriptions_job() -> None:
    with SessionLocal() as db:
        try:
            changed = subscription.expire_subscriptions(db)
            if changed:
                logger.info("subscription expiry job: %d user(s) moved to free", changed)
        except Exception:
            logger.exception("subscription expiry job failed")


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone=UTC)
    scheduler.add_job(
        refresh_prices_job,
        "interval",
        seconds=settings.price_refresh_seconds,
        id="refresh_prices",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        expire_subscriptions_job,
        "cron",
        hour=0,
        minute=5,
        id="expire_subscriptions",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),  # also catch up once at startup
    )
    return scheduler
