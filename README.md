# Token Dashy

Local AI token analytics for OpenAI and Anthropic.

Token Dashy runs a FastAPI collector service, stores normalized usage/cost data in SQLite, and serves a Vite/React dashboard with burn charts, project filters, and observed rate-limit snapshots.

## What it tracks

- Historical token burn from provider admin usage APIs.
- Historical cost from provider admin cost APIs.
- Project/workspace breakdowns where providers expose IDs.
- Optional rate-limit snapshots from deliberate canary requests.

The important constraint: historical usage endpoints require admin keys, not ordinary inference keys. Canary requests use ordinary inference keys, but they are disabled by default because they spend real API quota.

## Architecture

```text
backend/   FastAPI + SQLite + provider collectors
frontend/  Vite + React + Recharts dashboard
data/      local SQLite database when running without Docker
```

## Quick start

For host migration or a concise setup path, see [QUICKSTART.md](QUICKSTART.md).

```bash
cp .env.example .env
docker compose up --build
```

Open:

- Dashboard: http://localhost:3000
- API health: http://localhost:8000/api/health

By default the app seeds demo data if no usage records exist. Set `TOKEN_DASHY_SEED_DEMO_DATA=false` when you only want provider data.

## Provider keys

Set these in `.env`:

```bash
OPENAI_ADMIN_KEY=
ANTHROPIC_ADMIN_KEY=
```

Optional canary mode:

```bash
TOKEN_DASHY_ENABLE_CANARY=true
OPENAI_API_KEY=
OPENAI_CANARY_MODEL=
ANTHROPIC_API_KEY=
ANTHROPIC_CANARY_MODEL=
```

Canary mode sends tiny generation requests to capture response headers such as OpenAI `x-ratelimit-*` and Anthropic `anthropic-ratelimit-*`. The dashboard labels these as observed capacity because they are snapshots from a specific model/key response, not a universal real-time account balance.

## Token alerts

Alerts open when token usage reaches `TOKEN_DASHY_ALERT_TOKEN_THRESHOLD`, defaulting to `0.95`.

Supported sources:

- Observed canary rate-limit token windows.
- Optional current-month provider token budgets:

```bash
TOKEN_DASHY_MONTHLY_TOKEN_BUDGET_OPENAI=
TOKEN_DASHY_MONTHLY_TOKEN_BUDGET_ANTHROPIC=
```

Set `TOKEN_DASHY_NTFY_TOPIC` to enable native ntfy.sh notifications. Optional ntfy settings:

```bash
TOKEN_DASHY_NTFY_SERVER=https://ntfy.sh
TOKEN_DASHY_NTFY_TOPIC=
TOKEN_DASHY_NTFY_TOKEN=
```

`TOKEN_DASHY_ALERT_WEBHOOK_URL` remains available for a generic JSON webhook.

## Local development

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

If the API is not on `http://localhost:8000`, set `VITE_API_BASE_URL`.

## API

- `GET /api/health`
- `GET /api/analytics/summary`
- `GET /api/analytics/trends?days=7&group_by=provider`
- `GET /api/analytics/projects`
- `GET /api/alerts`
- `POST /api/alerts/evaluate`
- `POST /api/collect`
- `POST /api/dev/seed`

## Notes on provider APIs

- OpenAI usage/cost collection targets organization endpoints such as `/v1/organization/usage/completions` and `/v1/organization/costs`.
- Anthropic usage/cost collection targets `/v1/organizations/usage_report/messages` and `/v1/organizations/cost_report`.
- Anthropic's programmatic usage/cost APIs require an Admin API key and are not available for individual accounts.
- Cost APIs are treated as financial totals; token counts alone are not used to infer exact billed spend.
