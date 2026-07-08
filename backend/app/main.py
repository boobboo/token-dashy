from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from app.alerts import evaluate_alerts, get_active_alerts, get_alert_history
from app.collectors import poll_all_metrics, seed_demo_data
from app.database import DB_PATH, get_conn, rows_to_dicts
from app.models import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    task: asyncio.Task | None = None
    if os.getenv("TOKEN_DASHY_START_POLLER", "true").lower() == "true":
        task = asyncio.create_task(polling_loop())
    try:
        yield
    finally:
        if task:
            task.cancel()


app = FastAPI(title="Token Dashy API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("TOKEN_DASHY_CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict:
    return {
        "app": "Token Dashy API",
        "status": "ok",
        "dashboard": "Open the frontend on the configured frontend port, usually http://<host>:3010",
        "health": "/api/health",
        "summary": "/api/analytics/summary",
        "alerts": "/api/alerts",
    }


async def polling_loop() -> None:
    interval = int(os.getenv("TOKEN_DASHY_POLL_INTERVAL_SECONDS", "300"))
    await asyncio.to_thread(poll_all_metrics)
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(poll_all_metrics)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "db_path": str(DB_PATH)}


@app.post("/api/collect")
async def collect_now(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(poll_all_metrics)
    return {"queued": True}


@app.post("/api/dev/seed")
def seed_now() -> dict:
    return {"message": seed_demo_data()}


@app.get("/api/analytics/summary")
def get_summary() -> dict:
    with get_conn() as conn:
        rate_limits = rows_to_dicts(
            conn.execute(
                """
                SELECT provider, model, limit_type, remaining, limit_value, reset_at,
                       reset_seconds, observed_at, status, source
                FROM rate_limits rl
                WHERE id IN (
                    SELECT MAX(id)
                    FROM rate_limits
                    GROUP BY provider, limit_type
                )
                ORDER BY provider, limit_type
                """
            ).fetchall()
        )
        burn_24h = rows_to_dicts(
            conn.execute(
                """
                SELECT provider,
                       SUM(total_tokens) AS total_tokens,
                       SUM(prompt_tokens) AS prompt_tokens,
                       SUM(completion_tokens) AS completion_tokens,
                       SUM(cached_tokens) AS cached_tokens,
                       SUM(request_count) AS request_count
                FROM token_usage
                WHERE datetime(bucket_start) >= datetime('now', '-1 day')
                GROUP BY provider
                ORDER BY provider
                """
            ).fetchall()
        )
        cost_month = rows_to_dicts(
            conn.execute(
                """
                SELECT provider, SUM(amount) AS amount, currency,
                       GROUP_CONCAT(DISTINCT source) AS sources,
                       MIN(bucket_start) AS first_bucket,
                       MAX(bucket_start) AS last_bucket
                FROM cost_usage
                WHERE datetime(bucket_start) >= datetime('now', 'start of month')
                GROUP BY provider, currency
                ORDER BY provider
                """
            ).fetchall()
        )
        totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(total_tokens), 0) AS all_time_tokens,
                COALESCE(SUM(request_count), 0) AS all_time_requests,
                COUNT(DISTINCT provider || ':' || COALESCE(project_id, '')) AS tracked_projects
            FROM token_usage
            """
        ).fetchone()
        collector_runs = rows_to_dicts(
            conn.execute(
                """
                SELECT provider, collector, status, message, started_at, finished_at
                FROM collector_runs cr
                WHERE id IN (
                    SELECT MAX(id)
                    FROM collector_runs
                    GROUP BY provider, collector
                )
                ORDER BY provider, collector
                """
            ).fetchall()
        )
        data_window = dict(
            conn.execute(
                """
                SELECT MIN(bucket_start) AS first_usage,
                       MAX(bucket_start) AS last_usage,
                       GROUP_CONCAT(DISTINCT source) AS usage_sources,
                       SUM(CASE WHEN source = 'demo_seed' THEN 1 ELSE 0 END) AS demo_rows,
                       COUNT(*) AS usage_rows
                FROM token_usage
                """
            ).fetchone()
        )
        cost_window = dict(
            conn.execute(
                """
                SELECT MIN(bucket_start) AS first_cost,
                       MAX(bucket_start) AS last_cost,
                       GROUP_CONCAT(DISTINCT source) AS cost_sources,
                       SUM(CASE WHEN source = 'demo_seed' THEN 1 ELSE 0 END) AS demo_rows,
                       COUNT(*) AS cost_rows
                FROM cost_usage
                WHERE datetime(bucket_start) >= datetime('now', 'start of month')
                """
            ).fetchone()
        )
        active_alerts = get_active_alerts()
    return {
        "rate_limits": rate_limits,
        "burn_24h": burn_24h,
        "cost_month": cost_month,
        "data_window": data_window,
        "cost_window": cost_window,
        "totals": dict(totals),
        "collector_runs": collector_runs,
        "active_alerts": active_alerts,
    }


@app.get("/api/alerts")
def alerts(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    return {"active": get_active_alerts(), "history": get_alert_history(limit)}


@app.post("/api/alerts/evaluate")
def evaluate_alerts_now() -> dict:
    result = evaluate_alerts()
    return {"active": get_active_alerts(), **result}


@app.get("/api/analytics/trends")
def get_trends(
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    group_by: Annotated[str, Query(pattern="^(provider|project|model)$")] = "provider",
    provider: str | None = None,
    project: str | None = None,
) -> list[dict]:
    dimension = {
        "provider": "provider",
        "project": "COALESCE(project_id, 'unassigned')",
        "model": "model",
    }[group_by]
    filters = ["datetime(bucket_start) >= datetime('now', ?)"]
    params: list[object] = [f"-{days} days"]
    if provider and provider != "all":
        filters.append("provider = ?")
        params.append(provider)
    if project and project != "all":
        filters.append("COALESCE(project_id, 'unassigned') = ?")
        params.append(project)

    where_clause = " AND ".join(filters)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT strftime('%Y-%m-%dT%H:00:00Z', bucket_start) AS time_bucket,
                   provider,
                   COALESCE(project_id, 'unassigned') AS project_id,
                   model,
                   {dimension} AS dimension,
                   SUM(total_tokens) AS tokens,
                   SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(cached_tokens) AS cached_tokens,
                   SUM(request_count) AS request_count
            FROM token_usage
            WHERE {where_clause}
            GROUP BY time_bucket, dimension, provider, project_id, model
            ORDER BY time_bucket ASC
            """,
            params,
        ).fetchall()
    return rows_to_dicts(rows)


@app.get("/api/analytics/projects")
def get_projects(
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    provider: str | None = None,
    project: str | None = None,
) -> dict:
    filters = ["datetime(bucket_start) >= datetime('now', ?)"]
    params: list[object] = [f"-{days} days"]
    if provider and provider != "all":
        filters.append("provider = ?")
        params.append(provider)
    if project and project != "all":
        filters.append("COALESCE(project_id, 'unassigned') = ?")
        params.append(project)
    where_clause = " AND ".join(filters)

    with get_conn() as conn:
        providers = rows_to_dicts(
            conn.execute("SELECT DISTINCT provider FROM token_usage ORDER BY provider").fetchall()
        )
        projects = rows_to_dicts(
            conn.execute(
                f"""
                SELECT provider, COALESCE(project_id, 'unassigned') AS project_id,
                       SUM(total_tokens) AS total_tokens,
                       SUM(request_count) AS request_count,
                       MIN(bucket_start) AS first_seen,
                       MAX(bucket_start) AS last_seen,
                       GROUP_CONCAT(DISTINCT source) AS sources
                FROM token_usage
                WHERE {where_clause}
                GROUP BY provider, COALESCE(project_id, 'unassigned')
                ORDER BY total_tokens DESC
                """,
                params,
            ).fetchall()
        )
        models = rows_to_dicts(
            conn.execute(
                f"""
                SELECT provider, model, SUM(total_tokens) AS total_tokens
                FROM token_usage
                WHERE {where_clause}
                GROUP BY provider, model
                ORDER BY total_tokens DESC
                """,
                params,
            ).fetchall()
        )
    return {"providers": providers, "projects": projects, "models": models, "window": {"days": days}}
