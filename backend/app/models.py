from __future__ import annotations

from app.database import get_conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket_start TEXT NOT NULL,
                bucket_end TEXT,
                provider TEXT NOT NULL,
                project_id TEXT,
                api_key_id TEXT,
                workspace_id TEXT,
                model TEXT NOT NULL DEFAULT 'unknown',
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                request_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'usage_api',
                raw_json TEXT,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS cost_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket_start TEXT NOT NULL,
                bucket_end TEXT,
                provider TEXT NOT NULL,
                project_id TEXT,
                workspace_id TEXT,
                api_key_id TEXT,
                line_item TEXT,
                amount REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'usd',
                source TEXT NOT NULL DEFAULT 'cost_api',
                raw_json TEXT,
                inserted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS rate_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                provider TEXT NOT NULL,
                model TEXT,
                limit_type TEXT NOT NULL,
                remaining INTEGER,
                limit_value INTEGER,
                reset_at TEXT,
                reset_seconds INTEGER,
                source TEXT NOT NULL DEFAULT 'canary',
                raw_headers TEXT,
                status TEXT NOT NULL DEFAULT 'ok'
            );

            CREATE TABLE IF NOT EXISTS collector_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                collector TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                status TEXT NOT NULL,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_key TEXT NOT NULL,
                provider TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                threshold REAL NOT NULL,
                usage_ratio REAL NOT NULL,
                used_tokens INTEGER NOT NULL,
                token_limit INTEGER NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_token_usage_bucket_provider
                ON token_usage(bucket_start, provider);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_token_usage_bucket_dimension
                ON token_usage(
                    bucket_start,
                    provider,
                    COALESCE(project_id, ''),
                    COALESCE(api_key_id, ''),
                    COALESCE(workspace_id, ''),
                    model,
                    source
                );
            CREATE INDEX IF NOT EXISTS idx_cost_usage_bucket_provider
                ON cost_usage(bucket_start, provider);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_cost_usage_bucket_dimension
                ON cost_usage(
                    bucket_start,
                    provider,
                    COALESCE(project_id, ''),
                    COALESCE(workspace_id, ''),
                    COALESCE(api_key_id, ''),
                    COALESCE(line_item, ''),
                    source
                );
            CREATE INDEX IF NOT EXISTS idx_rate_limits_provider_type
                ON rate_limits(provider, limit_type, observed_at);
            CREATE INDEX IF NOT EXISTS idx_alert_events_status
                ON alert_events(status, updated_at);
            CREATE UNIQUE INDEX IF NOT EXISTS uq_active_alert_events
                ON alert_events(alert_key)
                WHERE status = 'active';
            """
        )
