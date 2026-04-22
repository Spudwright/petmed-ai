# crittr.ai — Phase 6 + 7 deploy notes

Thirteen new modules across two phases. Each one is its own file, standalone,
with a smoke test run against it. This doc is the wire-up checklist for `app.py`,
the cron entries, and the env vars. Land in the order listed and the site keeps
working at every step.

---

## Phase 6 — AI operations automation

### 6.1 `admin_dashboard.py` — verdict QA dashboard

Wire-up (in `app.py`, after the app/db init):

```python
from admin_dashboard import register_admin_dashboard
register_admin_dashboard(app, q)
```

Env: `ADMIN_USER`, `ADMIN_PASS`. If either is unset, the dashboard endpoints
fail closed with 404.

Routes: `GET /admin/verdicts`, `GET /admin/verdicts.json`.

### 6.2 `events.py` — funnel & product telemetry

```python
from events import register_event_routes
register_event_routes(app, q)
```

Env: `ADMIN_USER` / `ADMIN_PASS` (shared with 6.1).

Routes: `POST /api/events` (fire-and-forget 204), `GET /admin/funnel`.

Note: the `events` table is auto-created on first call. No separate migration.

### 6.3 `partner_recon.py` — affiliate statement reconciler

Not wired into Flask. Run as a CLI against a partner CSV:

```bash
python partner_recon.py --partner vetster --statement statements/vetster-2026-04.csv --json out.json
```

Cron (monthly, first Monday, 9am):

```cron
0 9 1-7 * 1 cd /opt/crittr && python partner_recon.py --partner vetster --statement /var/crittr/statements/latest.csv
```

### 6.4 `alerts.py` — health checks + fallback observer

Two install steps.

First, wire the LLM fallback observer at app boot:

```python
from alerts import record_fallback
from llm_client import set_fallback_observer
set_fallback_observer(lambda provider, stage, err: record_fallback(q, provider, stage, err))
```

Then cron:

```cron
*/15 * * * * cd /opt/crittr && python alerts.py
```

Env: `SLACK_ALERT_WEBHOOK`, `ALERT_EMAIL` (either or both; if neither, alerts
just print).

### 6.5 `llm_client.py` — retries + Anthropic→OpenAI fallback

No route wire-up. New env vars:

- `LLM_RETRIES` (default `1`)
- `LLM_RETRY_BASE_MS` (default `250`)

The `generate_chat_reply` / `generate_summary` public API is unchanged.
Fallback observer hook is registered by 6.4 above.

### 6.6 `nightly_jobs.py` — stale summaries + summary size cap

Cron (nightly, 3am):

```cron
0 3 * * * cd /opt/crittr && python nightly_jobs.py
```

Env: `SUMMARY_MAX_CHARS` (default `6000`).

### 6.7 `triage_qa.py` — LLM reviewer for verdict calibration

Weekly cron:

```cron
0 10 * * 1 cd /opt/crittr && python triage_qa.py --n 20 --report /var/crittr/qa/weekly.md --json /var/crittr/qa/weekly.json
```

Or ad-hoc:

```bash
python triage_qa.py --n 20 --report qa.md
```

No env vars beyond the existing Anthropic / OpenAI keys.

---

## Phase 7 — Content, viral, regional

### 7.1 `seo_landings.py` — /c/&lt;slug&gt; landing pages + sitemap

```python
from seo_landings import register_seo_landings
register_seo_landings(app)
```

Routes: `GET /c/<slug>` for each seeded topic, `GET /sitemap.xml`.

Seed topics already in the module: dog-ate-grapes, dog-ate-chocolate,
dog-ate-xylitol, cat-throwing-up-foam, dog-limping-after-walk,
puppy-not-eating, cat-not-eating.

To add more, append to the `TOPICS` list at the bottom of the module.

### 7.2 `index-v2.html` — Open Graph + Twitter Cards + JSON-LD

No wire-up — the file is served as before. Additions are confined to the
`<head>`:

- Open Graph tags (og:type, og:site_name, og:title, og:description, og:url, og:image)
- Twitter summary_large_image card
- `<link rel="canonical">`
- Two `<script type="application/ld+json">` blocks — WebSite with SearchAction
  (target `/c/{search_term_string}`), and Organization.

The Organization schema's `sameAs` array is currently empty. Fill it in when
the social handles are live.

### 7.3 `weekly_digest.py` — Monday email

Cron (Monday 7am local):

```cron
0 7 * * 1 cd /opt/crittr && python weekly_digest.py
```

The module depends on `emails.send_email` and `products.get_top_picks` — both
already exist. Idempotency is a 6-day dedupe in `email_sends`.

Opt-out: honored via `users.digest_opt_in IS NOT FALSE`.

### 7.4 `find_vet.py` — Google Places "vets near me"

```python
from find_vet import register_find_vet_routes
register_find_vet_routes(app)
```

Env: `GOOGLE_PLACES_API_KEY` (required — without it, the endpoint returns an
empty list, which is fine degraded behavior).
Optional: `FIND_VET_CACHE_TTL` (seconds, default `600`).

Route: `POST /api/vets/nearby` with `{lat, lng, radius_km?}`.

Frontend: call `navigator.geolocation.getCurrentPosition` after a VET TOMORROW
verdict, POST the coords, render the returned list below the partner CTA.

### 7.5 `referrals.py` — referral codes, `/r/CODE`, credit ledger

```python
from referrals import register_referral_routes
register_referral_routes(app, q)
```

Env: `REFERRER_CREDIT_CENTS` (default `500`), `REFEREE_CREDIT_CENTS`
(default `500`), `BASE_URL` (used to render share links).

Routes: `GET /r/<code>` (redirect + cookie), `GET /api/referrals/me`,
`POST /api/referrals/redeem`.

Signup-flow integration: after `session['user_id']` is set in the signup
route, call `redeem_referral(q, request.cookies.get('crittr_ref'), uid)` and
clear the cookie. That's the only extra line required in existing signup code.

### 7.6 `VIDEO-SCRIPTS.md` — short-form video script pack

Not code. Ten 30-second scripts. Hand to whoever's shooting.

### 7.7 `regions.py` — country-aware poison hotline + legal text

```python
from regions import register_region_middleware, render_region_footer
register_region_middleware(app)
```

This adds a `before_request` that sets `g.region` and `g.region_config`. Use
in any template or route:

```python
from flask import g
footer = render_region_footer(g.region)  # HTML string
```

Inference priority: `?region=` query string → `crittr_region` cookie →
`CF-IPCountry` / `X-Country-Code` / `X-Vercel-IP-Country` header
(with GB → UK mapping) → `Accept-Language` → default US.

Regions configured: US, CA, UK, AU, plus a FALLBACK. NZ currently folds into
AU until we have dedicated AU-vs-NZ partners.

No env vars.

---

## Minimum `app.py` diff

Below is the set of new imports and registrations, grouped so the order is
safe. Put this block after the existing `app = Flask(__name__)` and `q = ...`
init, and before the first existing `register_*` call:

```python
# ---- Phase 6 ----
from admin_dashboard import register_admin_dashboard
from events import register_event_routes
from alerts import record_fallback
from llm_client import set_fallback_observer

register_admin_dashboard(app, q)
register_event_routes(app, q)
set_fallback_observer(lambda p, s, e: record_fallback(q, p, s, e))

# ---- Phase 7 ----
from seo_landings import register_seo_landings
from find_vet import register_find_vet_routes
from referrals import register_referral_routes
from regions import register_region_middleware

register_region_middleware(app)           # earliest — sets g.region for everything else
register_seo_landings(app)
register_find_vet_routes(app)
register_referral_routes(app, q)
```

Cron entries to add (one file, `/etc/cron.d/crittr`):

```cron
*/15 * * * *  www-data  cd /opt/crittr && python alerts.py
0 3 * * *      www-data  cd /opt/crittr && python nightly_jobs.py
0 7 * * 1      www-data  cd /opt/crittr && python weekly_digest.py
0 10 * * 1     www-data  cd /opt/crittr && python triage_qa.py --n 20 --report /var/crittr/qa/weekly.md
0 9 1-7 * 1    www-data  cd /opt/crittr && python partner_recon.py --partner vetster --statement /var/crittr/statements/latest.csv || true
```

Env vars summary (add to `.env` or the host's secret store):

```
# admin dashboards (shared by 6.1 + 6.2)
ADMIN_USER=...
ADMIN_PASS=...

# LLM retry/fallback tuning (6.5)
LLM_RETRIES=1
LLM_RETRY_BASE_MS=250

# alerts (6.4)
SLACK_ALERT_WEBHOOK=
ALERT_EMAIL=

# nightly jobs (6.6)
SUMMARY_MAX_CHARS=6000

# find-a-vet (7.4)
GOOGLE_PLACES_API_KEY=
FIND_VET_CACHE_TTL=600

# referrals (7.5)
REFERRER_CREDIT_CENTS=500
REFEREE_CREDIT_CENTS=500
BASE_URL=https://crittr.ai
```

---

## Smoke test status

Every code module was smoke-tested in the sandbox during development:

- 6.1 admin_dashboard — HTTP basic auth gate, HTML + JSON endpoints, verdict_class filter
- 6.2 events — event write, funnel math, partial indexes
- 6.3 partner_recon — three partner CSV shapes, monetary parsing, matched/unmatched/converted buckets
- 6.4 alerts — 5 checks + fallback observer round-trip (notification-free run)
- 6.5 llm_client — retry/backoff, Anthropic→OpenAI fallback, observer fires on exhaustion
- 6.6 nightly_jobs — stale-pets CTE, summary truncation at SUMMARY_MAX_CHARS
- 6.7 triage_qa — fence-stripping JSON parser, markdown report rendering
- 7.1 seo_landings — all seeded slugs render with OG + JSON-LD, sitemap.xml
- 7.2 index-v2.html — 890 lines, all 12 sanity checks pass (doctype, closing tags, og:*, JSON-LD WebSite + Organization, /api/chat/anon, verdict-card, partner-card, thinking-dots)
- 7.3 weekly_digest — subject/html/text render, seasonal tip selection wraps year-end
- 7.4 find_vet — coordinate validation, distance sort, missing-API-key graceful degrade
- 7.5 referrals — code generation idempotent, redeem + credit ledger, self-ref guard, cookie redirect, auth-required endpoints
- 7.7 regions — all 5 inference paths (query/cookie/CF/Accept-Language/default), unknown-region fallback, footer HTML

## What's not in this ship

- No A/B framework for landing-page variants yet — `/c/<slug>` is single-variant.
- No Stripe wiring for the referral credits — they sit in `user_credits` as a ledger; redemption at checkout is a Phase 8 item.
- No admin UI for editing topics — add them by editing `seo_landings.TOPICS` and redeploying.
- `alerts.py` doesn't page — Slack/email only. Pager integration is Phase 8.
- `partner_recon.py` is manual-run; auto-ingest from partner SFTPs comes later.
