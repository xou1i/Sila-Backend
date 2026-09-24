# Sila (صِلة) Backend · Execution Plan

Requirement IDs refer to `SPEC_MATRIX.md`; finding IDs (B*, S*) to `AUDIT_REPORT.md`; decisions (D-*) to `DECISIONS.md`.

## P0 · Broken, insecure or spec-violating money logic; clean start and migration

- [x] P0-1 Project skeleton: `requirements.txt` (pinned), `pyproject.toml` (ruff, pytest), `.env.example`, settings via `pydantic-settings`; remove the broken `routers/posts.py` and `service/transaction_service.py` (B1, B2, S7, S13, CC-07, CC-11, CC-12)
- [x] P0-2 PostgreSQL + SQLAlchemy 2 models matching the ERD exactly (UUID, NUMERIC, enums, CHECKs, indexes, FK actions, TIMESTAMPTZ) + first Alembic migration; `docker-compose.yml` for Postgres (DB-01…DB-10, S12)
- [x] P0-3 Error standard module (`AppError`, handlers for validation/HTTP/500) (CC-01…CC-05)
- [x] P0-4 Identity: bcrypt, JWT access/refresh with `type` claim, signup/login/refresh/me, `require_role`, KYC endpoints (ID-01…ID-19, S1, S2, S3, S9)
- [x] P0-5 Money core: karat price, commission tiers, rounding; no float (TX-02…TX-05, MD-04, D-07, D-08)
- [x] P0-6 Checkout: read-only preview; atomic confirm with `FOR UPDATE`, KYC gate first, active/weight checks, sold-out, transaction insert, ownership upsert + signature, audit log (TX-01…TX-18, TX-25, S4, S5, B3, B6, B7)
- [x] P0-7 Ownership signature canonical form + `GET /api/ownership/me` verification (OW-01…OW-08)
- [x] P0-8 Listings create with KYC gate and server-side price; no client `is_promoted` (LS-01…LS-05, S6)

## P1 · Missing endpoints/behaviors, jobs, rate limit, CORS, core tests

- [x] P1-1 Market data: provider, snapshot job, `/api/market/prices` from cache with fallback (MD-01…MD-09)
- [x] P1-2 Listings browse (filters, promoted-first, pagination), detail, PATCH, promote via internal mock payment, `seller_id=me` (LS-06…LS-21)
- [x] P1-3 Mock payment service + audit, subscription subscribe/status, daily expiry job (SB-*, PM-*)
- [x] P1-4 AI engine: match, risk-analysis, premium insights; rule-based + optional LLM (AI-01…AI-12)
- [x] P1-5 Transactions list/detail with party checks and anonymized buyer (TX-19…TX-22)
- [x] P1-6 Rate limiting (login, KYC, AI), CORS from env (ID-20, AI-12, CC-06, S8, S10)
- [x] P1-7 Test suite on real Postgres: money boundaries, preview side effects, confirm paths, concurrency, signature tampering, subscription, RBAC, error format, promoted sorting

## P2 · Frontend readiness, seed, docs, polish

- [x] P2-1 Quote token + `PRICE_CHANGED`, `Idempotency-Key` on confirm and listing create (TX-23, TX-24, D-16, D-17)
- [x] P2-2 Price history endpoint + `change_24h_pct`, `/api/health`, `/api/config` (MD-08, MD-10, CC-15)
- [x] P2-3 OpenAPI polish (tags, response models, error responses, examples) + export `docs/api/openapi.json` (CC-13, CC-14)
- [x] P2-4 Dockerfile + compose API service with auto-migrate; seed script with Iraqi demo data (CC-19)
- [x] P2-5 `README.md`, `docs/api/API_CONTRACT.md`
- [x] P2-6 ruff clean, float grep, final full verification on a fresh DB

## Verification log

| Check | Result |
|---|---|
| `alembic upgrade head` → `downgrade base` → `upgrade head` on an empty DB; `alembic check` | clean, no drift |
| `pytest` (fresh schema via migrations each session, PostgreSQL 16) | 104 passed, warnings treated as errors |
| Concurrency tests re-run 3× (8 buyers × 30 g on 100 g; same-key duplicates; same-investor parallel first purchases) | stable: exactly 3 succeed, 10.000 g left, never oversold |
| `ruff check .` / `ruff format --check .` | clean |
| `grep -rnE "\bfloat\b\|Float\b\|float\(" app/` | only timeout settings and rate-limiter timing; no money/weight path |
| `docker compose up --build` | migrations auto-applied, live price fetched, jobs scheduled, no warnings in logs |
| Seed in container + manual end-to-end run (prices, browse, match, preview, confirm with Idempotency-Key, ownership verify, insights, history 1D–1Y, KYC interrupt, expired-premium job) | all as specified |
