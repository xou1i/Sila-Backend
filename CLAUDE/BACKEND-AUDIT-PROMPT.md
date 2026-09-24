# Sila (صِلة) Backend · Audit, Align & Harden · Prompt for Claude Code

> Copy this whole file as your first message to Claude Code, from the root of the FastAPI backend repository. Attach (or place next to the repo) the `Product/` folder.

---

## 0. Context & Mission

You are a senior backend engineer taking over the **FastAPI backend of Sila (صِلة)**: a fintech gold marketplace for the Iraqi market (live gold/USD prices, AI smart matching, fractional gold purchase with tiered commission, mock KYC, signed ownership records, premium subscription, promoted listings).

I did not write this backend. Your job:
1. Understand the **specification** in `Product/` completely.
2. Audit the **whole existing codebase** against it.
3. Write a clear plan, then **execute it**: fix, complete and harden the backend so that it matches the spec 100%, runs with one command, has zero known errors, and is ready for a React (Vite) frontend to connect to.

Context that shapes every decision:
- This is a **hackathon project** (4-day MVP). I need it **correct, complete and demo-safe**, not enterprise-scale. Do not over-engineer, do not add microservices, queues or infrastructure the spec does not ask for.
- Everything written in `Product/` **must exist and work**. Security holes, bugs and small missing pieces that can be fixed quickly must be fixed.
- Keep what already works. Refactor only when it fixes a bug, a spec mismatch, or blocks the frontend. No rewrite for taste.

---

## 1. Phase 1 · Read the Specification (do this first, fully, before touching code)

Read every file in this order. Do not skim.

| # | File | What it defines |
|---|---|---|
| 1 | `Product/01_product_vision.md` | Product, audience, in-scope / out-of-scope |
| 2 | `Product/02_domain_model.md` | Entities, relationships, business rules, karat formula, commission tiers |
| 3 | `Product/03_business_capabilities.md` | Capability map |
| 4 | `Product/04_system_design.md` | Architecture (modular monolith), stack, modules, concurrency rule, **security checklist** |
| 5 | `Product/05_schema.dbml` | Schema (DBML) |
| 6 | `Product/06_erd_database_design.md` | Exact column types (NUMERIC), constraints, indexes, referential actions |
| 7 | `Product/07_api_design.md` | Every endpoint, auth rule, error format and error codes |
| 8 | `Product/Workflow/00-index.md` … `08-ownership-portfolio.md` | Step-by-step behavior, server checks, edge cases, error responses |

Then write `docs/audit/SPEC_MATRIX.md`: a numbered list of **every testable requirement** extracted from those files (ID, source file + section, requirement in one line). Group by module: Identity & Security, Market Data, Listing & Asset, AI Engine, Order & Transaction, Subscription & Mock Payment, Ownership, Cross-cutting (errors, security, jobs, config). Expect roughly 80 to 120 requirements. This matrix is the checklist for everything that follows.

---

## 2. Phase 2 · Audit the Existing Codebase

Explore the entire repository before judging it: structure, dependencies, config, models, migrations, routers, services, schemas, jobs, tests, Docker files, `.env` handling.

Then actually **run it**: install dependencies, start PostgreSQL (Docker if available), run migrations, start the app, open `/docs`, run existing tests. Record every failure.

Produce `docs/audit/AUDIT_REPORT.md` with:
1. **Overview**: stack and versions found, project structure, how to run it today, overall maturity level (honest, one paragraph).
2. **Spec compliance table**: every requirement ID from `SPEC_MATRIX.md` → status `✅ done` / `⚠️ partial or wrong` / `❌ missing`, with file:line evidence and a one-line note.
3. **Bugs & runtime errors** found (with reproduction).
4. **Security findings** against the `04_system_design.md` checklist and the list in section 4 below, each with severity (Critical / High / Medium / Low).
5. **Data-layer findings**: types vs ERD (NUMERIC not FLOAT), constraints, indexes, FK `ON DELETE` actions, migrations state.
6. **Frontend-readiness gaps** (section 5 below).
7. **Test coverage** status.

Be factual. Evidence over opinion.

---

## 3. Phase 3 · Plan, Then Execute

Write `docs/audit/PLAN.md`: ordered tasks, each linked to requirement/finding IDs, with priority:
- **P0**: broken, insecure, or spec-violating money logic (checkout, commission, karat pricing, KYC gate, row locking, signature, RBAC). App must start and migrate cleanly.
- **P1**: missing endpoints/behaviors from the spec, error standard, background jobs, rate limiting, CORS, tests for core rules.
- **P2**: frontend-readiness additions (section 5), seed data, docs, polish.

Then **execute the plan in order without waiting for me**, committing in small logical commits (Conventional Commits, e.g. `fix(checkout): lock listing row with SELECT FOR UPDATE`). Keep `PLAN.md` updated with checkboxes as you go.

Stop and ask me **only** before: dropping or rewriting data/migrations that could lose data, replacing the ORM/framework, or changing a documented API path or field name.

---

## 4. What Must Be True When You Finish (derived from the spec)

Use this as a minimum. Your `SPEC_MATRIX.md` may find more; the spec files win over this summary.

### 4.1 Architecture & stack
- Modular monolith: one FastAPI app, a router + service layer per module (Identity & Security, Market Data, Listing & Asset, AI Engine, Order & Transaction, Subscription & Mock Payment, Ownership). Business logic lives in services, not in routers.
- PostgreSQL + SQLAlchemy 2.x + Alembic. Pydantic v2 schemas. JWT (access + refresh). Background jobs with APScheduler (keep Celery out unless it already exists and works).
- Settings via environment (`pydantic-settings`), `.env.example` complete, no secret in git.

### 4.2 Data layer (match `06_erd_database_design.md` exactly)
- All money and weights are `NUMERIC` with the documented precision (`NUMERIC(10,3)` grams, `NUMERIC(14,2)` price per gram, `NUMERIC(16,2)` amounts, `NUMERIC(5,4)` rate, `NUMERIC(12,3)` accumulated grams). Python side uses `Decimal`, never `float`, in any money or weight calculation.
- Enums: `role (investor, seller)`, `risk_profile (low, medium, high)`, `subscription_tier (free, premium)`, `status (active, sold_out, suspended)`.
- CHECK constraints: `total_weight_grams > 0`, `available_weight_grams >= 0`, `karat IN (18,21,22,24)`, `purchased_weight_grams > 0`.
- Indexes and unique constraints as documented; `fractional_ownership_records.investor_id` unique (1:1).
- FK actions: listings → seller `RESTRICT`; transactions → investor/asset `RESTRICT`; ownership → investor `CASCADE`.
- Timezone-aware timestamps everywhere (`TIMESTAMPTZ`, `datetime.now(timezone.utc)`).
- Alembic migration history applies cleanly on an empty database.

### 4.3 Business rules
- **Karat pricing**: `price(karat) = global_24k_price × karat / 24`, applied at listing creation (`base_price_per_gram`) and at execution (`execution_price_per_gram`). The seller cannot set a price.
- **Commission tiers** (boundaries explicit and tested): `< 50 g → 1.5%`, `50 to 200 g inclusive → 1.0%`, `> 200 g → 0.5%`. Commission is added on top and paid by the investor; seller receives full `principal_amount`.
  `principal = grams × price`, `commission = principal × rate`, `total = principal + commission`. Define and document one rounding rule (recommended: `ROUND_HALF_UP` to 2 decimals on principal, then commission, then total).
- **Checkout** (`05-checkout-purchase.md`): `preview` is read-only (no reservation, no writes). `confirm` in **one atomic DB transaction** with `SELECT ... FOR UPDATE` on the listing row: server-side KYC check first (`403 KYC_NOT_VERIFIED`), listing must be `active` (`LISTING_NOT_ACTIVE`), re-check weight (`INSUFFICIENT_AVAILABLE_WEIGHT`), decrement available weight, set `sold_out` at zero, insert transaction, upsert ownership record, regenerate signature, commit or full rollback.
- **Ownership signature**: `HMAC-SHA256(secret, investor_id + total_accumulated_grams + timestamp)` with the secret from env only. Use a **canonical string format** (fixed Decimal quantization, ISO-8601 UTC timestamp, explicit separator) so generation and verification always match. Verify with `hmac.compare_digest`. `GET /api/ownership/me` verifies before returning; mismatch → `403 INTEGRITY_CHECK_FAILED` + security audit log entry, and no balance in the response. New investor without record → `0` grams (no error).
- **KYC** (mock "توقيعك"): investor via `POST /api/kyc/verify`, seller via `POST /api/kyc/seller`; sets `kyc_verified = true` permanently; each endpoint only for its role. Gate enforced on the server at confirm and at listing creation.
- **Listings**: create (seller, KYC-gated), browse with filters `karat`, `min_price`, `max_price`, `sort=promoted_first` (promoted = `is_promoted AND promotion_expiry_date > now()`, then newest), detail, `PATCH` status by the owner only (`active` ↔ `suspended`; a `sold_out` listing cannot be re-activated), **no delete endpoint**. Seller's own listings view (`seller_id=me` or equivalent).
- **Promotion**: owner + KYC-verified seller; fee is a server config value (client cannot send it); internal mock payment; sets `is_promoted` and `promotion_expiry_date`.
- **Subscription**: `subscribe` via internal mock payment; +30 days from current expiry if still active, else from now; `subscription_tier = premium`. `GET /api/subscription/status`. `GET /api/ai/insights` checks `subscription_expiry_date > now()` (not the tier field) → else `403 SUBSCRIPTION_REQUIRED`. Daily job resets expired users to `free`.
- **Mock payment**: an internal service function, **not** a publicly callable route. If a `/api/payments/mock` route exists, remove it from the public router or protect it so the frontend cannot call it. Every mock payment is audit-logged with its context (promotion / subscription) and a `payment_ref`.
- **Market data**: background job refreshes gold (24K reference) and USD/IQD roughly every minute into a cache; `GET /api/market/prices` is public and **never calls the external API per request**; returns all four karats, USD/IQD, and the last update timestamp. On external failure: serve the last cached value with its timestamp (never error the endpoint because the provider is down).
- **AI**: `POST /api/ai/match` (free, investor, `budget_iqd`, risk profile read from the account server-side, only `active` listings, ranked results each with a short reason), `POST /api/ai/risk-analysis` (short insight, never blocks checkout), `GET /api/ai/insights` (premium). If the AI provider key is missing or the call fails, fall back to a **deterministic rule-based engine** so the demo always works; budget below cheapest listing → empty results with a clear message; provider failure → documented error the frontend can show.
- **Transactions history**: `GET /api/transactions` (investor: own purchases; seller: sales on own listings via `asset → seller`), `GET /api/transactions/{id}` (only a party to it). Transactions are never updated or deleted.

### 4.4 Security (from `04_system_design.md` + must-haves)
- Bcrypt password hashing (use `bcrypt` or `pwdlib` directly; avoid unmaintained `passlib` warnings). Password min 8 chars. Email normalized (lowercase, trimmed); duplicate → `409`.
- JWT: short-lived access token, refresh token with a distinct `type` claim; secret and TTLs from env; reject wrong token type; `401` on invalid/expired.
- **RBAC** dependency (`require_role("investor")` / `"seller"`) on every role-specific endpoint; ownership checks on listing PATCH/promote and transaction detail.
- Rate limiting (e.g. `slowapi`) on `/api/auth/login`, `/api/kyc/*`, `/api/ai/*`, with a `429 RATE_LIMITED` response in the standard error format.
- CORS restricted to the frontend origin(s) from env (localhost Vite port for dev).
- No stack traces or secrets in responses; generic login error message; no user enumeration.
- Audit logging for transactions, ownership updates, mock payments, integrity failures (a structured logger or a small `audit_logs` table; choose the simplest that satisfies the spec).
- Dependencies pinned; no secrets committed; `.env` in `.gitignore`.

### 4.5 Error standard (`07_api_design.md` section 9)
Every error, including FastAPI's own validation errors, 401/403/404/405/409/429/500, returns:
```json
{ "error_code": "KYC_NOT_VERIFIED", "message": "يجب إكمال التوثيق قبل إتمام عملية الشراء", "status": 403 }
```
- Documented codes: `INSUFFICIENT_AVAILABLE_WEIGHT`, `KYC_NOT_VERIFIED`, `SUBSCRIPTION_REQUIRED`, `LISTING_NOT_ACTIVE`, `INTEGRITY_CHECK_FAILED`, `VALIDATION_ERROR`.
- Add (same format) where needed: `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `EMAIL_ALREADY_EXISTS`, `RATE_LIMITED`, `AI_UNAVAILABLE`, `INTERNAL_ERROR`. Validation errors may include a `details` array (field + message) for inline form errors.
- Messages in Arabic (the product language), codes in English. One exception handler module; no ad-hoc `HTTPException(detail=...)` strings scattered in routers.

---

## 5. Frontend-Readiness (the React app will be built from a detailed UI Kit)

The frontend needs these. Add them as **additive, non-breaking** changes (no renamed documented fields):

1. **Consistent contract**: `snake_case` JSON, UUIDs as strings, ISO-8601 UTC datetimes. Decide one representation for `Decimal` in JSON (recommended: string, which is Pydantic v2's default, to preserve precision) and document it.
2. **OpenAPI quality**: tags per module, `response_model` on every route, documented error responses, request/response examples. Export `openapi.json` to `docs/api/` so the frontend can generate TypeScript types.
3. **Price history for charts** (the UI shows a live gold chart with ranges 1D/1W/1M/3M/1Y and 24h sparklines, but the spec has no history endpoint): the price job also stores a snapshot in a small `price_snapshots` table; add `GET /api/market/prices/history?karat=24&range=1D` returning `[{ts, price_per_gram}]` (downsampled per range). Also include `change_24h_pct` and `updated_at` in `/api/market/prices`.
4. **Price quote freshness**: `preview` returns `quoted_at` and a `quote_expires_at` (e.g. 60s). `confirm` accepts the preview's values safely: recommended a short **signed quote token** (HMAC over asset_id, grams, price, expiry) returned by preview and sent back to confirm; expired or tampered → `409 PRICE_CHANGED` so the UI asks the user to refresh the preview. This keeps "what the user saw = what they pay" (workflow 05 says the transaction stores the values computed at preview).
5. **Idempotency on confirm**: accept an `Idempotency-Key` header; a repeated key from the same user returns the original transaction instead of buying twice.
6. `GET /api/users/me` returns everything the UI needs: role, full_name, email, kyc_verified, risk_profile, subscription_tier, subscription_expiry_date.
7. Listing responses include what cards display: karat, total/available grams, base price, status, is_promoted (effective, expiry-aware), promotion_expiry_date, seller display name (or anonymized id), seller kyc flag, created_at. Pagination (`limit`/`offset` or cursor) with total count.
8. Transaction responses include all computed fields; seller view hides buyer identity (anonymized).
9. `GET /api/health` (DB + price cache age) for the demo.

---

## 6. Quality Gates (Definition of Done)

- `docker compose up` starts **PostgreSQL + API** (migrations auto-applied), and the README has the 3-command local run path without Docker as well.
- **Seed script** (`python -m app.scripts.seed` or equivalent): realistic Iraqi demo data (sellers like `مجوهرات الكرّادة`, `صاغة شارع النهر`; investors like `زينب الموسوي`), listings in all karats with organic weights (e.g. `84.250 g`), a verified and an unverified investor, a promoted listing, a premium subscriber, a few transactions with valid signatures. Demo credentials printed in the README.
- **Tests (pytest + httpx AsyncClient, real PostgreSQL via Docker or testcontainers)** covering at least:
  - karat conversion and commission tier boundaries (49.999 / 50 / 200 / 200.001 g), rounding;
  - preview has no side effects;
  - confirm happy path; KYC gate (investor and seller); `LISTING_NOT_ACTIVE`; `INSUFFICIENT_AVAILABLE_WEIGHT`; auto `sold_out`;
  - **concurrency**: two simultaneous confirms on the same listing never oversell (run truly concurrently against Postgres);
  - signature valid after purchases; tampering `total_accumulated_grams` in DB → `INTEGRITY_CHECK_FAILED`;
  - subscription extension from now vs from current expiry; insights gate by date; expiry job;
  - RBAC (investor cannot create listings, seller cannot buy, non-owner cannot PATCH/promote);
  - error format for validation, 401, 404, 409, 429;
  - promoted-first sorting honors expiry.
- `ruff check` and `ruff format --check` clean; app starts with zero warnings; no `float` in money paths (`grep` proves it).
- `docs/api/API_CONTRACT.md`: for the frontend, every endpoint with request/response example, auth, and error codes (generated from OpenAPI + hand-written notes on flows: checkout sequence, KYC interrupt, quote token, idempotency).
- `docs/audit/SPEC_MATRIX.md` all rows `✅`, or `⚠️` with an explicit, accepted reason.

---

## 7. Known Inconsistencies in the Docs (resolve and record)

Record your decision for each in `docs/audit/DECISIONS.md`:
1. `02_domain_model.md` says `Float`; `06_erd_database_design.md` says `NUMERIC`. **Use NUMERIC / Decimal** (the ERD explains why).
2. `Workflow/00-index.md` is titled "SmartBridge" (old name). The product is **Sila (صِلة)**; use Sila in code, docs and OpenAPI title.
3. `POST /api/ai/risk-analysis` input is named `weight_grams` in the API doc and `purchased_weight_grams` in checkout. Accept the documented name; keep naming consistent in responses.
4. Seller after KYC: workflow 02 says the seller presses "publish" again; workflow 06 says publishing continues automatically. The backend only needs to make the retry idempotent-safe and return the created listing; document the expected frontend behavior (auto-retry after KYC success).
5. Where `07_api_design.md` does not list an endpoint that a workflow needs (e.g. seller's own listings, `GET /api/subscription/status`, `GET /api/transactions/{id}`), implement it following the documented conventions.
6. `04_system_design.md` legal flag: the digital signature is an internal integrity proof, not legal ownership. Add a short note in the API contract so the frontend can show that disclaimer.

---

## 8. Working Rules

- Work on a new branch (`chore/audit-and-hardening`). Small commits. Never force-push.
- Prefer the simplest correct solution; standard library and existing dependencies first. Every new dependency must be justified in `DECISIONS.md`.
- Do not change documented endpoint paths or field names. Additions only, unless a documented behavior is impossible (then ask).
- Do not invent library APIs: check installed versions and their docs (FastAPI, SQLAlchemy 2.x async/sync as used by the repo, Pydantic v2, Alembic, APScheduler, slowapi).
- After each P0/P1 group: run migrations on a fresh DB, run the full test suite, start the app, hit the affected endpoints.

---

## 9. Final Report (your last message)

Reply with:
1. What the backend looked like when you started (3 to 5 lines).
2. What you changed, grouped by P0 / P1 / P2, with commit references.
3. Spec compliance: counts of ✅ / ⚠️ / ❌ from `SPEC_MATRIX.md`, and every remaining ⚠️ with its reason.
4. Security findings fixed and any left (with severity).
5. How to run: docker, local, seed, tests, demo credentials.
6. What the frontend developer needs to know (link to `API_CONTRACT.md`, auth flow, error format, Decimal format, quote token, idempotency, price history).
7. Suggested next steps that were out of scope.
