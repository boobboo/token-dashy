from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.database import get_conn, rows_to_dicts


TOKEN_THRESHOLD = float(os.getenv("TOKEN_DASHY_ALERT_TOKEN_THRESHOLD", "0.95"))
WEBHOOK_URL = os.getenv("TOKEN_DASHY_ALERT_WEBHOOK_URL", "").strip()
NTFY_SERVER = os.getenv("TOKEN_DASHY_NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
NTFY_TOPIC = os.getenv("TOKEN_DASHY_NTFY_TOPIC", "").strip().strip("/")
NTFY_TOKEN = os.getenv("TOKEN_DASHY_NTFY_TOKEN", "").strip()


def utcnow() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def provider_budget(provider: str) -> int | None:
    env_name = f"TOKEN_DASHY_MONTHLY_TOKEN_BUDGET_{provider.upper()}"
    raw = os.getenv(env_name)
    if not raw:
        return None
    try:
        budget = int(raw)
    except ValueError:
        return None
    return budget if budget > 0 else None


def latest_rate_limit_token_windows() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT provider, model, limit_type, remaining, limit_value,
                       observed_at, reset_at, source
                FROM rate_limits
                WHERE id IN (
                    SELECT MAX(id)
                    FROM rate_limits
                    WHERE limit_type IN ('tokens', 'input-tokens', 'output-tokens')
                    GROUP BY provider, model, limit_type
                )
                  AND limit_value IS NOT NULL
                  AND limit_value > 0
                  AND remaining IS NOT NULL
                """
            ).fetchall()
        )


def current_month_usage() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT provider, SUM(total_tokens) AS used_tokens
                FROM token_usage
                WHERE datetime(bucket_start) >= datetime('now', 'start of month')
                GROUP BY provider
                """
            ).fetchall()
        )


def active_alert_for(alert_key: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM alert_events
            WHERE alert_key = ? AND status = 'active'
            ORDER BY triggered_at DESC
            LIMIT 1
            """,
            (alert_key,),
        ).fetchone()
    return dict(row) if row else None


def open_or_update_alert(alert: dict[str, Any]) -> bool:
    existing = active_alert_for(alert["alert_key"])
    now = utcnow()
    payload = {
        "updated_at": now,
        "usage_ratio": alert["usage_ratio"],
        "used_tokens": alert["used_tokens"],
        "token_limit": alert["token_limit"],
        "message": alert["message"],
        "details_json": json.dumps(alert.get("details", {}), separators=(",", ":")),
    }
    if existing:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE alert_events
                SET updated_at = :updated_at,
                    usage_ratio = :usage_ratio,
                    used_tokens = :used_tokens,
                    token_limit = :token_limit,
                    message = :message,
                    details_json = :details_json
                WHERE id = :id
                """,
                {**payload, "id": existing["id"]},
            )
        return False

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO alert_events (
                alert_key, provider, alert_type, dimension, threshold, usage_ratio,
                used_tokens, token_limit, message, details_json, status,
                triggered_at, updated_at
            )
            VALUES (
                :alert_key, :provider, :alert_type, :dimension, :threshold, :usage_ratio,
                :used_tokens, :token_limit, :message, :details_json, 'active',
                :triggered_at, :updated_at
            )
            """,
            {
                **alert,
                "details_json": json.dumps(alert.get("details", {}), separators=(",", ":")),
                "triggered_at": now,
                "updated_at": now,
            },
        )
    return True


def resolve_alert(alert_key: str, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE alert_events
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP,
                message = ?
            WHERE alert_key = ? AND status = 'active'
            """,
            (reason, alert_key),
        )


def send_webhook(alert: dict[str, Any]) -> None:
    if not WEBHOOK_URL:
        return
    payload = {
        "event": "token_dashy.alert",
        "alert": alert,
    }
    with httpx.Client(timeout=10) as client:
        client.post(WEBHOOK_URL, json=payload).raise_for_status()


def send_ntfy(alert: dict[str, Any]) -> None:
    if not NTFY_TOPIC:
        return
    headers = {
        "Title": "Token Dashy alert",
        "Priority": "urgent",
        "Tags": "warning,chart_with_upwards_trend",
        "Markdown": "yes",
    }
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    body = (
        f"{alert['message']}\n\n"
        f"- Provider: {alert['provider']}\n"
        f"- Alert type: {alert['alert_type']}\n"
        f"- Used: {alert['used_tokens']:,} / {alert['token_limit']:,} tokens\n"
        f"- Threshold: {alert['threshold']:.0%}"
    )
    with httpx.Client(timeout=10) as client:
        client.post(f"{NTFY_SERVER}/{NTFY_TOPIC}", headers=headers, content=body).raise_for_status()


def evaluate_rate_limit_alerts() -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for row in latest_rate_limit_token_windows():
        used = max(0, int(row["limit_value"]) - int(row["remaining"]))
        ratio = used / int(row["limit_value"])
        dimension = f"{row['model'] or 'unknown'}:{row['limit_type']}"
        key = f"rate_limit_tokens:{row['provider']}:{dimension}"
        if ratio >= TOKEN_THRESHOLD:
            alerts.append(
                {
                    "alert_key": key,
                    "provider": row["provider"],
                    "alert_type": "rate_limit_tokens",
                    "dimension": dimension,
                    "threshold": TOKEN_THRESHOLD,
                    "usage_ratio": ratio,
                    "used_tokens": used,
                    "token_limit": row["limit_value"],
                    "message": (
                        f"{row['provider']} {row['limit_type']} window is "
                        f"{ratio:.1%} used for {row['model'] or 'unknown'}."
                    ),
                    "details": row,
                }
            )
        else:
            resolve_alert(key, f"Resolved: usage is {ratio:.1%}, below {TOKEN_THRESHOLD:.0%}.")
    return alerts


def evaluate_monthly_budget_alerts() -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for row in current_month_usage():
        budget = provider_budget(row["provider"])
        if not budget:
            continue
        used = int(row["used_tokens"] or 0)
        ratio = used / budget
        key = f"monthly_token_budget:{row['provider']}"
        if ratio >= TOKEN_THRESHOLD:
            alerts.append(
                {
                    "alert_key": key,
                    "provider": row["provider"],
                    "alert_type": "monthly_token_budget",
                    "dimension": "month",
                    "threshold": TOKEN_THRESHOLD,
                    "usage_ratio": ratio,
                    "used_tokens": used,
                    "token_limit": budget,
                    "message": f"{row['provider']} monthly token budget is {ratio:.1%} used.",
                    "details": row,
                }
            )
        else:
            resolve_alert(key, f"Resolved: usage is {ratio:.1%}, below {TOKEN_THRESHOLD:.0%}.")
    return alerts


def evaluate_alerts() -> dict[str, Any]:
    candidates = evaluate_rate_limit_alerts() + evaluate_monthly_budget_alerts()
    new_alerts: list[dict[str, Any]] = []
    notification_errors: list[str] = []
    for alert in candidates:
        is_new = open_or_update_alert(alert)
        if is_new:
            new_alerts.append(alert)
            try:
                send_ntfy(alert)
            except Exception as exc:
                notification_errors.append(f"ntfy {alert['alert_key']}: {exc}")
            try:
                send_webhook(alert)
            except Exception as exc:
                notification_errors.append(f"webhook {alert['alert_key']}: {exc}")
    return {"new_alerts": new_alerts, "notification_errors": notification_errors}


def get_active_alerts() -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT id, alert_key, provider, alert_type, dimension, threshold,
                       usage_ratio, used_tokens, token_limit, message, status,
                       triggered_at, updated_at, resolved_at
                FROM alert_events
                WHERE status = 'active'
                ORDER BY usage_ratio DESC, updated_at DESC
                """
            ).fetchall()
        )


def get_alert_history(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_to_dicts(
            conn.execute(
                """
                SELECT id, alert_key, provider, alert_type, dimension, threshold,
                       usage_ratio, used_tokens, token_limit, message, status,
                       triggered_at, updated_at, resolved_at
                FROM alert_events
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
