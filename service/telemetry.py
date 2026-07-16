"""Product telemetry ingest: Flutter events → Loki."""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

LOKI_URL = os.getenv("LOKI_URL", "http://monitoring-loki.monitoring.svc.cluster.local:3100/loki/api/v1/push")
ENVIRONMENT = os.getenv("ENVIRONMENT", "preprod")

_ENV_ALIASES = {
    "production": "prod",
    "prod": "prod",
    "preprod": "preprod",
    "staging": "preprod",
    "development": "dev",
    "dev": "dev",
}


def _normalize_env(raw: str) -> str:
    """Map client/server env strings onto Grafana Product Env values."""
    key = (raw or "").strip().lower()
    # Flutter falls back to "default" when build env is unset — use server ENVIRONMENT.
    if key in {"", "default", "unknown"}:
        key = (ENVIRONMENT or "").strip().lower()
    return _ENV_ALIASES.get(key, key or "unknown")


_ALLOWED_EVENTS = frozenset(
    {
        "screen_view",
        "api_timing",
        "boot_rum",
        "feature_action",
        "session_start",
        "auth_logout",
    }
)


class ProductEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str
    ts: Optional[str] = None
    anon_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    platform: Optional[str] = None
    env: Optional[str] = None
    section: Optional[str] = None
    screen: Optional[str] = None
    duration_ms: Optional[float] = None
    # Flattened RUM / action fields (also mirrored under props)
    action: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status: Optional[Any] = None
    category: Optional[str] = None
    tag: Optional[str] = None
    props: Optional[dict[str, Any]] = None


class TelemetryBatch(BaseModel):
    events: list[ProductEvent] = Field(..., min_length=1, max_length=100)


async def push_to_loki(
    *,
    lines: list[tuple[str, str]],
    labels: dict[str, str],
) -> None:
    """Push nanosecond-timestamped log lines to Loki."""
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
    # Use timezone-aware UTC. Naive utcnow().timestamp() is wrong when TZ!=UTC
    # (pod TZ=Asia/Kolkata shifts Loki timestamps ~5.5h behind → reject_old_samples).
    return str(int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1e9))


def _parse_ts_ns(ts: Optional[str]) -> str:
    if not ts:
        return _ns_now()
    try:
        # Accept ISO-8601
        cleaned = ts.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return str(int(dt.timestamp() * 1e9))
    except Exception:
        return _ns_now()


async def ingest_product_events(batch: TelemetryBatch) -> dict[str, Any]:
    """Validate and push product events to Loki (streams keyed by event+platform)."""
    accepted = 0
    rejected = 0
    # Key: (event, platform, env) → lines — platform/env as Loki labels for dashboards
    by_stream: dict[tuple[str, str, str], list[tuple[str, str]]] = {}

    for ev in batch.events:
        name = (ev.event or "").strip()
        if name not in _ALLOWED_EVENTS:
            rejected += 1
            continue

        body = ev.model_dump(exclude_none=True)
        if "env" not in body or not body["env"]:
            body["env"] = ENVIRONMENT
        # Normalize for Grafana Env filter (prod/preprod/dev); keep body.env as sent.
        env_label = _normalize_env(str(body.get("env") or ENVIRONMENT))
        body["env"] = env_label
        platform = (ev.platform or body.get("platform") or "unknown").strip().lower()
        if platform not in {"web", "android", "ios"}:
            platform = "unknown"
        body["platform"] = platform
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
