# Sila (صِلة) Backend · Audit Report

Audit of the backend **as received** (baseline commit `5fd8be4`), measured against `SPEC_MATRIX.md`.
Runtime checks were done with the in-process ASGI client against the shipped `smartbridge.db` (the file was restored afterwards).

## 1. Overview

| Item | Found |
|---|---|
| Files | `app/main.py` (all routes), `app/models.py`, `app/schemas.py`, `app/database.py`, `app/seed.py`, `app/routers/posts.py`, `app/service/transaction_service.py` |
| Stack | FastAPI + SQLAlchemy (sync) + Pydantic v2 (`EmailStr`) + **SQLite** (`sqlite:///./smartbridge.db`) |
| Dependencies | None declared (no `requirements.txt` / `pyproject.toml`), nothing pinned |
| Migrations | None (`Base.metadata.create_all` at import) |
| Config / secrets | None; HMAC secret hard-coded in `transaction_service.py:8` |
| Docker | None |
| Tests | None |
| How to run today | `pip install fastapi uvicorn sqlalchemy pydantic[email]` then `uvicorn app.main:app` (undocumented) |

**Maturity:** an early prototype. It has 7 routes covering about 10% of the spec. There is no authentication, the IDs are integers, weights and prices are floats on SQLite, and passwords are stored in plaintext. Two of the seven Python modules cannot be imported. The only correct pieces of business logic are the commission-tier function and a `with_for_update()` call, both in `transaction_service.py`, and that module is dead code because it fails to import. The backend needs a structured rebuild on the documented stack, keeping the same framework and ORM, before it can satisfy the spec.

## 2. Spec compliance (baseline)

| Group | ✅ | ⚠️ | ❌ | Notes |
|---|---|---|---|---|
| A. Identity & Security (20) | 0 | 3 | 17 | Signup exists but stores the plaintext password (`main.py:45`), returns `400` on duplicates (`main.py:40`), has no email normalization and no password rules. No login, refresh, `/users/me` or JWT. KYC is `POST /api/kyc/verify/{user_id}` with no auth, so anyone can verify anyone (`main.py:54`). No seller KYC route, no RBAC, no rate limiting. |
| B. Market Data (10) | 0 | 1 | 9 | `get_live_gold_price()` fetches an exchange-rate URL and ignores the response, always returning the constant `75.50` USD (`main.py:27-34`). It is called on **every request**, with no cache, job, IQD conversion, 22K price or timestamp. |
| C. Listing & Asset (21) | 0 | 3 | 18 | Create takes `seller_id` **from the body** (impersonation) and `is_promoted` from the client (free promotion) (`main.py:80-93`). There is no price computation and no weight/karat validation (−5 g at karat 99 was accepted). No filters, sort, detail, PATCH, promote or pagination. |
| D. AI Engine (12) | 0 | 0 | 12 | Not implemented. |
| E. Order & Transaction (25) | 0 | 4 | 21 | Preview ignores karat, uses a flat 1.5% fee and float math (`main.py:106-109`). Confirm takes `investor_id` from the body, has no row lock and creates no transaction or ownership row (`main.py:119-141`). A **negative weight purchase is accepted and adds stock** (verified: `weight_grams=-3` → `200`, `remaining_weight` 10 → 13). The service version (`transaction_service.py`) has a lock and the correct tiers, but it imports a non-existent `Transaction` model and is never wired. |
| F. Subscription & Payment (11) | 0 | 0 | 11 | Not implemented. |
| G. Ownership (8) | 0 | 1 | 7 | Dead code signs `investor_id:weight:timestamp` (purchase weight, not the accumulated balance) with a hard-coded secret, and never stores or verifies it. |
| H. Data Layer (10) | 0 | 0 | 10 | SQLite, integer PKs, `Float`, free-text `String` enums with wrong values (`"Medium"`, `"Basic"`), no CHECKs, no `created_at`/`updated_at`, no FK actions, no `transactions`/`fractional_ownership_records` tables, no migrations. |
| I. Cross-cutting (19) | 0 | 1 | 18 | CORS `*` with credentials. Default FastAPI `{"detail": ...}` errors. OpenAPI title "SmartBridge API - Trident Wealth". |

## 3. Bugs & runtime errors (reproduced)

| # | Bug | Reproduction |
|---|---|---|
| B1 | `app/routers/posts.py` fails to import (`ImportError: cannot import name 'Post'`); it is a leftover from another project | `python -c "import app.routers.posts"` |
| B2 | `app/service/transaction_service.py` fails to import (`cannot import name 'Transaction'`) | `python -c "import app.service.transaction_service"` |
| B3 | Negative purchase weight is accepted and **increases** stock | `POST /api/transactions/confirm {"investor_id":2,"listing_id":1,"weight_grams":-3}` → 200, remaining 13 |
| B4 | Invalid listing accepted (`total_weight_grams=-5`, `karat=99`) | `POST /api/listings` → 200 |
| B5 | Market price is a constant; the HTTP result is discarded | `main.py:31-32` |
| B6 | Float equality `available == 0` for sold-out never triggers after e.g. `0.1+0.2` style residues | `main.py:132` |
| B7 | No transaction or ownership record is written on purchase | `main.py:131-141` |
| B8 | `models.py` has duplicate imports and an `image_url` column not in the spec | `models.py:1-6,30` |
| B9 | `seed.py` deletes all users/listings on each run and stores plaintext passwords | `seed.py:10-11,18` |
| B10 | Non-UTF-8 console crashes printing Arabic responses (Windows cp1252) | tooling only; fixed by `PYTHONIOENCODING=utf-8` in docs |

## 4. Security findings

| # | Severity | Finding | Location |
|---|---|---|---|
| S1 | **Critical** | No authentication at all; every "protected" action trusts IDs in the body or path | `main.py` whole file |
| S2 | **Critical** | Plaintext password storage | `main.py:45`, `seed.py:18,27` |
| S3 | **Critical** | Anyone can KYC-verify any user (`/api/kyc/verify/{user_id}`, unauthenticated) | `main.py:54` |
| S4 | **Critical** | Negative-quantity purchase mints gold (B3); no input validation on money/weight | `main.py:119-141` |
| S5 | **High** | Race condition on confirm (no row lock in the wired code) → overselling | `main.py:127-135` |
| S6 | **High** | Client-controlled `is_promoted` bypasses paid promotion | `main.py:92` |
| S7 | **High** | Hard-coded HMAC secret committed in source | `transaction_service.py:8` |
| S8 | **High** | CORS `allow_origins=["*"]` with `allow_credentials=True` | `main.py:19-25` |
| S9 | Medium | Duplicate-email error leaks account existence with 400 (spec: 409) | `main.py:40` |
| S10 | Medium | No rate limiting on login/KYC/AI | n/a |
| S11 | Medium | No audit logging | n/a |
| S12 | Low | Float money math (rounding errors) | `main.py:106-116` |
| S13 | Low | Unpinned, undeclared dependencies | n/a |

## 5. Data-layer findings

- Engine is SQLite; the spec requires PostgreSQL (row locks, NUMERIC, TIMESTAMPTZ, native enums).
- All weights/prices are `Float` (spec: NUMERIC(10,3)/(14,2)/(16,2)/(5,4)/(12,3)).
- PKs are `Integer` (spec: UUID `gen_random_uuid()`).
- Enums are free strings with non-spec values (`"Medium"`, `"Basic"`, `"Premium"`).
- Missing tables: `transactions`, `fractional_ownership_records`. Missing columns: `base_price_per_gram`, `promotion_expiry_date`, `subscription_expiry_date`, all timestamps.
- No CHECK constraints, no FK `ON DELETE` actions, indexes only implicit.
- No Alembic; schema created by `create_all` at import time.
- Existing `smartbridge.db` / `creator_guard.db` contain only demo seed rows with plaintext passwords. They are **left on disk untouched** (git-ignored) and are no longer used.

## 6. Frontend-readiness gaps

Found none of the §5 items: no auth flow, no stable contract (int IDs, floats, inconsistent field names like `listing_id` vs spec `asset_id`, `weight_grams` vs `purchased_weight_grams`), no pagination, no price history, no quote freshness, no idempotency, no health endpoint, no OpenAPI tags/response models on confirm/KYC, and no exported schema.

## 7. Test coverage

0 tests, no test tooling.

## 8. Post-hardening verification

- `pytest`: **104 passed** against PostgreSQL 16. The schema is built with `alembic upgrade head` on an empty database each run.
- `ruff check .` and `ruff format --check .`: clean.
- Float check, `grep -rnE "float|Float|float\(" app/`. The only hits are `price_fetch_timeout_seconds`, `ai_timeout_seconds`, the provider's `timeout` parameter and the rate limiter's monotonic clock. Provider JSON is parsed with `parse_float=Decimal`, so no money or weight value is ever a binary float.
- All Critical/High findings (S1–S8) and bugs B1–B9 are fixed. Status per requirement is in `SPEC_MATRIX.md`; the steps are in `PLAN.md`.
