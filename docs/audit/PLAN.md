# Sila (صِلة) Backend · Execution Plan

Requirement IDs refer to `SPEC_MATRIX.md`; finding IDs (B*, S*) to `AUDIT_REPORT.md`; decisions (D-*) to `DECISIONS.md`.

## P0 · Broken, insecure or spec-violating money logic; clean start and migration

- [ ] P0-1 Project skeleton: `requirements.txt` (pinned), `pyproject.toml` (ruff, pytest), `.env.example`, settings via `pydantic-settings`; remove the broken `routers/posts.py` and `service/transaction_service.py` (B1, B2, S7, S13, CC-07, CC-11, CC-12)
- [ ] P0-2 PostgreSQL + SQLAlchemy 2 models matching the ERD exactly (UUID, NUMERIC, enums, CHECKs, indexes, FK actions, TIMESTAMPTZ) + first Alembic migration; `docker-compose.yml` for Postgres (DB-01…DB-10, S12)
- [ ] P0-3 Error standard module (`AppError`, handlers for validation/HTTP/500) (CC-01…CC-05)
- [ ] P0-4 Identity: bcrypt, JWT access/refresh with `type` claim, signup/login/refresh/me, `require_role`, KYC endpoints (ID-01…ID-19, S1, S2, S3, S9)
- [ ] P0-5 Money core: karat price, commission tiers, rounding; no float (TX-02…TX-05, MD-04, D-07, D-08)
- [ ] P0-6 Checkout: read-only preview; atomic confirm with `FOR UPDATE`, KYC gate first, active/weight checks, sold-out, transaction insert, ownership upsert + signature, audit log (TX-01…TX-18, TX-25, S4, S5, B3, B6, B7)
- [ ] P0-7 Ownership signature canonical form + `GET /api/ownership/me` verification (OW-01…OW-08)
- [ ] P0-8 Listings create with KYC gate and server-side price; no client `is_promoted` (LS-01…LS-05, S6)

## P1 · Missing endpoints/behaviors, jobs, rate limit, CORS, core tests

- [ ] P1-1 Market data: provider, snapshot job, `/api/market/prices` from cache with fallback (MD-01…MD-09)
- [ ] P1-2 Listings browse (filters, promoted-first, pagination), detail, PATCH, promote via internal mock payment, `seller_id=me` (LS-06…LS-21)
- [ ] P1-3 Mock payment service + audit, subscription subscribe/status, daily expiry job (SB-*, PM-*)
- [ ] P1-4 AI engine: match, risk-analysis, premium insights; rule-based + optional LLM (AI-01…AI-12)
- [ ] P1-5 Transactions list/detail with party checks and anonymized buyer (TX-19…TX-22)
- [ ] P1-6 Rate limiting (login, KYC, AI), CORS from env (ID-20, AI-12, CC-06, S8, S10)
- [ ] P1-7 Test suite on real Postgres: money boundaries, preview side effects, confirm paths, concurrency, signature tampering, subscription, RBAC, error format, promoted sorting

## P2 · Frontend readiness, seed, docs, polish

- [ ] P2-1 Quote token + `PRICE_CHANGED`, `Idempotency-Key` on confirm and listing create (TX-23, TX-24, D-16, D-17)
- [ ] P2-2 Price history endpoint + `change_24h_pct`, `/api/health`, `/api/config` (MD-08, MD-10, CC-15)
- [ ] P2-3 OpenAPI polish (tags, response models, error responses, examples) + export `docs/api/openapi.json` (CC-13, CC-14)
- [ ] P2-4 Dockerfile + compose API service with auto-migrate; seed script with Iraqi demo data (CC-19)
- [ ] P2-5 `README.md`, `docs/api/API_CONTRACT.md`
- [ ] P2-6 ruff clean, float grep, final full verification on a fresh DB
