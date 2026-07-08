from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.alerts import evaluate_alerts
from app.database import get_conn


USER_AGENT = "token-dashy/0.1.0 (https://github.com/boobboo/token-dashy)"
POLL_DAYS = int(os.getenv("TOKEN_DASHY_POLL_DAYS", "7"))
REQUEST_TIMEOUT = float(os.getenv("TOKEN_DASHY_REQUEST_TIMEOUT", "20"))


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_epoch(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return iso(datetime.fromtimestamp(int(value), UTC))
    except (TypeError, ValueError, OSError):
        return str(value)


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def run_started(provider: str, collector: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO collector_runs(provider, collector, status) VALUES (?, ?, ?)",
            (provider, collector, "running"),
        )
        return int(cur.lastrowid)


def run_finished(run_id: int, status: str, message: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE collector_runs
            SET finished_at = CURRENT_TIMESTAMP, status = ?, message = ?
            WHERE id = ?
            """,
            (status, message[:1000], run_id),
        )


def upsert_token_usage(record: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO token_usage (
                bucket_start, bucket_end, provider, project_id, api_key_id, workspace_id,
                model, prompt_tokens, completion_tokens, cached_tokens, total_tokens,
                request_count, source, raw_json
            )
            VALUES (
                :bucket_start, :bucket_end, :provider, :project_id, :api_key_id, :workspace_id,
                :model, :prompt_tokens, :completion_tokens, :cached_tokens, :total_tokens,
                :request_count, :source, :raw_json
            )
            ON CONFLICT DO UPDATE SET
                bucket_end = excluded.bucket_end,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                cached_tokens = excluded.cached_tokens,
                total_tokens = excluded.total_tokens,
                request_count = excluded.request_count,
                raw_json = excluded.raw_json,
                inserted_at = CURRENT_TIMESTAMP
            """,
            record,
        )


def upsert_cost_usage(record: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO cost_usage (
                bucket_start, bucket_end, provider, project_id, workspace_id, api_key_id,
                line_item, amount, currency, source, raw_json
            )
            VALUES (
                :bucket_start, :bucket_end, :provider, :project_id, :workspace_id, :api_key_id,
                :line_item, :amount, :currency, :source, :raw_json
            )
            ON CONFLICT DO UPDATE SET
                bucket_end = excluded.bucket_end,
                amount = excluded.amount,
                currency = excluded.currency,
                raw_json = excluded.raw_json,
                inserted_at = CURRENT_TIMESTAMP
            """,
            record,
        )


def insert_rate_limit(record: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO rate_limits (
                observed_at, provider, model, limit_type, remaining, limit_value,
                reset_at, reset_seconds, source, raw_headers, status
            )
            VALUES (
                :observed_at, :provider, :model, :limit_type, :remaining, :limit_value,
                :reset_at, :reset_seconds, :source, :raw_headers, :status
            )
            """,
            record,
        )


def paged_get(client: httpx.Client, url: str, headers: dict[str, str], params: dict[str, Any]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_params = params.copy()
    while True:
        response = client.get(url, headers=headers, params=page_params)
        response.raise_for_status()
        payload = response.json()
        pages.extend(payload.get("data", []))
        next_page = payload.get("next_page")
        if not payload.get("has_more") or not next_page:
            break
        page_params["page"] = next_page
    return pages


def collect_openai_usage() -> str:
    admin_key = os.getenv("OPENAI_ADMIN_KEY")
    if not admin_key:
        return "OPENAI_ADMIN_KEY not set; skipped historical usage."

    started = run_started("openai", "usage")
    try:
        end = utcnow()
        start = end - timedelta(days=POLL_DAYS)
        headers = {"Authorization": f"Bearer {admin_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
        params = {
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "bucket_width": "1h",
            "group_by": ["model", "project_id", "api_key_id"],
            "limit": 168,
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            buckets = paged_get(
                client,
                "https://api.openai.com/v1/organization/usage/completions",
                headers,
                params,
            )
        count = 0
        for bucket in buckets:
            for result in bucket.get("results", []):
                prompt_tokens = as_int(result.get("input_tokens")) + as_int(result.get("input_audio_tokens"))
                completion_tokens = as_int(result.get("output_tokens")) + as_int(result.get("output_audio_tokens"))
                cached_tokens = as_int(result.get("input_cached_tokens"))
                upsert_token_usage(
                    {
                        "bucket_start": parse_epoch(bucket.get("start_time")),
                        "bucket_end": parse_epoch(bucket.get("end_time")),
                        "provider": "openai",
                        "project_id": result.get("project_id") or "unassigned",
                        "api_key_id": result.get("api_key_id"),
                        "workspace_id": None,
                        "model": result.get("model") or "unknown",
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cached_tokens": cached_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                        "request_count": as_int(result.get("num_model_requests")),
                        "source": "openai_usage_api",
                        "raw_json": json.dumps(result, separators=(",", ":")),
                    }
                )
                count += 1
        run_finished(started, "ok", f"stored {count} usage rows")
        return f"OpenAI usage stored {count} rows."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def collect_openai_costs() -> str:
    admin_key = os.getenv("OPENAI_ADMIN_KEY")
    if not admin_key:
        return "OPENAI_ADMIN_KEY not set; skipped costs."

    started = run_started("openai", "costs")
    try:
        end = utcnow()
        start = end - timedelta(days=POLL_DAYS)
        headers = {"Authorization": f"Bearer {admin_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
        params = {
            "start_time": int(start.timestamp()),
            "end_time": int(end.timestamp()),
            "bucket_width": "1d",
            "group_by": ["project_id", "line_item"],
            "limit": 31,
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            buckets = paged_get(client, "https://api.openai.com/v1/organization/costs", headers, params)
        count = 0
        for bucket in buckets:
            for result in bucket.get("results", []):
                amount = result.get("amount") or {}
                upsert_cost_usage(
                    {
                        "bucket_start": parse_epoch(bucket.get("start_time")),
                        "bucket_end": parse_epoch(bucket.get("end_time")),
                        "provider": "openai",
                        "project_id": result.get("project_id") or "unassigned",
                        "workspace_id": None,
                        "api_key_id": result.get("api_key_id"),
                        "line_item": result.get("line_item") or "usage",
                        "amount": as_float(amount.get("value")),
                        "currency": amount.get("currency") or "usd",
                        "source": "openai_cost_api",
                        "raw_json": json.dumps(result, separators=(",", ":")),
                    }
                )
                count += 1
        run_finished(started, "ok", f"stored {count} cost rows")
        return f"OpenAI costs stored {count} rows."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def collect_anthropic_usage() -> str:
    admin_key = os.getenv("ANTHROPIC_ADMIN_KEY")
    if not admin_key:
        return "ANTHROPIC_ADMIN_KEY not set; skipped historical usage."

    started = run_started("anthropic", "usage")
    try:
        end = utcnow()
        start = end - timedelta(days=POLL_DAYS)
        headers = {
            "x-api-key": admin_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": USER_AGENT,
        }
        params = {
            "starting_at": iso(start),
            "ending_at": iso(end),
            "bucket_width": "1h",
            "group_by[]": ["model", "workspace_id"],
            "limit": 168,
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            buckets = paged_get(
                client,
                "https://api.anthropic.com/v1/organizations/usage_report/messages",
                headers,
                params,
            )
        count = 0
        for bucket in buckets:
            bucket_start = bucket.get("starting_at") or bucket.get("start_time")
            bucket_end = bucket.get("ending_at") or bucket.get("end_time")
            for result in bucket.get("results", []):
                prompt_tokens = (
                    as_int(result.get("input_tokens"))
                    + as_int(result.get("cache_creation_input_tokens"))
                    + as_int(result.get("cache_read_input_tokens"))
                )
                completion_tokens = as_int(result.get("output_tokens"))
                upsert_token_usage(
                    {
                        "bucket_start": bucket_start,
                        "bucket_end": bucket_end,
                        "provider": "anthropic",
                        "project_id": result.get("workspace_id") or "unassigned",
                        "api_key_id": result.get("api_key_id"),
                        "workspace_id": result.get("workspace_id"),
                        "model": result.get("model") or "unknown",
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "cached_tokens": as_int(result.get("cache_read_input_tokens")),
                        "total_tokens": prompt_tokens + completion_tokens,
                        "request_count": as_int(result.get("num_model_requests") or result.get("request_count")),
                        "source": "anthropic_usage_api",
                        "raw_json": json.dumps(result, separators=(",", ":")),
                    }
                )
                count += 1
        run_finished(started, "ok", f"stored {count} usage rows")
        return f"Anthropic usage stored {count} rows."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def collect_anthropic_costs() -> str:
    admin_key = os.getenv("ANTHROPIC_ADMIN_KEY")
    if not admin_key:
        return "ANTHROPIC_ADMIN_KEY not set; skipped costs."

    started = run_started("anthropic", "costs")
    try:
        end = utcnow()
        start = end - timedelta(days=POLL_DAYS)
        headers = {
            "x-api-key": admin_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": USER_AGENT,
        }
        params = {
            "starting_at": iso(start),
            "ending_at": iso(end),
            "group_by[]": ["workspace_id", "description"],
            "limit": 31,
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            buckets = paged_get(client, "https://api.anthropic.com/v1/organizations/cost_report", headers, params)
        count = 0
        for bucket in buckets:
            bucket_start = bucket.get("starting_at") or bucket.get("start_time")
            bucket_end = bucket.get("ending_at") or bucket.get("end_time")
            for result in bucket.get("results", []):
                amount = result.get("amount") or {}
                raw_amount = amount.get("value") if isinstance(amount, dict) else None
                if raw_amount is None:
                    raw_amount = result.get("cost") or result.get("amount") or result.get("amount_usd")
                upsert_cost_usage(
                    {
                        "bucket_start": bucket_start,
                        "bucket_end": bucket_end,
                        "provider": "anthropic",
                        "project_id": result.get("workspace_id") or "unassigned",
                        "workspace_id": result.get("workspace_id"),
                        "api_key_id": result.get("api_key_id"),
                        "line_item": result.get("description") or result.get("line_item") or "usage",
                        "amount": as_float(raw_amount),
                        "currency": (amount.get("currency") if isinstance(amount, dict) else None) or "usd",
                        "source": "anthropic_cost_api",
                        "raw_json": json.dumps(result, separators=(",", ":")),
                    }
                )
                count += 1
        run_finished(started, "ok", f"stored {count} cost rows")
        return f"Anthropic costs stored {count} rows."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def sniff_openai_rate_limits() -> str:
    if os.getenv("TOKEN_DASHY_ENABLE_CANARY", "false").lower() != "true":
        return "Canary disabled; skipped OpenAI rate-limit sniff."
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_CANARY_MODEL")
    if not api_key or not model:
        return "OPENAI_API_KEY or OPENAI_CANARY_MODEL missing; skipped canary."

    started = run_started("openai", "rate_limit_canary")
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT}
        payload = {"model": model, "input": "ping", "max_output_tokens": 1}
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
        raw_headers = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith("x-ratelimit")}
        for limit_type in ("requests", "tokens"):
            insert_rate_limit(
                {
                    "observed_at": iso(utcnow()),
                    "provider": "openai",
                    "model": model,
                    "limit_type": limit_type,
                    "remaining": as_int(raw_headers.get(f"x-ratelimit-remaining-{limit_type}"), None),
                    "limit_value": as_int(raw_headers.get(f"x-ratelimit-limit-{limit_type}"), None),
                    "reset_at": raw_headers.get(f"x-ratelimit-reset-{limit_type}"),
                    "reset_seconds": None,
                    "source": "canary",
                    "raw_headers": json.dumps(raw_headers, separators=(",", ":")),
                    "status": "ok" if response.is_success else f"http_{response.status_code}",
                }
            )
        run_finished(started, "ok", "captured OpenAI rate headers")
        return "OpenAI rate limit headers captured."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def sniff_anthropic_rate_limits() -> str:
    if os.getenv("TOKEN_DASHY_ENABLE_CANARY", "false").lower() != "true":
        return "Canary disabled; skipped Anthropic rate-limit sniff."
    api_key = os.getenv("ANTHROPIC_API_KEY")
    model = os.getenv("ANTHROPIC_CANARY_MODEL")
    if not api_key or not model:
        return "ANTHROPIC_API_KEY or ANTHROPIC_CANARY_MODEL missing; skipped canary."

    started = run_started("anthropic", "rate_limit_canary")
    try:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": USER_AGENT,
        }
        payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]}
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
        raw_headers = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith("anthropic-ratelimit")}
        for limit_type in ("requests", "tokens", "input-tokens", "output-tokens"):
            reset_at = raw_headers.get(f"anthropic-ratelimit-{limit_type}-reset")
            insert_rate_limit(
                {
                    "observed_at": iso(utcnow()),
                    "provider": "anthropic",
                    "model": model,
                    "limit_type": limit_type,
                    "remaining": as_int(raw_headers.get(f"anthropic-ratelimit-{limit_type}-remaining"), None),
                    "limit_value": as_int(raw_headers.get(f"anthropic-ratelimit-{limit_type}-limit"), None),
                    "reset_at": reset_at,
                    "reset_seconds": None,
                    "source": "canary",
                    "raw_headers": json.dumps(raw_headers, separators=(",", ":")),
                    "status": "ok" if response.is_success else f"http_{response.status_code}",
                }
            )
        run_finished(started, "ok", "captured Anthropic rate headers")
        return "Anthropic rate limit headers captured."
    except Exception as exc:
        run_finished(started, "error", str(exc))
        raise


def seed_demo_data() -> str:
    with get_conn() as conn:
        existing = conn.execute("SELECT COUNT(*) AS count FROM token_usage").fetchone()["count"]
    if existing:
        return "Demo seed skipped; usage rows already exist."

    providers = [
        ("openai", ["gpt-4.1", "gpt-4.1-mini"], ["riverdale", "mycroft", "sap-btp"]),
        ("anthropic", ["claude-sonnet", "claude-haiku"], ["riverdale", "mycroft", "sap-btp"]),
    ]
    now = utcnow().replace(minute=0, second=0)
    for hours_ago in range(24 * 7, 0, -1):
        start = now - timedelta(hours=hours_ago)
        end = start + timedelta(hours=1)
        day_weight = 1 + (6 - start.weekday()) * 0.03
        hour_weight = 0.35 + max(0, 1 - abs(start.hour - 14) / 12)
        for provider, models, projects in providers:
            for project in projects:
                model = random.choice(models)
                base = random.randint(800, 3800)
                total = int(base * day_weight * hour_weight)
                prompt = int(total * random.uniform(0.55, 0.8))
                completion = total - prompt
                cached = int(prompt * random.uniform(0.05, 0.28))
                upsert_token_usage(
                    {
                        "bucket_start": iso(start),
                        "bucket_end": iso(end),
                        "provider": provider,
                        "project_id": project,
                        "api_key_id": f"demo-{project}",
                        "workspace_id": project if provider == "anthropic" else None,
                        "model": model,
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "cached_tokens": cached,
                        "total_tokens": total,
                        "request_count": max(1, total // random.randint(600, 1800)),
                        "source": "demo_seed",
                        "raw_json": "{}",
                    }
                )
        if start.hour == 0:
            for provider, _, projects in providers:
                for project in projects:
                    upsert_cost_usage(
                        {
                            "bucket_start": iso(start),
                            "bucket_end": iso(start + timedelta(days=1)),
                            "provider": provider,
                            "project_id": project,
                            "workspace_id": project if provider == "anthropic" else None,
                            "api_key_id": f"demo-{project}",
                            "line_item": "demo usage",
                            "amount": round(random.uniform(0.45, 4.8), 2),
                            "currency": "usd",
                            "source": "demo_seed",
                            "raw_json": "{}",
                        }
                    )

    observed = iso(utcnow())
    for provider in ("openai", "anthropic"):
        for limit_type, limit in (("requests", 5000), ("tokens", 1_000_000)):
            remaining = int(limit * random.uniform(0.35, 0.92))
            insert_rate_limit(
                {
                    "observed_at": observed,
                    "provider": provider,
                    "model": "demo",
                    "limit_type": limit_type,
                    "remaining": remaining,
                    "limit_value": limit,
                    "reset_at": iso(utcnow() + timedelta(minutes=random.randint(5, 45))),
                    "reset_seconds": None,
                    "source": "demo_seed",
                    "raw_headers": "{}",
                    "status": "demo",
                }
            )
    return "Demo data seeded."


def poll_all_metrics() -> dict[str, Any]:
    messages: list[str] = []
    errors: list[str] = []
    collectors = [
        collect_openai_usage,
        collect_openai_costs,
        collect_anthropic_usage,
        collect_anthropic_costs,
        sniff_openai_rate_limits,
        sniff_anthropic_rate_limits,
    ]
    for collector in collectors:
        try:
            messages.append(collector())
        except Exception as exc:
            errors.append(f"{collector.__name__}: {exc}")

    if os.getenv("TOKEN_DASHY_SEED_DEMO_DATA", "true").lower() == "true":
        messages.append(seed_demo_data())

    try:
        alert_result = evaluate_alerts()
        messages.append(f"Alert evaluation opened {len(alert_result['new_alerts'])} new alerts.")
        errors.extend(alert_result["notification_errors"])
    except Exception as exc:
        errors.append(f"evaluate_alerts: {exc}")

    return {"messages": messages, "errors": errors, "ok": not errors}
