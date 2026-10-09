# Sila (صِلة) API Contract · for the React frontend

Machine-readable schema: [`openapi.json`](openapi.json) (regenerate with `python -m app.scripts.export_openapi`).
Generate TypeScript types from it, e.g. `npx openapi-typescript docs/api/openapi.json -o src/api/schema.d.ts`.
Interactive docs: `http://localhost:8010/docs` while the API runs.

## 1. Conventions

| Topic | Rule |
|---|---|
| Base URL | `http://localhost:8010` (dev). All paths start with `/api`. |
| JSON | `snake_case` keys. UUIDs are strings. |
| Dates | ISO-8601 **UTC**, e.g. `"2026-09-24T08:37:53.701952Z"`. |
| Money & weights | **Decimal strings**, never numbers: `"157060.58"`, `"84.250"`. Keep them as strings; parse only for display (for arithmetic, use a decimal library such as `decimal.js`, not `Number`). Requests accept strings or numbers; send strings. Grams have up to 3 decimals (always echoed with 3), IQD amounts 2, `commission_rate` 4 (`"0.0150"` = 1.5%). |
| Currency | IQD. `price_per_gram_usd` is informational. |
| Auth | `Authorization: Bearer <access_token>` on every protected call. |
| CORS | Allowed origins come from `CORS_ORIGINS` (default `http://localhost:5173`, `http://127.0.0.1:5173`). Allowed headers: `Authorization`, `Content-Type`, `Idempotency-Key`. |
| Pagination | `?limit=20&offset=0` (max 100) → `{ items, total, limit, offset }`. |

## 2. Errors (every non-2xx response)

```json
{ "error_code": "KYC_NOT_VERIFIED", "message": "يجب إكمال التوثيق قبل إتمام عملية الشراء", "status": 403 }
```
Validation errors add `details` for inline form messages:
```json
{ "error_code": "VALIDATION_ERROR", "message": "البيانات المدخلة غير صالحة", "status": 422,
  "details": [{ "field": "password", "message": "String should have at least 8 characters" }] }
```
Branch on `error_code`, and show `message` (Arabic) to the user.

| error_code | HTTP | When / what the UI should do |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Bad input; map `details[].field` to form fields |
| `UNAUTHORIZED` | 401 | Missing/expired access token → call `/api/auth/refresh`, retry once, else go to login |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password (same message for both) |
| `FORBIDDEN` | 403 | Wrong role or not the owner |
| `KYC_NOT_VERIFIED` | 403 | Show the "توقيعك" screen, verify, then **auto-retry** the original action (§5, §6) |
| `SUBSCRIPTION_REQUIRED` | 403 | Show the upgrade screen (`/api/subscription/subscribe`) |
| `INTEGRITY_CHECK_FAILED` | 403 | Balance could not be verified; show an error, never a number |
| `NOT_FOUND` | 404 | Unknown id (also returned for other people's transactions) |
| `METHOD_NOT_ALLOWED` | 405 | — |
| `EMAIL_ALREADY_EXISTS` | 409 | "هذا الإيميل مسجل مسبقاً" on the email field |
| `LISTING_NOT_ACTIVE` | 409 | Listing is sold out or suspended; send the user back to browsing |
| `INSUFFICIENT_AVAILABLE_WEIGHT` | 409 | Quantity changed; reload the listing, ask for a smaller amount |
| `PRICE_CHANGED` | 409 | Quote expired or does not match; **call preview again** and re-confirm |
| `INVALID_STATUS_TRANSITION` | 409 | A sold-out listing cannot change status |
| `PAYMENT_FAILED` | 402 | Mock payment declined (demo switch); nothing was changed |
| `RATE_LIMITED` | 429 | Too many attempts; wait `Retry-After` seconds |
| `AI_UNAVAILABLE` | 503 | Show "الخدمة غير متاحة مؤقتاً" and link to manual browsing |
| `PRICE_UNAVAILABLE` | 503 | No market price yet (first boot with no network) |
| `INTERNAL_ERROR` | 500 | Generic error; no details are ever leaked |

Rate limits (per IP): login 5/min, KYC 5/min, AI 20/min.

## 3. Auth flow

1. `POST /api/auth/signup` → `201` user profile (no tokens). Investors must send `risk_profile` (`low|medium|high`); sellers must not.
2. `POST /api/auth/login` → tokens + `user`:
   ```json
   { "access_token": "eyJ…", "refresh_token": "eyJ…", "token_type": "bearer", "expires_in": 900,
     "user": { "id": "9fb9…", "role": "investor", "full_name": "زينب الموسوي", "email": "zainab@sila.iq",
               "kyc_verified": true, "risk_profile": "medium", "subscription_tier": "premium",
               "subscription_expiry_date": "2026-10-15T08:36:54.426896Z", "is_premium_active": true,
               "created_at": "2026-09-24T08:36:55.279129Z" } }
   ```
3. The access token lives 15 min. On `401 UNAUTHORIZED`, `POST /api/auth/refresh {refresh_token}` returns a new pair (the refresh token lasts 7 days).
4. Route by `user.role`: `investor` → dashboard with prices; `seller` → "my listings".
5. Storage: keep the access token in memory and the refresh token in `sessionStorage`.
6. Gate Premium UI with `is_premium_active` (the date check), **not** `subscription_tier`.

## 4. Endpoints

Legend: 🌐 public · 👤 any logged-in user · 💰 investor · 🏪 seller.

### Identity & Security
| | Method & path | Body | Success |
|---|---|---|---|
| 🌐 | `POST /api/auth/signup` | `{role, full_name, email, password (8–72), risk_profile?}` | `201 UserOut` |
| 🌐 | `POST /api/auth/login` | `{email, password}` | `200 LoginOut` |
| 🌐 | `POST /api/auth/refresh` | `{refresh_token}` | `200 TokenOut` |
| 👤 | `GET /api/users/me` | — | `200 UserOut` |
| 💰 | `POST /api/kyc/verify` | — | `200 {kyc_verified: true, message, user}` |
| 🏪 | `POST /api/kyc/seller` | — | same |

### Market Data
| | Method & path | Notes |
|---|---|---|
| 🌐 | `GET /api/market/prices` | Served from cache; the job refreshes every ~60 s. Poll every 30–60 s. |
| 🌐 | `GET /api/market/prices/history?karat=24&range=1D` | `range` ∈ `1D,1W,1M,3M,1Y` → `{karat, range, currency, points:[{ts, price_per_gram}]}`. Buckets: 10 min / 1 h / 4 h / 12 h / 1 day. Use `1D` for 24h sparklines. |

`GET /api/market/prices` example:
```json
{ "base_currency": "IQD",
  "karats": [ {"karat": 24, "price_per_gram_iqd": "179497.80", "price_per_gram_usd": "137.18"},
              {"karat": 22, "price_per_gram_iqd": "164539.65", "price_per_gram_usd": "125.75"},
              {"karat": 21, "price_per_gram_iqd": "157060.58", "price_per_gram_usd": "120.04"},
              {"karat": 18, "price_per_gram_iqd": "134623.35", "price_per_gram_usd": "102.89"} ],
  "usd_iqd": "1308.4454", "xau_usd_per_ounce": "4266.8999", "change_24h_pct": "1.16",
  "updated_at": "2026-09-24T08:37:53.701952Z", "source": "live", "is_stale": false }
```
Show `updated_at` as "آخر تحديث". If `is_stale` is true, the provider is down and the price shown is the last cached one.

### Listing & Asset
| | Method & path | Notes |
|---|---|---|
| 🏪 | `POST /api/listings` | `{total_weight_grams, karat: 18|21|22|24}`. Price is computed by the server; any price you send is ignored. KYC required. Optional `Idempotency-Key` header: a replay returns `200` with the same listing. |
| 🌐 | `GET /api/listings` | Filters `karat`, `min_price`, `max_price` (on `base_price_per_gram`), `sort=promoted_first` (default) `|newest|price_asc|price_desc`, `limit`, `offset`. Active listings only. |
| 🏪 | `GET /api/listings?seller_id=me[&status=active|suspended|sold_out]` | The seller's own listings, all statuses |
| 🌐 | `GET /api/listings/{id}` | Any status |
| 🏪 | `PATCH /api/listings/{id}` | `{status: "active"|"suspended"}`, owner only. There is **no DELETE**. |
| 🏪 | `POST /api/listings/{id}/promote` | No body. The fee comes from `/api/config`. Returns `{listing, payment:{status, payment_ref, amount_iqd, purpose}}`. |

Listing card (`ListingOut`):
```json
{ "id": "514bc57c-…", "seller_id": "f23e…", "seller_name": "مجوهرات الكرّادة", "seller_kyc_verified": true,
  "karat": 21, "total_weight_grams": "84.250", "available_weight_grams": "71.750",
  "base_price_per_gram": "157115.79", "current_price_per_gram": "157060.58", "status": "active",
  "is_promoted": true, "promotion_expiry_date": "2026-09-29T08:36:54.426896Z",
  "created_at": "2026-09-12T08:36:54.426896Z", "updated_at": "2026-09-24T08:38:08.950993Z" }
```
`is_promoted` is already expiry-aware. Show `current_price_per_gram` as the buy price; `base_price_per_gram` is the reference price at creation.

### AI Engine (💰, rate-limited)
| Method & path | Body | Returns |
|---|---|---|
| `POST /api/ai/match` | `{budget_iqd}` | `{budget_iqd, risk_profile, engine, message, results:[{rank, listing, suggested_weight_grams, execution_price_per_gram, estimated_total_iqd, commission_rate, budget_usage_pct, score, reason}]}`. Empty `results` + `message` when the budget is too small. |
| `POST /api/ai/risk-analysis` | `{asset_id, weight_grams}` | `{level: low|medium|high, insight, signals[], engine, asset_id, purchased_weight_grams}` |
| `GET /api/ai/insights` | — | Premium only: `{engine, alerts[], market:{…trend…}, portfolio:{total_grams, total_paid_iqd, current_value_iqd, unrealized_pnl_iqd, unrealized_pnl_pct, by_karat[]}}` |

| `POST /api/ai/advisor` | `{question (1–500 chars), budget_iqd?}` | `{engine, answer, show_figures, budget, holdings_grams, suggestions[], market_snapshot:{price_24k_per_gram, change_24h_pct, updated_at, is_stale}, follow_up_questions[], disclaimer}`. Free. See below. |

`engine` is `"rules"` (deterministic) or `"llm"` (text rewritten by Claude). The numbers are identical either way.

#### AI Advisor (`POST /api/ai/advisor`)
A free question in Arabic ("عندي مليونين، شنو أحسن شي أشتريه هسة؟") → a short Arabic `answer` plus offers the investor can buy.
- **`suggestions`** have the exact shape of `/api/ai/match` `results` (`listing` is a full `ListingOut`): send the user to checkout with them. They always come from the rule-based matcher, never from the model text.
- **`budget`** is `null` or `{amount_iqd, source, confirmed}`:
  | `source` | When | `confirmed` | `suggestions` |
  |---|---|---|---|
  | `request` | `budget_iqd` was sent (wins over the question) | `true` | yes |
  | `question_digits` | digits in the question: `2000000`, `2,000,000`, `٢٠٠٠٠٠٠` | `true` | yes |
  | `question_words` | words: `مليونين`, `نص مليون`, `3 ملايين`, `500 ألف`; or two different amounts | **`false`** | **empty**: show "فهمت ميزانيتك X، صح؟" and, on yes, resend the same question with `budget_iqd` |
  - `budget: null` → the answer covers the market only; offer quick budget choices and resend with `budget_iqd`.
- **`engine`**: `"llm"` when the configured model worded the answer, `"rules"` otherwise (no provider, any provider failure, or a reply rejected by the check: a number not in the data, an offer that does not exist, a profit promise, a non-Arabic reply). The UI may show that the answer is simplified.
- **`answer`** is plain Arabic text (no markdown, no dashes, the brand written صِلة). It does not repeat the live figures.
- **`show_figures`**: `true` when the question is about prices, money, the budget or holdings (always `true` with a budget). Show `market_snapshot`, the budget and `holdings_grams` in their own panel under the answer; hide it for unrelated questions.
- **`follow_up_questions`**: up to 3 short questions fitted to this question, budget state and risk profile. Offer them as the next quick questions. They pass the same check as the answer (a failing one is dropped); rule-based ones are returned when the model is not used.
- **`disclaimer`** is always present: always show it under the answer.
- Only anonymous data reaches the model (prices, risk profile, holdings in grams, budget, offers numbered 1–5 without ids or seller names). E-mails and phone numbers are removed from the question. Questions are independent: send no history.
- Errors: `403 FORBIDDEN` (not an investor), `422 VALIDATION_ERROR`, `429 RATE_LIMITED` (shares the AI limit), `503 AI_UNAVAILABLE` (no price data at all).
- Server config: `ADVISOR_PROVIDER` (`none|groq|gemini|anthropic`), `ADVISOR_API_KEY`, `ADVISOR_MODEL`, `ADVISOR_BASE_URL`, `ADVISOR_TIMEOUT_SECONDS`, `ADVISOR_MAX_OUTPUT_TOKENS` (see `.env.example`). The key never reaches the frontend.

### Order & Transaction
| | Method & path | Notes |
|---|---|---|
| 💰 | `POST /api/transactions/preview` | `{asset_id, purchased_weight_grams}` → breakdown + `quote_token` + `risk_insight`. Read-only; no KYC needed. |
| 💰 | `POST /api/transactions/confirm` | `{asset_id, purchased_weight_grams, quote_token}` + header `Idempotency-Key`. `201` new, `200` replay. |
| 👤 | `GET /api/transactions` | Investor: my purchases. Seller: sales on my listings (`buyer_ref` pseudonym, no buyer identity). |
| 👤 | `GET /api/transactions/{id}` | Parties only (others get 404) |

### Subscription (💰)
| Method & path | Returns |
|---|---|
| `POST /api/subscription/subscribe` | `{subscription_tier, subscription_expiry_date, is_active, days_remaining, price_iqd, duration_days, payment}`. No KYC. Renewing early extends from the current expiry. |
| `GET /api/subscription/status` | same without `payment` |

### Ownership (💰)
`GET /api/ownership/me`:
```json
{ "investor_id": "9fb9…", "total_accumulated_grams": "70.500", "verified": true,
  "digital_signature_token": "419e2e4e…", "updated_at": "2026-09-24T08:38:49.096381Z", "message": null,
  "disclaimer": "التوقيع الرقمي إثبات تقني داخلي لسلامة رصيدك داخل منصة صِلة، وليس سند ملكية قانونياً معترفاً به رسمياً." }
```
A new investor gets `"0.000"` with `message: "ابدأ أول استثمار"`. **Always show `disclaimer`** near the balance (the legal flag in System Design §6). The signature is an internal integrity proof, not legal ownership.

### System (🌐)
- `GET /api/health` → `{status: ok|degraded, database, price_cache_age_seconds, price_source, price_is_stale, time}`
- `GET /api/config` → fees, subscription price, commission tiers, `quote_ttl_seconds`. Display these values; never hard-code them. Each tier has `label` (English) and `label_ar` (Arabic display text, e.g. `أقل من 50 غ`).

## 5. Checkout sequence (the most important flow)

```
listing page ──► POST /preview {asset_id, purchased_weight_grams}
                    ◄── breakdown + quote_token + quote_expires_at (+ risk_insight)
confirmation screen (show principal, commission + rate, total, insight; count down to quote_expires_at)
user taps "تأكيد العملية"
   key = crypto.randomUUID()   ← create once per confirmation screen, reuse on every retry
   POST /confirm {asset_id, purchased_weight_grams, quote_token}  +  Idempotency-Key: key
      201 ──► success screen: transaction + ownership.total_accumulated_grams
      403 KYC_NOT_VERIFIED ──► "توقيعك" screen → POST /api/kyc/verify → resend the SAME confirm (same key)
      409 PRICE_CHANGED ──► quote expired/mismatch → call /preview again, show new numbers, confirm again (new key)
      409 INSUFFICIENT_AVAILABLE_WEIGHT / LISTING_NOT_ACTIVE ──► back to the listing
      network error / timeout ──► resend with the SAME key: you get the original transaction (200), never a double buy
```
- **Quote token:** a signed quote (price, grams, listing, investor, expiry). The transaction is executed at the **quoted** price, so what the user saw is what they pay. It expires after `quote_ttl_seconds` (60 s); refresh the preview when the countdown ends. The token is bound to the user and the exact grams: changing the quantity requires a new preview.
- If `risk_insight` is `null`, show `risk_insight_note` ("التحليل الذكي غير متاح حالياً"). It never blocks checkout. Call `/api/ai/risk-analysis` in parallel if you want the LLM-worded version.
- Preview reserves nothing. Only `confirm` changes stock.

## 6. Seller publish with the KYC interrupt

```
POST /api/listings {total_weight_grams, karat}  + Idempotency-Key: key
   403 KYC_NOT_VERIFIED ──► "توقيعك" screen → POST /api/kyc/seller → automatically resend the SAME request (same key)
   201 ──► listing created (a replay with the same key returns 200 and the same listing)
```

## 7. Demo accounts (after `python -m app.scripts.seed`)

Password for all: `Sila@2026`

| Email | Role | State |
|---|---|---|
| `zainab@sila.iq` | investor | KYC ✓, medium risk, Premium active, has holdings |
| `haider@sila.iq` | investor | KYC ✓, high risk, free, large holdings |
| `ali@sila.iq` | investor | **not KYC-verified** (demo the KYC interrupt) |
| `sara@sila.iq` | investor | Premium **expired** (demo the upgrade flow) |
| `karrada@sila.iq` | seller | مجوهرات الكرّادة, KYC ✓, promoted listing |
| `nahr@sila.iq` | seller | صاغة شارع النهر, KYC ✓, one sold-out and one suspended listing |
| `mansour@sila.iq` | seller | ذهب المنصور, **not KYC-verified** (demo the publish interrupt) |
