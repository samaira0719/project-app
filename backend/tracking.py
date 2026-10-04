"""Lightweight in-memory active-session tracking and JSON lifecycle logs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .config import get_settings

router = APIRouter(prefix="/api/usage", tags=["usage"])
logger = logging.getLogger("decidewell.usage")
_SESSION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    ip: str
    session_id: str
    first_seen: datetime
    last_seen: datetime
    heartbeat_count: int = 1


_sessions: dict[tuple[str, str], Session] = {}


def _json_log(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True), flush=True)


def _stored_ip(ip: str) -> str:
    settings = get_settings()
    if not settings.hash_ips:
        return ip
    if not settings.ip_hash_salt:
        raise RuntimeError("IP_HASH_SALT is required when HASH_IPS is enabled")
    return hmac.new(settings.ip_hash_salt.encode(), ip.encode(), hashlib.sha256).hexdigest()


def logged_ip(request: Request) -> str:
    try:
        return _stored_ip(client_ip(request))
    except Exception:
        logger.exception("could not apply configured IP privacy mode")
        return "unavailable"


def _peer_trusted(peer: str | None, trusted: str) -> bool:
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for item in trusted.split(","):
        try:
            if item.strip() and address in ipaddress.ip_network(item.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """Honor proxy headers only when the direct network peer is configured trusted."""
    peer = request.client.host if request.client else None
    if _peer_trusted(peer, get_settings().trusted_proxies):
        headers = request.headers
        # Prefer the hosting proxy's single-address headers. X-Forwarded-For is
        # accepted only from a trusted peer and its leftmost value is the client.
        forwarded = headers.get("x-forwarded-for", "").split(",")
        # The rightmost value is the one appended by the immediate trusted
        # proxy; a caller-supplied leftmost value must not win.
        candidates = (headers.get("cf-connecting-ip"), headers.get("fly-client-ip"),
                      headers.get("x-real-ip"), forwarded[-1] if forwarded else "")
        for value in candidates:
            try:
                return str(ipaddress.ip_address(value.strip())) if value else "unknown"
            except ValueError:
                continue
    try:
        return str(ipaddress.ip_address(peer)) if peer else "unknown"
    except ValueError:
        return "unknown"


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


@router.get("/config")
def tracking_config() -> dict[str, int]:
    return {"heartbeat_interval": max(5, get_settings().heartbeat_interval)}


@router.post("/heartbeat", status_code=204)
async def heartbeat(body: Heartbeat, request: Request) -> Response:
    settings = get_settings()
    if not settings.tracking_enabled:
        return Response(status_code=204)
    if not _SESSION_ID.fullmatch(body.session_id):
        # Explicit defense in depth alongside schema validation.
        return Response(status_code=422)
    ip = logged_ip(request)
    if ip == "unavailable":
        return Response(status_code=204)
    key = (ip, body.session_id)
    now = _now()
    async with _lock:
        current = _sessions.get(key)
        if current:
            current.last_seen = now
            current.heartbeat_count += 1
        else:
            if len(_sessions) >= max(1, settings.tracking_max_active_sessions):
                return Response(status_code=204)
            _sessions[key] = Session(ip, body.session_id, now, now)
            _json_log({"event": "session_start", "ip": ip, "session_id": body.session_id,
                       "ts": now.isoformat()})
    return Response(status_code=204)


async def cleanup_sessions() -> None:
    settings = get_settings()
    cutoff = time.time() - max(1, settings.session_idle_timeout)
    async with _lock:
        expired = [key for key, session in _sessions.items() if session.last_seen.timestamp() < cutoff]
        for key in expired:
            session = _sessions.pop(key)
            ended = _now()
            duration = max(0, int((session.last_seen - session.first_seen).total_seconds()))
            _json_log({"event": "session_end", "ip": session.ip,
                       "session_id": session.session_id, "duration_sec": duration,
                       "heartbeats": session.heartbeat_count,
                       "started": session.first_seen.isoformat(), "ended": ended.isoformat()})


async def cleanup_worker(stop: asyncio.Event) -> None:
    settings = get_settings()
    delay = max(1, settings.tracking_cleanup_interval)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            try:
                await cleanup_sessions()
            except Exception:
                logger.exception("usage session cleanup failed")
