# Sila (صِلة) · Spec Matrix

Every testable requirement extracted from `CLAUDE/Product/` (01–07 + Workflow 00–08) and the must-haves in `CLAUDE/BACKEND-AUDIT-PROMPT.md` §4–5.
Source abbreviations: `DM` = 02_domain_model, `BC` = 03_business_capabilities, `SD` = 04_system_design, `DBML` = 05_schema.dbml, `ERD` = 06_erd_database_design, `API` = 07_api_design, `WFnn` = Workflow/nn-*, `P§` = audit prompt section.

**Status** is the state **after** the hardening work (see `AUDIT_REPORT.md` §2 for the state found at the start, and `tests/` for the evidence).
Legend: ✅ done · ⚠️ partial, with an accepted reason · ❌ missing

## A. Identity & Security

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| ID-01 | API §1, WF01 §2 | `POST /api/auth/signup` accepts `role`, `full_name`, `email`, `password`, `risk_profile` (required when investor) | ✅ | `app/modules/identity/schemas.py`, `tests/test_auth.py` |
| ID-02 | WF01 §2 | Seller signup does not require `risk_profile` (stored NULL for sellers) | ✅ | `identity/schemas.py` validator, `test_auth.py` |
| ID-03 | WF01 §2, WF01 edge | Email unique; duplicate → `409` (`EMAIL_ALREADY_EXISTS`) | ✅ | `identity/service.py`, `test_auth.py` |
| ID-04 | P§4.4 | Email normalized (trimmed, lower-case) before uniqueness check and login | ✅ | `identity/schemas.py`, `test_auth.py` |
| ID-05 | DM 1.1, SD §5, WF01 §2 | Password hashed with bcrypt before storage; plaintext never stored | ✅ | `app/core/security.py` |
| ID-06 | WF01 edge, P§4.4 | Password min 8 chars → else `422 VALIDATION_ERROR` | ✅ | `test_auth.py` |
| ID-07 | WF01 §2 | New user: `kyc_verified=false`, `subscription_tier='free'` | ✅ | `models.py` defaults, `test_auth.py` |
| ID-08 | WF01 §1 | Role fixed at signup; no endpoint changes it | ✅ | no role-update route exists |
| ID-09 | API §1, WF01 §3 | `POST /api/auth/login` returns `access_token` + `refresh_token` | ✅ | `identity/router.py`, `test_auth.py` |
| ID-10 | P§4.4 | Generic login error (no user enumeration), constant-time-ish path for unknown email | ✅ | `identity/service.py` (dummy bcrypt) |
| ID-11 | API §1 | `POST /api/auth/refresh` issues a new access token from a refresh token | ✅ | `test_auth.py` |
| ID-12 | SD §5, P§4.4 | Short-lived access token; TTLs + secret from env | ✅ | `core/config.py` |
| ID-13 | P§4.4 | Token `type` claim; refresh token rejected as access token and vice versa | ✅ | `core/security.py`, `test_auth.py` |
| ID-14 | WF02 edge | Invalid/expired token → `401 UNAUTHORIZED` | ✅ | `core/deps.py`, `test_errors.py` |
| ID-15 | API §1, P§5.6 | `GET /api/users/me` returns role, full_name, email, kyc_verified, risk_profile, subscription_tier, subscription_expiry_date | ✅ | `test_auth.py` |
| ID-16 | API §1, WF02-A | `POST /api/kyc/verify` (investor only) sets `kyc_verified=true` | ✅ | `test_kyc.py` |
| ID-17 | API §1, WF02-B | `POST /api/kyc/seller` (seller only) sets `kyc_verified=true` | ✅ | `test_kyc.py` |
| ID-18 | WF02 note | KYC is permanent; repeated verify is a harmless 200 | ✅ | `test_kyc.py` |
| ID-19 | SD §5 RBAC | Explicit role separation (`require_role`) on every role-specific endpoint | ✅ | `core/deps.py`, `test_rbac.py` |
| ID-20 | API §1, SD §5 | Rate limiting on `/api/auth/login`, `/api/kyc/*` → `429 RATE_LIMITED` | ✅ | `core/rate_limit.py`, `test_errors.py` |

## B. Market Data

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| MD-01 | API §2, WF03 §1 | `GET /api/market/prices` is public | ✅ | `test_market.py` |
| MD-02 | API §2, WF03 §1 | Returns price per gram for 24/22/21/18 + USD/IQD rate | ✅ | `test_market.py` |
| MD-03 | WF03 §1, SD §1 | Served from an internal cache refreshed by a background job (~60 s); never calls the provider per request | ✅ | `modules/market/service.py`, `jobs/scheduler.py` |
| MD-04 | BC §2, DM 3.1 | Karat formula `price(k) = price24 × k / 24` | ✅ | `core/money.py`, `test_money.py` |
| MD-05 | BC §2 | Global (USD/oz) price converted to IQD per gram | ✅ | `market/provider.py` |
| MD-06 | WF03 edge | Provider failure → last cached price + its "last updated" timestamp; endpoint never errors because of the provider | ✅ | `test_market.py` |
| MD-07 | P§4.3 | Response includes last-update timestamp | ✅ | `updated_at` |
| MD-08 | P§5.3 | `change_24h_pct` in `/api/market/prices` | ✅ | `test_market.py` |
| MD-09 | P§5.3 | Price job stores a snapshot in `price_snapshots` | ✅ | `models.py`, `market/service.py` |
| MD-10 | P§5.3 | `GET /api/market/prices/history?karat=&range=1D\|1W\|1M\|3M\|1Y` returns downsampled `[{ts, price_per_gram}]` | ✅ | `test_market.py` |

## C. Listing & Asset

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| LS-01 | API §3, WF06-A | `POST /api/listings` (seller) with `total_weight_grams`, `karat` | ✅ | `test_listings.py` |
| LS-02 | API §3, WF02-B, SD §5 | Server-side KYC gate on listing creation → `403 KYC_NOT_VERIFIED` | ✅ | `test_listings.py` |
| LS-03 | BC §3, WF06-A §2 | `base_price_per_gram` computed server-side from the cached 24K price; client cannot set it | ✅ | `test_listings.py` |
| LS-04 | WF06-A §2 | On create: `available = total`, `status='active'` | ✅ | `test_listings.py` |
| LS-05 | DM 1.2, ERD §2 | `karat ∈ {18,21,22,24}`, weight `> 0` (validation + DB CHECK) | ✅ | schemas + migration |
| LS-06 | API §3, WF03 §2 | `GET /api/listings` public, filters `karat`, `min_price`, `max_price` | ✅ | `test_listings.py` |
| LS-07 | WF03 §2 | Default / `sort=promoted_first`: effectively promoted (`is_promoted AND expiry > now()`) first, then newest | ✅ | `test_listings.py` |
| LS-08 | WF03 edge | No match → empty list (200) | ✅ | `test_listings.py` |
| LS-09 | WF03 §2, P§5.7 | Card fields: seller name, available/total grams, karat, base price, status, effective `is_promoted`, `promotion_expiry_date`, seller KYC flag, `created_at` | ✅ | `listings/schemas.py` |
| LS-10 | P§5.7 | Pagination (`limit`/`offset`) with total count | ✅ | `test_listings.py` |
| LS-11 | API §3, WF03 §3 | `GET /api/listings/{id}` public detail; unknown id → `404 NOT_FOUND` | ✅ | `test_listings.py` |
| LS-12 | API §3, WF06-C | `PATCH /api/listings/{id}` owner only → `status` `active`↔`suspended` | ✅ | `test_listings.py` |
| LS-13 | WF06 edge | Non-owner PATCH/promote → `403 FORBIDDEN` | ✅ | `test_rbac.py` |
| LS-14 | P§4.3 | `sold_out` listing cannot be re-activated | ✅ | `test_listings.py` |
| LS-15 | WF06-C | No delete endpoint for listings | ✅ | `test_listings.py` (DELETE → 405) |
| LS-16 | WF08-B §1 | Seller's own listings: `GET /api/listings?seller_id=me` (all statuses) | ✅ | `test_listings.py` |
| LS-17 | API §3, WF06-B | `POST /api/listings/{id}/promote` owner + KYC-verified | ✅ | `test_listings.py` |
| LS-18 | API §3 note, WF06-B §1 | Promotion fee is a server config value; client cannot send it | ✅ | `core/config.py` |
| LS-19 | WF06-B §3-4 | Promotion pays via internal mock payment, then sets `is_promoted=true`, `promotion_expiry_date=now()+duration` | ✅ | `test_listings.py` |
| LS-20 | WF06 edge | Mock payment failure → listing stays unpromoted, error returned | ✅ | `test_subscription.py::test_payment_failure_*` |
| LS-21 | DM 3 | `available_weight_grams` reaching 0 → `sold_out` automatically | ✅ | `test_checkout.py` |

## D. AI Engine

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| AI-01 | API §4, WF04 | `POST /api/ai/match` investor-only, input `budget_iqd` | ✅ | `test_ai.py` |
| AI-02 | WF04 §2 | `risk_profile` read from the account server-side, not from the request | ✅ | `ai/service.py` |
| AI-03 | WF04 §2 | Only `active` listings considered; uses live cached prices | ✅ | `test_ai.py` |
| AI-04 | WF04 §3 | Ranked results, each with a short reason | ✅ | `test_ai.py` |
| AI-05 | BC §4 | Smart matching free for all investors (no subscription check) | ✅ | `test_ai.py` |
| AI-06 | WF04 edge | Budget below cheapest option → empty results + clear message | ✅ | `test_ai.py` |
| AI-07 | WF04 edge, P§4.3 | Provider failure / missing key → deterministic rule-based fallback; documented `AI_UNAVAILABLE` when nothing can be computed | ✅ | `ai/service.py`, `test_ai.py` |
| AI-08 | API §4 | `POST /api/ai/risk-analysis` investor, input `asset_id`, `weight_grams`, returns short insight | ✅ | `test_ai.py` |
| AI-09 | WF05 edge | Risk-analysis failure never blocks checkout (preview returns without insight) | ✅ | `orders/service.py` |
| AI-10 | API §4, WF07 §3 | `GET /api/ai/insights` checks `subscription_expiry_date > now()` (not tier) → else `403 SUBSCRIPTION_REQUIRED` | ✅ | `test_subscription.py` |
| AI-11 | WF07 §3 | Insights = alerts + market trend + portfolio performance | ✅ | `test_subscription.py` |
| AI-12 | SD §5, P§4.4 | Rate limiting on `/api/ai/*` | ✅ | `core/rate_limit.py` |

## E. Order & Transaction

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| TX-01 | API §5, WF05 §2 | `POST /api/transactions/preview` investor, body `asset_id`, `purchased_weight_grams` | ✅ | `test_checkout.py` |
| TX-02 | WF05 §2 | `execution_price_per_gram = price24 × karat/24` at preview time | ✅ | `test_checkout.py` |
| TX-03 | DM 3.2, WF05 §2 | Commission tiers `<50 → 1.5%`, `50–200 incl. → 1.0%`, `>200 → 0.5%` | ✅ | `test_money.py` (49.999 / 50 / 200 / 200.001) |
| TX-04 | DM 3.2 | `principal = g × p`, `commission = principal × rate`, `total = principal + commission`; one documented rounding rule | ✅ | `core/money.py`, `DECISIONS.md` D-07 |
| TX-05 | DM 3.2, WF05 post | Commission paid by investor on top; seller receives full principal | ✅ | `test_checkout.py` |
| TX-06 | WF05 §2 | Preview is read-only: no reservation, no writes | ✅ | `test_checkout.py::test_preview_has_no_side_effects` |
| TX-07 | API §5 | Preview includes AI risk insight | ✅ | `test_checkout.py` |
| TX-08 | API §5, WF05 §4 | `POST /api/transactions/confirm` investor | ✅ | `test_checkout.py` |
| TX-09 | WF05 §4, SD §5 | KYC checked server-side first → `403 KYC_NOT_VERIFIED` | ✅ | `test_checkout.py` |
| TX-10 | WF05 §4 | Listing must be `active` → `LISTING_NOT_ACTIVE` | ✅ | `test_checkout.py` |
| TX-11 | WF05 §4.1 | Re-check weight at execution → `INSUFFICIENT_AVAILABLE_WEIGHT` | ✅ | `test_checkout.py` |
| TX-12 | SD §4, WF05 §4 | One atomic DB transaction with `SELECT … FOR UPDATE` on the listing row | ✅ | `orders/service.py` |
| TX-13 | SD §4 | Concurrent confirms never oversell | ✅ | `test_checkout.py::test_concurrent_confirms_never_oversell` |
| TX-14 | WF05 §4.2-3 | Decrement available weight; auto `sold_out` at 0 | ✅ | `test_checkout.py` |
| TX-15 | WF05 §4.4 | Transaction row stores the values computed at preview | ✅ | quote token, `test_checkout.py` |
| TX-16 | WF05 §4.5 | Upsert ownership record (`+= grams`) and regenerate signature | ✅ | `test_ownership.py` |
| TX-17 | WF05 §4.6 | Commit or full rollback | ✅ | single `Session` transaction |
| TX-18 | WF05 §5 | Confirm response includes new `total_accumulated_grams` | ✅ | `test_checkout.py` |
| TX-19 | API §5, WF08 | `GET /api/transactions`: investor → own purchases; seller → sales on own listings | ✅ | `test_transactions.py` |
| TX-20 | API §5 | `GET /api/transactions/{id}` only for a party to it | ✅ | `test_transactions.py` |
| TX-21 | WF05 post, ERD §6 | Transactions never updated or deleted (no routes; FK RESTRICT) | ✅ | routes + migration |
| TX-22 | P§5.8 | Seller view hides buyer identity (anonymized ref) | ✅ | `test_transactions.py` |
| TX-23 | P§5.4 | Preview returns `quoted_at`, `quote_expires_at` and a signed `quote_token`; expired/tampered → `409 PRICE_CHANGED` | ✅ | `test_checkout.py` |
| TX-24 | P§5.5 | `Idempotency-Key` on confirm returns the original transaction on repeat | ✅ | `test_checkout.py` |
| TX-25 | DM 3 | Purchase weight must be `> 0` and `≤ available` | ✅ | `test_checkout.py` |

## F. Subscription & Mock Payment

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| SB-01 | API §6, WF07 §2 | `POST /api/subscription/subscribe` investor, via internal mock payment | ✅ | `test_subscription.py` |
| SB-02 | API §6, WF07 §2 | +30 days from current expiry if still active, else from now | ✅ | `test_subscription.py` |
| SB-03 | WF07 §2 | Sets `subscription_tier='premium'` | ✅ | `test_subscription.py` |
| SB-04 | WF07 prereq | Subscription does not require KYC | ✅ | `test_subscription.py` |
| SB-05 | API §6 | `GET /api/subscription/status` returns tier + expiry | ✅ | `test_subscription.py` |
| SB-06 | WF07 §4 | Daily job resets expired users to `free` | ✅ | `test_subscription.py::test_expiry_job` |
| SB-07 | WF07 edge | Payment failure → tier unchanged | ✅ | `test_subscription.py` |
| SB-08 | WF07 §1 | Monthly price is a server config value | ✅ | `core/config.py`, `GET /api/config` |
| PM-01 | API §7 | Mock payment is an internal service call, **not** a public route | ✅ | `modules/payments/service.py`; no `/api/payments/*` route |
| PM-02 | API §7 | Mock payment returns `status=success` + `payment_ref` (uuid) | ✅ | `payments/service.py` |
| PM-03 | API §7 note | Every mock payment audit-logged with context (promotion/subscription) + `payment_ref` | ✅ | `test_subscription.py` |

## G. Ownership

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| OW-01 | API §8, WF08-A | `GET /api/ownership/me` investor-only | ✅ | `test_ownership.py` |
| OW-02 | DM 1.4, WF05 §4.5 | Signature = HMAC-SHA256(secret, investor_id + grams + timestamp) with a canonical string | ✅ | `core/signature.py` |
| OW-03 | SD §5 | Secret from env only | ✅ | `core/config.py` |
| OW-04 | WF08-A §1 | Verified (constant-time compare) before returning | ✅ | `test_ownership.py` |
| OW-05 | DM 3, WF08-A | Mismatch → `403 INTEGRITY_CHECK_FAILED`, no balance in body, security audit entry | ✅ | `test_ownership.py::test_tampering_detected` |
| OW-06 | WF08 edge | New investor without record → `0` grams, no error | ✅ | `test_ownership.py` |
| OW-07 | DM 2 | One ownership record per investor (1:1, unique) | ✅ | migration |
| OW-08 | SD §6, P§7.6 | Disclaimer: signature is an internal integrity proof, not legal ownership | ✅ | response `disclaimer`, `API_CONTRACT.md` |

## H. Data Layer

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| DB-01 | SD §2 | PostgreSQL + SQLAlchemy 2.x + Alembic | ✅ | `alembic/`, `docker-compose.yml` |
| DB-02 | ERD §1-4 | UUID primary keys, `gen_random_uuid()` default | ✅ | migration |
| DB-03 | ERD | NUMERIC types: grams (10,3), price (14,2), amounts (16,2), rate (5,4), accumulated (12,3) | ✅ | migration |
| DB-04 | ERD note, P§4.2 | Python uses `Decimal`, never `float`, in money/weight paths | ✅ | `grep` in `AUDIT_REPORT.md` §8 |
| DB-05 | ERD | Enums: role, risk_profile, subscription_tier, listing status | ✅ | migration |
| DB-06 | ERD | CHECKs: total > 0, available ≥ 0, karat IN (18,21,22,24), purchased > 0 | ✅ | migration |
| DB-07 | ERD | Indexes: users email (unique), role, sub_expiry; listings seller, status, (promoted, expiry); tx investor, asset, created; ownership investor (unique) | ✅ | migration |
| DB-08 | ERD §6 | FK actions: listings→seller RESTRICT; tx→investor/asset RESTRICT; ownership→investor CASCADE | ✅ | migration |
| DB-09 | ERD, P§4.2 | TIMESTAMPTZ everywhere; `created_at`/`updated_at` defaults | ✅ | migration |
| DB-10 | P§4.2 | Migration history applies cleanly on an empty DB | ✅ | tests run `alembic upgrade head` on a fresh DB |

## I. Cross-cutting

| ID | Source | Requirement | Status | Evidence |
|---|---|---|---|---|
| CC-01 | API §9 | Error body `{error_code, message, status}` for every error incl. 401/403/404/405/409/422/429/500 | ✅ | `core/errors.py`, `test_errors.py` |
| CC-02 | API §9, P§4.5 | Documented codes + `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `EMAIL_ALREADY_EXISTS`, `RATE_LIMITED`, `AI_UNAVAILABLE`, `INTERNAL_ERROR`, `PRICE_CHANGED` | ✅ | `core/errors.py` |
| CC-03 | P§4.5 | Validation errors include `details` (field + message) | ✅ | `test_errors.py` |
| CC-04 | P§4.5 | Arabic messages, English codes; one exception module | ✅ | `core/errors.py` |
| CC-05 | P§4.4 | No stack traces / secrets in responses | ✅ | 500 handler |
| CC-06 | SD §5 | CORS restricted to frontend origins from env | ✅ | `main.py` |
| CC-07 | SD §5 | Secrets in `.env`, `.env.example` complete, `.env` git-ignored | ✅ | `.gitignore`, `.env.example` |
| CC-08 | SD §5, API §7 | Audit log: transactions, ownership updates, mock payments, integrity failures | ✅ | `audit_logs` table, tests |
| CC-09 | SD §2-3 | Modular monolith: router + service per module; logic in services | ✅ | `app/modules/*` |
| CC-10 | SD §2 | Background jobs via APScheduler (prices ~1 min, subscription expiry daily) | ✅ | `jobs/scheduler.py` |
| CC-11 | P§4.1 | Settings via `pydantic-settings` | ✅ | `core/config.py` |
| CC-12 | P§4.4 | Dependencies pinned | ✅ | `requirements.txt` |
| CC-13 | P§5.1 | snake_case JSON, UUID strings, ISO-8601 UTC datetimes, Decimal as string | ✅ | `API_CONTRACT.md` |
| CC-14 | P§5.2 | OpenAPI: tags per module, `response_model` on every route, error responses, exported `docs/api/openapi.json` | ✅ | `scripts/export_openapi.py` |
| CC-15 | P§5.9 | `GET /api/health` (DB + price-cache age) | ✅ | `test_market.py` |
| CC-16 | P§7.2 | Product name Sila (صِلة) in code, docs, OpenAPI title | ✅ | `main.py` |
| CC-17 | SD §5 | HTTPS on all endpoints | ⚠️ | TLS is terminated by the deployment's reverse proxy / hosting; the app does not serve TLS itself (see D-12) |
| CC-18 | SD §5 | Token storage: httpOnly cookie **or** secure storage | ⚠️ | Bearer tokens in the JSON body (the "secure storage" option); storage is a frontend decision (see D-11) |
| CC-19 | P§6 | `docker compose up` runs PostgreSQL + API with auto-migrations; seed script; README | ✅ | `docker-compose.yml`, `app/scripts/seed.py`, `README.md` |

**Totals:** 116 requirements · ✅ 114 · ⚠️ 2 · ❌ 0
