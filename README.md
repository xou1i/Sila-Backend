# Sila (صِلة) Backend

FastAPI backend for Sila, a gold marketplace for the Iraqi market. It has live gold/USD prices, AI smart matching, fractional purchases with tiered commission, mock KYC (توقيعك), signed ownership records, a premium subscription and promoted listings.

- **Stack:** Python 3.11 · FastAPI · SQLAlchemy 2 · PostgreSQL 16 · Alembic · Pydantic v2 · APScheduler · JWT · bcrypt
- **Frontend contract:** [`docs/api/API_CONTRACT.md`](docs/api/API_CONTRACT.md) · schema [`docs/api/openapi.json`](docs/api/openapi.json)
- **Audit trail:** [`docs/audit/`](docs/audit/) (spec matrix, audit report, decisions, plan)

## Run with Docker (one command)

```bash
docker compose up --build            # PostgreSQL + API on http://localhost:8010 (migrations auto-applied)
docker compose exec api python -m app.scripts.seed    # optional demo data
```
Port 8010 busy? Use e.g. `API_PORT=8080 docker compose up --build`. PostgreSQL is exposed on host port **5433**.
Compose ships dev-only secrets so it works on a fresh clone. For anything shared, put real secrets in `.env`.

## Run locally without Docker (needs PostgreSQL ≥ 14)

Inside a virtualenv (`python -m venv .venv`, then activate it):
```bash
pip install -r requirements-dev.txt
cp .env.example .env                       # set DATABASE_URL to your PostgreSQL + real secrets
alembic upgrade head && uvicorn app.main:app --reload --port 8010
```
Open `http://localhost:8010/docs`. No local PostgreSQL? `docker compose up -d db` starts one on port 5433, which already matches `.env.example`.

Seed demo data: `python -m app.scripts.seed` (refuses to run on a non-empty DB; `--reset` wipes and re-seeds).

### Demo accounts (password `Sila@2026`)
| Email | Role | Use it to demo |
|---|---|---|
| `zainab@sila.iq` | investor | Premium insights, portfolio, checkout |
| `haider@sila.iq` | investor | High-risk matching, large holdings |
| `ali@sila.iq` | investor | KYC interrupt at checkout |
| `sara@sila.iq` | investor | Expired Premium → upgrade |
| `karrada@sila.iq` | seller | مجوهرات الكرّادة: listings, promotion |
| `nahr@sila.iq` | seller | صاغة شارع النهر: sales log, sold-out listing |
| `mansour@sila.iq` | seller | KYC interrupt at publish |

## Tests & quality

```bash
docker compose up -d db                      # tests use the sila_test database on port 5433
pytest                                       # 104 tests on real PostgreSQL, incl. concurrency
ruff check . && ruff format --check .
```
The test session drops and re-creates the `sila_test` schema via `alembic upgrade head`; it refuses to run against a database whose name lacks "test". Override the target with `TEST_DATABASE_URL`.

## Configuration

Every setting is in [`.env.example`](.env.example). Key ones:

| Variable | Purpose |
|---|---|
| `JWT_SECRET`, `OWNERSHIP_SIGNING_SECRET`, `QUOTE_SIGNING_SECRET` | Required, ≥ 32 chars. Never commit. Changing `OWNERSHIP_SIGNING_SECRET` invalidates every stored ownership signature. |
| `CORS_ORIGINS` | Comma-separated frontend origins (Vite: `http://localhost:5173`) |
| `PRICE_REFRESH_SECONDS` | Market price job interval (default 60) |
| `PROMOTION_FEE_IQD`, `SUBSCRIPTION_PRICE_IQD`, … | Server-side business values (exposed read-only at `/api/config`) |
| `AI_API_KEY` | Optional. Empty = deterministic rule-based AI. Set = Claude (`AI_MODEL`, default `claude-opus-5`) rewrites the Arabic explanation text only. |
| `MOCK_PAYMENT_FAIL` | `true` to demo payment-failure paths |

**Deploying the frontend:** browsers only reach the API from origins listed in `CORS_ORIGINS`. When the frontend gets its production domain, add it there (for example `CORS_ORIGINS=https://<frontend-domain>`, several origins comma-separated) and restart the API. No code change is needed.

## Architecture

A modular monolith: one FastAPI app, and each business capability has its own `router.py` (HTTP) and `service.py` (logic):

```
app/
  core/        config, db, errors (one error format), security (bcrypt/JWT), deps (RBAC),
               money (Decimal math), signature (HMAC), rate_limit, audit
  modules/     identity · market · listings · ai · orders · subscription · payments (internal) · ownership · system
  jobs/        APScheduler: price refresh (~1 min), subscription expiry (daily)
  scripts/     seed, export_openapi
alembic/       migrations (applied on container start)
tests/         pytest + httpx AsyncClient against PostgreSQL
```

Key guarantees:
- **Money is `Decimal` / `NUMERIC` end to end**, with one rounding rule (ROUND_HALF_UP to 2 dp: price → principal → commission; total = sum).
- **Checkout** runs in one DB transaction with `SELECT … FOR UPDATE` on the listing. The KYC check comes first. Stock is decremented, the listing goes to `sold_out` at 0, and the transaction row, the ownership upsert, the re-signed HMAC and the audit rows are written together, then all committed or all rolled back.
- **Ownership balance** is verified with `hmac.compare_digest` on every read. If someone tampers with it, the read returns `403 INTEGRITY_CHECK_FAILED` and a security audit row is written, and later purchases refuse to re-sign the tampered balance.
- **Market prices** are never fetched per request: the job writes `price_snapshots`, and readers use the latest row. If the provider is down, readers keep the last value and its timestamp.
- **Mock payment** is an internal service call (no public route) and is audit-logged with its `payment_ref`.
