# Token Dashy Quick Start

Use this when moving Token Dashy to another host.

## 1. Clone the repo

```bash
git clone https://github.com/boobboo/token-dashy.git
cd token-dashy
```

## 2. Create environment config

```bash
cp .env.example .env
```

Edit `.env`.

Minimum live config:

```bash
OPENAI_ADMIN_KEY=your_openai_admin_key
ANTHROPIC_ADMIN_KEY=your_anthropic_admin_key
TOKEN_DASHY_SEED_DEMO_DATA=false
TOKEN_DASHY_ENABLE_CANARY=false
TOKEN_DASHY_ALERT_TOKEN_THRESHOLD=0.95
TOKEN_DASHY_BACKEND_PORT=8010
TOKEN_DASHY_FRONTEND_PORT=3010
VITE_API_BASE_URL=
TOKEN_DASHY_POLL_INTERVAL_SECONDS=300
TOKEN_DASHY_POLL_DAYS=7
```

Important: historical usage and cost ingestion needs provider admin keys. Normal inference keys are not enough.

Optional observed rate-limit snapshots:

```bash
TOKEN_DASHY_ENABLE_CANARY=true
OPENAI_API_KEY=your_openai_inference_key
OPENAI_CANARY_MODEL=gpt-4.1-mini
ANTHROPIC_API_KEY=your_anthropic_inference_key
ANTHROPIC_CANARY_MODEL=claude-3-5-haiku-latest
```

Canary mode sends tiny real requests. Leave it off unless you explicitly want rate-limit header sampling.

## 3. Token alerts

Token Dashy opens active alerts when token usage reaches `TOKEN_DASHY_ALERT_TOKEN_THRESHOLD`, which defaults to `0.95`.

There are two supported alert sources:

- Observed rate-limit token windows from canary response headers.
- Optional current-month token budgets that you configure manually.

Monthly budget example:

```bash
TOKEN_DASHY_MONTHLY_TOKEN_BUDGET_OPENAI=10000000
TOKEN_DASHY_MONTHLY_TOKEN_BUDGET_ANTHROPIC=10000000
```

ntfy.sh notifications:

```bash
TOKEN_DASHY_NTFY_SERVER=https://ntfy.sh
TOKEN_DASHY_NTFY_TOPIC=your-private-topic
TOKEN_DASHY_NTFY_TOKEN=
```

Only the topic is required for a public ntfy.sh topic. Use a long, unguessable topic name.

Optional generic JSON webhook:

```bash
TOKEN_DASHY_ALERT_WEBHOOK_URL=https://example.internal/token-dashy-alerts
```

ntfy and webhook notifications are sent only when a new alert opens. Existing active alerts are updated in SQLite without repeatedly sending notifications.

## 4. Run with Docker

```bash
docker compose up --build -d
```

Open:

- Dashboard: `http://<host>:3010`
- Backend health: `http://<host>:8010/api/health`

If `http://<host>:8010/` opens an API status page, that is normal. It is the backend, not the dashboard.

In Docker Compose, leave `VITE_API_BASE_URL` blank. The frontend calls `/api/...` on port `3010`, and Vite proxies those requests to the backend container internally. This avoids browser-side `localhost` problems on remote hosts.

The SQLite database is stored in the Docker volume `token-dashy_sqlite_data`.

## 5. Check ingestion

```bash
curl http://localhost:8010/api/health
curl http://localhost:8010/api/analytics/summary
```

To trigger collection immediately:

```bash
curl -X POST http://localhost:8010/api/collect
```

The normal poller runs every `TOKEN_DASHY_POLL_INTERVAL_SECONDS`.

To evaluate alerts immediately:

```bash
curl -X POST http://localhost:8010/api/alerts/evaluate
curl http://localhost:8010/api/alerts
```

If demo data was previously seeded, setting `TOKEN_DASHY_SEED_DEMO_DATA=false` will not remove existing SQLite rows. To purge only demo rows:

```bash
sudo docker-compose exec backend python -c "import sqlite3; conn=sqlite3.connect('/data/token_dashy.db'); [conn.execute(sql) for sql in (\"DELETE FROM token_usage WHERE source='demo_seed'\", \"DELETE FROM cost_usage WHERE source='demo_seed'\", \"DELETE FROM rate_limits WHERE source='demo_seed'\", \"DELETE FROM alert_events\")]; conn.commit(); conn.close()"
```

Or, if you want to wipe the whole Token Dashy database:

```bash
sudo docker-compose down
sudo docker volume rm token-dashy_sqlite_data
sudo docker-compose up --build -d
```

## 6. Back up or move the SQLite database

If running via Docker:

```bash
docker compose stop backend
docker run --rm -v token-dashy_sqlite_data:/data -v "$PWD:/backup" alpine \
  sh -c "cp /data/token_dashy.db /backup/token_dashy.db"
docker compose start backend
```

Restore on a new host:

```bash
docker compose up --build -d
docker compose stop backend
docker run --rm -v token-dashy_sqlite_data:/data -v "$PWD:/backup" alpine \
  sh -c "cp /backup/token_dashy.db /data/token_dashy.db"
docker compose start backend
```

## 7. Non-Docker fallback

Backend:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export TOKEN_DASHY_DB_PATH=../data/token_dashy.db
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd frontend
npm install
export VITE_API_BASE_URL=http://<host>:8010
npm run build
npm run preview -- --host 0.0.0.0 --port 3010
```

For a persistent non-Docker host, put both processes behind systemd or your host supervisor.

## 8. Production notes

- Do not commit `.env`.
- Put the app behind HTTPS if reachable beyond your LAN.
- Restrict inbound access to the backend where possible.
- Keep `TOKEN_DASHY_SEED_DEMO_DATA=false` on live hosts.
- Treat canary limits as observed snapshots, not exact account-wide capacity.
