# Sila (صِلة) Backend · Decisions

## Documentation inconsistencies (audit prompt §7)

**D-01 · Float vs NUMERIC.** `02_domain_model.md` says `Float`; `06_erd_database_design.md` says `NUMERIC`. **Decision: NUMERIC in the DB, `Decimal` in Python**, with the ERD's precisions. Floats cause rounding errors on money and gold weight (the ERD states this reason).

**D-02 · Product name.** `Workflow/00-index.md` says "SmartBridge", and the old code used "SmartBridge API - Trident Wealth". **Decision: "Sila (صِلة)"** everywhere: OpenAPI title, docs, container names. The default DB name is `sila`.

**D-03 · `weight_grams` vs `purchased_weight_grams`.** `POST /api/ai/risk-analysis` accepts the documented `weight_grams`. Checkout endpoints use `purchased_weight_grams`. Responses always use `purchased_weight_grams` when describing a purchase.

**D-04 · Seller publish after KYC.** Workflow 02 says the seller presses "publish" again; workflow 06 says publishing resumes automatically. The backend creates nothing when it returns `403 KYC_NOT_VERIFIED`, so a retry is safe. `POST /api/listings` also accepts an optional `Idempotency-Key` header, so a double click or auto-retry never creates two listings. **Expected frontend behavior:** after `POST /api/kyc/seller` succeeds, automatically resend the same `POST /api/listings` body with the same `Idempotency-Key`.

**D-05 · Endpoints required by workflows but not listed in 07.** Implemented following the documented conventions:
- Seller's own listings: `GET /api/listings?seller_id=me` (seller auth, all statuses).
- `GET /api/subscription/status`, `GET /api/transactions/{id}` (listed in text, implemented).
- Additive: `GET /api/market/prices/history`, `GET /api/health`, `GET /api/config` (public server config values the UI must display: promotion fee, subscription price, commission tiers).

**D-06 · Legal flag.** `GET /api/ownership/me` returns a `disclaimer` string (Arabic) stating that the digital signature is an internal integrity proof, not a legal title. The note is repeated in `docs/api/API_CONTRACT.md`.

## Design decisions

**D-07 · Rounding rule.** All money uses `ROUND_HALF_UP` to 2 decimals, applied in this order:
`execution_price = round2(price24 × karat / 24)` → `principal = round2(grams × execution_price)` → `commission = round2(principal × rate)` → `total = principal + commission` (exact, no further rounding).
Grams are validated to at most 3 decimals and are never rounded silently. `price24` (IQD per gram) = `round2(XAU_USD_per_oz / 31.1034768 × USD_IQD)`.

**D-08 · Commission boundaries.** `grams < 50 → 0.0150`; `50 ≤ grams ≤ 200 → 0.0100`; `grams > 200 → 0.0050`. Both 50 and 200 are inclusive in the middle tier, following "50 - 200 غرام".

**D-09 · Stack kept; engine replaced.** FastAPI and SQLAlchemy are kept as found (sync ORM, `def` routes run in the threadpool). SQLite is replaced by PostgreSQL 16, as the spec mandates. The old SQLite files are left on disk untouched; they contained only demo seed data with plaintext passwords. psycopg 3 is the driver.

**D-10 · Price cache = latest `price_snapshots` row.** The background job (every `PRICE_REFRESH_SECONDS`, default 60) fetches the provider and inserts a snapshot. Every reader (prices, listing creation, preview, AI) reads the **latest snapshot from the DB**, never the provider. The cache therefore survives restarts and multiple workers, and it doubles as the chart history. On provider failure the job logs a warning and inserts nothing; readers keep serving the last snapshot with its real `updated_at`, and `is_stale=true` once it is older than `PRICE_STALE_SECONDS`. If the DB has no snapshot at all (first boot, offline), the job stores one from the configured fallback values (`source="fallback"`) so the demo always works.
Providers (free, keyless): gold `https://api.gold-api.com/price/XAU` (USD/oz), FX `https://open.er-api.com/v6/latest/USD` (IQD). Both URLs are configurable.

**D-11 · Token transport.** Access and refresh tokens are returned in the JSON body and sent as `Authorization: Bearer`, per `07_api_design.md`. The security checklist allows "httpOnly cookie **or** secure storage". The recommendation for the frontend is to keep the access token in memory and the refresh token in `sessionStorage`. The refresh token is stateless (no server-side revocation list); that is acceptable for the MVP.

**D-12 · HTTPS.** The app is served behind a TLS-terminating proxy or host in any real deployment; uvicorn serves plain HTTP for local dev. This is not enforced in code.

**D-13 · Rate limiting without a new dependency.** A ~40-line in-memory fixed-window limiter (`app/core/rate_limit.py`) is used as a FastAPI dependency instead of `slowapi`. `slowapi` is unmaintained relative to Starlette 1.x, and a single-process MVP does not need Redis. Limits are configurable: login `5/min`, KYC `5/min`, AI `20/min` per client IP. Trade-off: counters are per process and reset on restart.

**D-14 · Audit log = `audit_logs` table.** Rows carry `event_type`, `actor_id`, `entity_type`, `entity_id`, JSONB `data` and `created_at`. It covers transactions, ownership updates, mock payments and integrity failures. Transaction and ownership entries are written inside the same DB transaction as the change. Integrity failures are committed on their own so they persist even though the request fails.

**D-15 · Signature canonical form.** `message = f"{investor_id}|{grams:.3f}|{updated_at ISO-8601 UTC, microseconds, 'Z'}"`, `HMAC-SHA256(OWNERSHIP_SIGNING_SECRET)`, hex. `updated_at` on the ownership row is written explicitly by the service (no DB `onupdate`), so the signed timestamp and the stored timestamp are the same value. Verification recomputes the signature and uses `hmac.compare_digest`.

**D-16 · Quote token.** Preview returns `quote_token = base64url(payload).base64url(HMAC)`, where the payload is the JSON of `investor_id`, `asset_id`, `grams`, `price` (execution price per gram) and `exp` (expiry, Unix seconds). The commission rate is not in the token; it is re-derived from the grams. It is signed with `QUOTE_SIGNING_SECRET` and has a TTL of `QUOTE_TTL_SECONDS` (default 60). `confirm` requires the token and the same `asset_id`/`purchased_weight_grams`. The token is bound to the investor, so it cannot be replayed by another user. On expiry, tampering or mismatch it returns `409 PRICE_CHANGED`, and the UI re-runs preview. The transaction stores the **quoted** price (Workflow 05 §4.4, "القيم المحسوبة بالخطوة 2").

**D-17 · Idempotency.** `transactions.idempotency_key` and `asset_listings.idempotency_key` are nullable columns with a unique index per user. The key is checked **after** the listing row lock is taken, so two concurrent requests with the same key serialize, and the second one returns the first transaction (`200` with the same body). These are additive columns beyond the ERD.

**D-18 · Listing visibility.** `GET /api/listings` shows only `active` listings unless `seller_id=me` is passed by the owning seller, who then sees all statuses. `GET /api/listings/{id}` returns any status (the status is shown on the page; buying a non-active one fails with `LISTING_NOT_ACTIVE`). `min_price`/`max_price` filter on `base_price_per_gram`, the documented field. Cards also carry `current_price_per_gram` (live karat price).

**D-19 · Seller name, buyer anonymized.** Listing cards show the seller's `full_name` (sellers are shops, e.g. «مجوهرات الكرّادة») and `seller_kyc_verified`. The seller's transaction view replaces the investor with `buyer_ref`, a stable pseudonym (`"مستثمر #" + first 6 hex chars of HMAC(secret, investor_id)`).

**D-20 · Transaction detail for non-parties → 404.** This avoids revealing that a transaction ID exists. Listing PATCH/promote by a non-owner → `403 FORBIDDEN`, per Workflow 06.

**D-21 · AI engine.** The rule-based engine is always computed and is the source of truth for ranking. If `AI_API_KEY` is set, Claude (`AI_MODEL`, default `claude-opus-5`, effort `low`, server-side refusal fallbacks enabled, 6-second timeout, no retries) is asked only to rewrite the Arabic reason/insight text. If that call fails, times out or is refused, the rule-based text is returned unchanged and the response reports `engine: "rules"`. `503 AI_UNAVAILABLE` is returned only when no price data exists at all, so nothing can be computed.
Matching: for each active listing it computes the maximum affordable grams (the commission tier depends on the grams, so each tier is solved and checked against its range), capped by the available weight. Candidates below `AI_MATCH_MIN_GRAMS` (default 1 g) are dropped. Candidates are scored by budget usage and a karat preference per `risk_profile` (low → 24/22K, medium → 22/21K, high → 21/18K). The top 5 are returned. If nothing qualifies, the response has an empty list and an Arabic message.

**D-22 · Promotion extension.** Promoting an already-promoted listing extends from the current `promotion_expiry_date`, the same rule as subscriptions. Only `active` listings can be promoted (`LISTING_NOT_ACTIVE`). The fee (`PROMOTION_FEE_IQD`, default 25,000) and duration (`PROMOTION_DURATION_DAYS`, default 7) come from config.

**D-23 · Mock payment failure switch.** `MOCK_PAYMENT_FAIL=true` makes the internal payment raise `402 PAYMENT_FAILED`, to demo and test the "payment failed" edge cases in Workflows 06/07. It is off by default.

**D-24 · Validation status code.** Validation errors keep FastAPI's `422` with `error_code=VALIDATION_ERROR` and a `details: [{field, message}]` array.

**D-25 · New dependencies** (the prompt requires each to be justified). The old repo declared none, so every package is new: `psycopg[binary]` (PostgreSQL driver), `alembic` (migrations, mandated), `pydantic-settings` (config, mandated), `email-validator` (needed by `EmailStr`), `bcrypt` (hashing; `passlib` avoided), `PyJWT` (JWT), `APScheduler` 3.x (jobs, mandated). `anthropic` (official Claude SDK for the optional AI wording layer; the Claude API guidance prefers the SDK over raw HTTP in Python projects. It is used only when `AI_API_KEY` is set). Dev only: `pytest`, `ruff`, and `httpx2`, which Starlette 1.x's test client and our `AsyncClient` tests require. The market-price providers are called with the standard library (`urllib`, parsing numbers straight to `Decimal`).

**D-26 · Host ports.** On the audit machine, ports 5432 and 8000 were already taken by other software. Compose therefore publishes PostgreSQL on **5433**, and the API on `${API_PORT:-8000}`.

**D-27 · Preview risk insight.** `POST /api/transactions/preview` embeds the instant rule-based insight (no network call), so preview stays fast. `POST /api/ai/risk-analysis` returns the same analysis, optionally worded by Claude. The frontend may call both in parallel.

**D-28 · Canonical grams.** Every weight input is quantized to 3 decimals at validation (`"40"` → `"40.000"`). Quote tokens, DB rows and responses therefore always use the same representation.
