"""Product telemetry ingest: Flutter events → Loki (privacy-safe)."""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Optional
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

LOKI_URL = os.getenv(
    "LOKI_URL",
    "http://monitoring-loki.monitoring.svc.cluster.local:3100/loki/api/v1/push",
)
ENVIRONMENT = os.getenv("ENVIRONMENT", "preprod")
TELEMETRY_HASH_SALT = os.getenv("TELEMETRY_HASH_SALT", "am-product-telemetry-v1")
TELEMETRY_RATE_LIMIT_PER_MIN = int(os.getenv("TELEMETRY_RATE_LIMIT_PER_MIN", "120"))
TELEMETRY_API_TIMING_SAMPLE = float(os.getenv("TELEMETRY_API_TIMING_SAMPLE", "1.0"))


def _parse_hash_ids(raw: str) -> frozenset[str]:
    """Which id fields to hash. Default keeps user_id RAW so operators can
    filter dashboards by the real user_id/portfolio_id they pass in."""
    items = {p.strip() for p in (raw or "").split(",") if p.strip()}
    return frozenset(items)


# Default: hash anonymous/session ids only. user_id stays raw for filtering.
TELEMETRY_HASH_IDS = _parse_hash_ids(
    os.getenv("TELEMETRY_HASH_IDS", "anon_id,session_id")
)

_ENV_ALIASES = {
    "production": "prod",
    "prod": "prod",
    "preprod": "preprod",
    "staging": "preprod",
    "development": "dev",
    "dev": "dev",
}

_ALLOWED_EVENTS = frozenset(
    {
        "screen_view",
        "screen_timing",
        "widget_timing",
        "api_timing",
        "boot_rum",
        "feature_action",
        "session_start",
        "session_summary",
        "auth_logout",
        "section_transition",
        "feedback_submit",
        "empty_state",
        "client_error",
        "subscription_converted",
    }
)

_ALLOWED_BODY_KEYS = frozenset(
    {
        "event",
        "ts",
        "anon_id",
        "user_id",
        "session_id",
        "platform",
        "env",
        "section",
        "screen",
        "screen_name",
        "route_template",
        "portfolio_id",
        "duration_ms",
        "action",
        "path",
        "method",
        "status",
        "category",
        "tag",
        "widget",
        "operation",
        "technical_area",
        "plan_code",
        "billing_interval",
        "entry_section",
        "entry_screen",
        "entry_source",
        "exit_section",
        "exit_screen",
        "from_section",
        "to_section",
        "experiment_id",
        "flag_key",
        "flag_variant",
        "feedback_score",
        "feedback_category",
        "empty_reason",
        "error_type",
        "props",
    }
)

_PROPS_ALLOWED = frozenset(
    {
        "method",
        "path",
        "status",
        "category",
        "action",
        "tag",
        "totalMs",
        "plan_code",
        "billing_interval",
        "widget",
        "operation",
        "error_type",
        "empty_reason",
        "flag_key",
        "flag_variant",
    }
)

_SENSITIVE_KEY_RE = re.compile(
    r"(password|token|secret|cvv|card|otp|email|phone|jwt|bearer|authorization|ssn)",
    re.I,
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.I,
)

# Simple in-memory rate limit: key → timestamps (epoch seconds)
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _normalize_env(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in {"", "default", "unknown"}:
        key = (ENVIRONMENT or "").strip().lower()
    return _ENV_ALIASES.get(key, key or "unknown")


def _hash_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digest = hashlib.sha256(f"{TELEMETRY_HASH_SALT}:{value}".encode("utf-8")).hexdigest()
    return digest[:16]


def _sanitize_path(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    path = raw.split("?", 1)[0].strip()
    if len(path) > 256:
        path = path[:256]
    return path


def _route_template(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return _UUID_RE.sub("{id}", path)


def _portfolio_id_from_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    # /app/trade/{uuid}/... or /app/portfolio/{uuid}/...
    if len(parts) >= 3 and parts[0] == "app" and parts[1] in {"trade", "portfolio"}:
        candidate = parts[2]
        try:
            UUID(candidate)
            return candidate
        except Exception:
            return None
    return None


def _screen_name(path: Optional[str]) -> Optional[str]:
    tmpl = _route_template(path)
    if not tmpl:
        return None
    return tmpl.rstrip("/").split("/")[-1] or tmpl


def _scrub_props(props: Any) -> Optional[dict[str, Any]]:
    if not isinstance(props, dict):
        return None
    out: dict[str, Any] = {}
    for k, v in props.items():
        key = str(k)
        if key not in _PROPS_ALLOWED or _SENSITIVE_KEY_RE.search(key):
            continue
        if isinstance(v, (dict, list)):
            continue
        if isinstance(v, str):
            if _SENSITIVE_KEY_RE.search(v):
                continue
            out[key] = v[:256]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[key] = v
    return out or None


def _allowlist_body(raw: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for k, v in raw.items():
        if k not in _ALLOWED_BODY_KEYS:
            continue
        if _SENSITIVE_KEY_RE.search(k):
            continue
        body[k] = v
    return body


def check_rate_limit(client_key: str) -> bool:
    """Return True if request is allowed."""
    now = time.time()
    window = 60.0
    bucket = _rate_buckets[client_key]
    _rate_buckets[client_key] = [t for t in bucket if now - t < window]
    if len(_rate_buckets[client_key]) >= TELEMETRY_RATE_LIMIT_PER_MIN:
        return False
    _rate_buckets[client_key].append(now)
    return True


class ProductEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str
    ts: Optional[str] = None
    anon_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    platform: Optional[str] = None
    env: Optional[str] = None
    section: Optional[str] = None
    screen: Optional[str] = None
    screen_name: Optional[str] = None
    route_template: Optional[str] = None
    portfolio_id: Optional[str] = None
    duration_ms: Optional[float] = None
    action: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status: Optional[Any] = None
    category: Optional[str] = None
    tag: Optional[str] = None
    widget: Optional[str] = None
    operation: Optional[str] = None
    technical_area: Optional[str] = None
    plan_code: Optional[str] = None
    billing_interval: Optional[str] = None
    entry_section: Optional[str] = None
    entry_screen: Optional[str] = None
    entry_source: Optional[str] = None
    exit_section: Optional[str] = None
    exit_screen: Optional[str] = None
    from_section: Optional[str] = None
    to_section: Optional[str] = None
    experiment_id: Optional[str] = None
    flag_key: Optional[str] = None
    flag_variant: Optional[str] = None
    feedback_score: Optional[float] = None
    feedback_category: Optional[str] = None
    empty_reason: Optional[str] = None
    error_type: Optional[str] = None
    props: Optional[dict[str, Any]] = None


class TelemetryBatch(BaseModel):
    events: list[ProductEvent] = Field(..., min_length=1, max_length=100)


async def push_to_loki(
    *,
    lines: list[tuple[str, str]],
    labels: dict[str, str],
) -> None:
    if not lines:
        return
    streams = [
        {
            "stream": {k: str(v) for k, v in labels.items() if v},
            "values": [[ts_ns, line] for ts_ns, line in lines],
        }
    ]
    payload = {"streams": streams}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(LOKI_URL, json=payload)
            if resp.status_code >= 300:
                logger.warning(
                    "Loki push failed status=%s body=%s",
                    resp.status_code,
                    resp.text[:300],
                )
    except Exception as exc:
        logger.warning("Loki push error: %s", exc)


def _ns_now() -> str:
    return str(int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1e9))


def _parse_ts_ns(ts: Optional[str]) -> str:
    if not ts:
        return _ns_now()
    try:
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return str(int(dt.timestamp() * 1e9))
    except Exception:
        return _ns_now()


def _should_sample(event_name: str) -> bool:
    if event_name != "api_timing":
        return True
    if TELEMETRY_API_TIMING_SAMPLE >= 1.0:
        return True
    # Deterministic-ish sample using time bucket
    return (time.time_ns() % 1000) < int(TELEMETRY_API_TIMING_SAMPLE * 1000)


def sanitize_event(ev: ProductEvent) -> Optional[dict[str, Any]]:
    name = (ev.event or "").strip()
    if name not in _ALLOWED_EVENTS:
        return None
    if not _should_sample(name):
        return None

    raw = ev.model_dump(exclude_none=True)
    body = _allowlist_body(raw)
    body["event"] = name

    env_label = _normalize_env(str(body.get("env") or ENVIRONMENT))
    body["env"] = env_label

    platform = (body.get("platform") or "unknown")
    platform = str(platform).strip().lower()
    if platform not in {"web", "android", "ios"}:
        platform = "unknown"
    body["platform"] = platform

    screen = _sanitize_path(body.get("screen") or body.get("path"))
    if screen:
        body["screen"] = screen
        body["route_template"] = body.get("route_template") or _route_template(screen)
        body["screen_name"] = body.get("screen_name") or _screen_name(screen)
        if not body.get("portfolio_id"):
            pid = _portfolio_id_from_path(screen)
            if pid:
                body["portfolio_id"] = pid

    if body.get("path"):
        body["path"] = _sanitize_path(str(body["path"]))

    for id_key in ("user_id", "anon_id", "session_id"):
        if id_key not in body:
            continue
        if id_key in TELEMETRY_HASH_IDS:
            hashed = _hash_id(str(body[id_key]))
            if hashed:
                body[id_key] = hashed
            else:
                body.pop(id_key, None)
        else:
            # Kept raw (e.g. user_id) so dashboards can filter by the real value.
            val = str(body[id_key]).strip()
            if val:
                body[id_key] = val[:128]
            else:
                body.pop(id_key, None)

    if "props" in body:
        scrubbed = _scrub_props(body["props"])
        if scrubbed:
            body["props"] = scrubbed
        else:
            body.pop("props", None)

    # Truncate free-form strings
    for k, v in list(body.items()):
        if isinstance(v, str) and len(v) > 256 and k not in {"route_template", "screen"}:
            body[k] = v[:256]

    return body


async def ingest_product_events(batch: TelemetryBatch) -> dict[str, Any]:
    accepted = 0
    rejected = 0
    by_stream: dict[tuple[str, str, str], list[tuple[str, str]]] = {}

    for ev in batch.events:
        body = sanitize_event(ev)
        if not body:
            rejected += 1
            continue

        name = body["event"]
        platform = body["platform"]
        env_label = body["env"]
        line = json.dumps(body, default=str, separators=(",", ":"))
        by_stream.setdefault((name, platform, env_label), []).append(
            (_parse_ts_ns(ev.ts), line)
        )
        accepted += 1

    for (event_name, platform, env_label), lines in by_stream.items():
        await push_to_loki(
            lines=lines,
            labels={
                "job": "am-product-telemetry",
                "app": "am-modern-ui",
                "event": event_name,
                "platform": platform,
                "env": env_label,
                "application": "am-modern-ui",
            },
        )

    return {"status": "accepted", "accepted": accepted, "rejected": rejected}
