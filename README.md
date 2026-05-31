# visage-ads-agent

A media-buying agent for **Apple Search Ads (ASA)** that optimizes the iOS App
Store campaigns for [Visage](https://hellovisage.com) (App ID 6775249776,
bundle `com.pgammedia.visage`) based on backend conversion signals — not just
install volume.

> Cron-driven Python worker. Not a web service. Deploys to a Render free-tier
> cron job. Read-only against Apple Search Ads and the backend in Phase 1.

---

## Why it exists

Apple's dashboard tells you what you spent and how many people tapped or
installed. It does not tell you whether those installs became **trials** or
**paid subscribers** — that data lives in the Visage backend (Neon Postgres).

This agent joins the two and reports the only metric that actually matters:
**CPA-to-paid per keyword.** Once that signal is trustworthy, the agent
graduates to recommending bid / pause / budget moves, and eventually to
executing the high-confidence ones itself within a hard guardrail box.

---

## Architecture — three phases, ship in order

### Phase 1 — Reporter (observe mode)  ✅ shipped here

Daily cron at 09:00 UTC:

1. Sign a client-secret JWT with the App Store Connect `.p8` key (Apple Search
   Ads uses the same key system as ASC, but with `aud=https://appleid.apple.com`).
2. Exchange the JWT for a ~1h access token at
   `https://appleid.apple.com/auth/oauth2/token`.
3. List every campaign in the org, then POST to
   `/api/v5/reports/campaigns/{id}/keywords` to pull yesterday's keyword-level
   performance for each campaign.
4. Query the **backend** Neon DB for users created in the same window whose
   `users.acquisition_source` matches `asa:<campaignId>:<adGroupId>:<keywordId>`.
5. Compute spend, installs, signups, trials, paid + CPA-at-each-stage.
6. Render a Slack digest with a fixed-width Markdown table.
7. Persist the full snapshot to **the agent's own** Postgres schema
   (`ads_agent.ads_snapshots`). The backend DB is never written.

**No writes to ASA.** Phase 1 is observation only. The point is to build
trust by showing what a future automated decision would look like, before
giving the agent any authority.

### Phase 2 — Recommender (review mode)  🚧 stubbed in `recommend.py`

Once Phase 1 has 7 days of snapshots, a rule engine flags pause/scale/bid/
budget candidates and posts each as a Slack message with thumbs-up/-down
reaction approval. Approved actions feed Phase 3.

### Phase 3 — Auto-execute with guardrails  🚧 stubbed in `agent.py`

After ~30 days of consistent approve/reject patterns, Claude (Sonnet, with
prompt caching on the system prompt) proposes changes given the daily report
+ recent decision history. A hard guardrail validator drops anything that
breaks a rule before any HTTP call is made. Auto-executable categories run
themselves; bigger moves still gate on human approval.

---

## Repo layout

```
visage-ads-agent/
├── visage_ads_agent/
│   ├── asa_client.py       # ASA OAuth + report API (async, retry/backoff)
│   ├── backend_db.py       # READ-ONLY queries against Neon backend
│   ├── agent_db.py         # writes to ads_agent schema (snapshots/recos/exec_log)
│   ├── slack.py            # incoming-webhook digest formatter
│   ├── config.py           # env-driven Config dataclass
│   ├── migrate.py          # creates ads_agent.* tables
│   ├── report.py           # Phase 1 entrypoint (`make report`)
│   ├── recommend.py        # Phase 2 stub (`make recommend`)
│   └── agent.py            # Phase 3 stub (`make execute`)
├── sql/
│   └── backend_migration_suggestion.sql   # adds users.acquisition_source
├── render.yaml             # Render Blueprint for the daily cron
├── Makefile
├── requirements.txt
├── .env.example
└── README.md
```

---

## Local setup

```bash
cd ~/Desktop/visage/visage-ads-agent
python3.12 -m venv .venv && source .venv/bin/activate
make install
cp .env.example .env
# edit .env (see "What you still owe" below)
make migrate          # creates the ads_agent schema on AGENT_DATABASE_URL
python -m visage_ads_agent.asa_client --check   # prints /acls — proves auth
make report           # runs Phase 1 end-to-end against your real account
```

### Credentials checklist

| Var | Source | Already known? |
|---|---|---|
| `ASA_KEY_ID` | `NMKX98FQ8B` | ✅ baked into `.env.example` |
| `ASA_ISSUER_ID` | `3207b23a-16e9-49ca-893b-4dda7d4abfb9` | ✅ baked in |
| `ASA_PRIVATE_KEY_PATH` | `~/.appstoreconnect/private_keys/AuthKey_NMKX98FQ8B.p8` | ✅ on your disk |
| `ASA_ORG_ID` | Returned by `GET /api/v5/acls` once Apple approves the Advanced account | ❌ owed by you |
| `BACKEND_DATABASE_URL` | Render → `srv-d7i28kcvikkc73aiovog` → env → `DATABASE_URL` | ✅ pull via Render API |
| `AGENT_DATABASE_URL` | Easiest: same Neon project as backend, different DB or schema | ❌ create on Neon |
| `SLACK_WEBHOOK_URL` | Slack → Apps → Incoming Webhooks → new webhook to `#visage-ads` | ❌ owed by you |
| `ANTHROPIC_API_KEY` | Anthropic console | ❌ not needed until Phase 3 |

---

## Deploying to Render (Phase 1)

1. Push this repo (we already did — see "Repo URL" in the session summary).
2. Render dashboard → **New** → **Blueprint** → point at `mastap150/visage-ads-agent`.
3. Render reads `render.yaml`, creates a free-tier cron job named
   `visage-ads-report` running daily at **09:00 UTC**.
4. Fill in every env var marked `sync: false` in `render.yaml`. For
   `ASA_PRIVATE_KEY_PEM`, open `AuthKey_NMKX98FQ8B.p8` and paste its full
   contents (the `-----BEGIN PRIVATE KEY-----` block included). Render
   accepts real newlines; you don't need to escape them.
5. Trigger a manual run from the Render dashboard once to verify Slack lights up.

That's it. After Apple approves Search Ads Advanced and you flip the
campaigns from Paused to Active, the daily digest will start populating with
real data automatically.

---

## How attribution works (and why we ask for a backend migration)

ASA install attribution lives outside the agent. Apple's MMP / AdServices
framework hands the iOS client a token; the iOS client forwards the resolved
attribution to the Visage backend on first signup. The backend stores it as:

```
users.acquisition_source = 'asa:<campaignId>:<adGroupId>:<keywordId>'
```

The agent parses that string to join keywords with their downstream funnel.
**That column does not yet exist on the backend.** See
[`sql/backend_migration_suggestion.sql`](sql/backend_migration_suggestion.sql)
— apply that on Neon (the agent does not touch the backend repo). Until then,
Phase 1 still runs and posts the Slack digest, but CPA columns show `-`
because there are no conversions to join. The agent logs a warning and
includes a note in the digest body so it's obvious.

The iOS client also needs to send the attribution — that's a small change in
the `visage-mobile` repo (out of scope here). Briefly: when a user first
signs up, call AdServices' `AAAttribution.attributionToken()`, POST it to
Apple's `https://api-adservices.apple.com/api/v1/`, parse the response, and
forward `{campaignId, adGroupId, keywordId}` to the backend signup endpoint
as part of the user-creation payload.

---

## What you still owe me

To turn Phase 1 from "ships and runs" into "useful daily signal":

1. **Apple Search Ads Org ID** — Once Apple approves the Advanced account at
   https://app.searchads.apple.com, run:
   ```bash
   make auth-check
   ```
   The first object in the response has `orgId`. Drop it into `ASA_ORG_ID`
   (and into the Render env).
2. **Slack webhook URL** — Create an Incoming Webhook in your workspace
   pointing at the channel you want the digest in (e.g. `#visage-ads`).
   Paste it into `SLACK_WEBHOOK_URL`.
3. **`acquisition_source` column on the backend** — Run
   `sql/backend_migration_suggestion.sql` on Neon. (Or punt — Phase 1 still
   works without it; you'll just see no CPAs.)
4. **iOS attribution** — Wire up the AdServices token capture in
   `visage-mobile` so new users get the column populated. Without this, the
   column stays empty even after the migration.
5. **Agent Postgres** — Pick where `AGENT_DATABASE_URL` points. Recommended:
   make a second database in the same Neon project (cheap, same network)
   and point both env vars there with `AGENT_DB_SCHEMA=ads_agent`.

Once those are in, the cron will start sending a useful daily digest within
24h of you activating campaigns in the ASA dashboard.

---

## What this repo will NOT do

- **Won't create campaigns programmatically.** You build the 3 manual
  campaigns in the ASA dashboard per `~/Desktop/visage/APPLE_SEARCH_ADS.md`.
  The agent only optimizes campaigns that already exist.
- **Won't write to the backend.** Read-only against `BACKEND_DATABASE_URL`.
- **Won't auto-execute anything in Phase 1.** Phase 2 requires human
  thumbs-up. Phase 3 only auto-executes inside a hard guardrail box.
- **Won't touch the `visage-backend` or `web` repos.** Separate concern;
  separate repo; separate Postgres schema.

---

## Notes on the ASA API that bit me while writing this

- The audience claim for the JWT is **`https://appleid.apple.com`**, not the
  ASC audience (`appstoreconnect-v1`). Different OAuth flow entirely.
- After the token exchange you need `X-AP-Context: orgId=<id>` on every
  request. Missing it returns `401 INVALID_TOKEN`, which looks like an auth
  bug but is actually a missing header.
- Reporting endpoints are POST, not GET. The body's `selector` block is
  required; `groupBy` is optional but recommended (`["countryCode"]` gives
  per-country breakdown without extra cost).
- Currency comes back as `{ amount: "1.23", currency: "USD" }` — string amount.
- Apple's docs are at https://developer.apple.com/documentation/apple_search_ads
  but the OAuth flow is documented under "Implementing OAuth for the Apple
  Search Ads API" which is a separate page; the main docs link there but
  not prominently.
