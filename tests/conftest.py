"""Test fixtures: real PostgreSQL (docker compose `db`, database `sila_test`).

The schema is built by running the Alembic migrations on an empty database, so every test run
also proves the migration history applies cleanly.
"""

import os

# Never inherit DATABASE_URL: the session fixture drops the whole schema.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sila:sila@localhost:5433/sila_test"
)
assert "test" in os.environ["DATABASE_URL"].rsplit("/", 1)[-1], "tests need a *test* database"
os.environ.update(
    {
        "JWT_SECRET": "test-jwt-secret-test-jwt-secret-test-jwt-secret",
        "OWNERSHIP_SIGNING_SECRET": "test-ownership-secret-test-ownership-secret",
        "QUOTE_SIGNING_SECRET": "test-quote-secret-test-quote-secret-test-q",
        "SCHEDULER_ENABLED": "false",
        "BCRYPT_ROUNDS": "4",
        "AI_API_KEY": "",
        "RATE_LIMIT_LOGIN": "1000/minute",
        "RATE_LIMIT_KYC": "1000/minute",
        "RATE_LIMIT_AI": "1000/minute",
        "CORS_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
    }
)

import itertools  # noqa: E402
from collections.abc import AsyncIterator, Iterator  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import httpx2  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PriceSnapshot  # noqa: E402

# 24K = 145,000 IQD/gram (the spec's own example) → 21K = 126,875.00
PRICE_24K = Decimal("145000.00")
PASSWORD = "Sila@2026"
_TABLES = (
    "audit_logs, transactions, fractional_ownership_records, asset_listings, users, price_snapshots"
)
_counter = itertools.count()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    settings = get_settings()
    saved = settings.model_dump()
    limiter.reset()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    add_snapshot(PRICE_24K)
    yield
    for key, value in saved.items():
        setattr(settings, key, value)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def add_snapshot(price_24k: Decimal, at: datetime | None = None, source: str = "live") -> None:
    """Insert a snapshot with an exact 24K IQD/gram price."""
    with SessionLocal() as session:
        session.add(
            PriceSnapshot(
                fetched_at=at or datetime.now(UTC),
                xau_usd_per_ounce=Decimal("3440.0000"),
                usd_iqd=Decimal("1310.0000"),
                gold_24k_iqd_per_gram=price_24k,
                source=source,
            )
        )
        session.commit()


class Actor:
    def __init__(self, user: dict, token: str) -> None:
        self.user = user
        self.id = user["id"]
        self.headers = {"Authorization": f"Bearer {token}"}


async def make_user(
    client: httpx2.AsyncClient,
    role: str = "investor",
    *,
    kyc: bool = False,
    risk_profile: str = "medium",
    name: str | None = None,
) -> Actor:
    n = next(_counter)
    email = f"{role}{n}@test.iq"
    body = {"role": role, "full_name": name or f"{role} {n}", "email": email, "password": PASSWORD}
    if role == "investor":
        body["risk_profile"] = risk_profile
    r = await client.post("/api/auth/signup", json=body)
    assert r.status_code == 201, r.text
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    actor = Actor(r.json()["user"], r.json()["access_token"])
    if kyc:
        path = "/api/kyc/verify" if role == "investor" else "/api/kyc/seller"
        r = await client.post(path, headers=actor.headers)
        assert r.status_code == 200, r.text
    return actor


async def make_listing(
    client: httpx2.AsyncClient, seller: Actor, grams: str = "100.000", karat: int = 24
) -> dict:
    r = await client.post(
        "/api/listings",
        json={"total_weight_grams": grams, "karat": karat},
        headers=seller.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def buy(
    client: httpx2.AsyncClient,
    investor: Actor,
    asset_id: str,
    grams: str,
    idempotency_key: str | None = None,
) -> httpx2.Response:
    r = await client.post(
        "/api/transactions/preview",
        json={"asset_id": asset_id, "purchased_weight_grams": grams},
        headers=investor.headers,
    )
    if r.status_code != 200:
        return r
    headers = dict(investor.headers)
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(
        "/api/transactions/confirm",
        json={
            "asset_id": asset_id,
            "purchased_weight_grams": grams,
            "quote_token": r.json()["quote_token"],
        },
        headers=headers,
    )


def hours_ago(h: float) -> datetime:
    return datetime.now(UTC) - timedelta(hours=h)
