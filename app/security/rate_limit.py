"""Thread-safe, bounded sliding-window request limiting for one app process."""

from __future__ import annotations

import ipaddress
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import Request

from app import config
from app.observability import log_event, metrics

MAX_BUCKETS = 50_000


@dataclass(frozen=True)
class RateLimitRule:
    surface: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitDecision:
    surface: str
    limit: int
    remaining: int
    reset_seconds: int
    allowed: bool


class SlidingWindowRateLimiter:
    """Limit requests without storing raw tokens, queries, or form content."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], deque[float]] = {}

    def check(self, rule: RateLimitRule, identity: str) -> RateLimitDecision:
        now = self._clock()
        cutoff = now - rule.window_seconds
        key = (rule.surface, identity)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                self._ensure_capacity(now - 86_400)
                key = key if len(self._buckets) < MAX_BUCKETS else (rule.surface, "overflow")
                bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= rule.limit:
                reset = max(1, math.ceil(bucket[0] + rule.window_seconds - now))
                return RateLimitDecision(rule.surface, rule.limit, 0, reset, False)
            bucket.append(now)
            remaining = max(0, rule.limit - len(bucket))
            reset = max(1, math.ceil(bucket[0] + rule.window_seconds - now))
            return RateLimitDecision(rule.surface, rule.limit, remaining, reset, True)

    def _ensure_capacity(self, cutoff: float) -> None:
        if len(self._buckets) < MAX_BUCKETS:
            return
        stale = [key for key, bucket in self._buckets.items() if not bucket or bucket[-1] <= cutoff]
        for key in stale:
            self._buckets.pop(key, None)


def rule_for_request(request: Request) -> RateLimitRule | None:
    if not config.RATE_LIMIT_ENABLED:
        return None
    method = request.method
    path = request.url.path
    policy_name = None
    surface = None
    if method == "POST" and path in {"/login", "/setup"}:
        policy_name, surface = "RATE_LIMIT_LOGIN", "authentication"
    elif method == "POST" and path == "/answer":
        policy_name, surface = "RATE_LIMIT_ASK", "ask"
    elif method == "POST" and path == "/api/v1/interface/trusted-web-research":
        policy_name, surface = "RATE_LIMIT_RESEARCH", "trusted_web_research"
    elif method == "POST" and path == "/upload-manual":
        policy_name, surface = "RATE_LIMIT_PUBLIC_UPLOAD", "public_manual_upload"
    elif method == "POST" and path in {
        "/manuals",
        "/batches",
        "/evidence-sources",
        "/api/v1/manuals",
        "/api/v1/batches",
        "/api/v1/evidence-sources",
    }:
        policy_name, surface = "RATE_LIMIT_CURATOR_UPLOAD", "curator_upload"
    elif method == "GET" and _is_expensive_read(request):
        policy_name, surface = "RATE_LIMIT_SEARCH", "search"
    elif method in {"POST", "PUT", "PATCH", "DELETE"} and (
        path.startswith("/admin/") or path.startswith("/api/v1/")
    ):
        policy_name, surface = "RATE_LIMIT_ADMIN_MUTATION", "admin_mutation"
    if policy_name is None or surface is None:
        return None
    limit, window = config.parse_rate_limit_policy(getattr(config, policy_name), policy_name)
    return RateLimitRule(surface, limit, window)


def request_identity(request: Request, principal) -> str:
    if principal is not None and principal.user_id is not None:
        return f"user:{principal.user_id}"
    value = request.client.host if request.client else "unknown"
    try:
        return f"client:{ipaddress.ip_address(value).compressed}"
    except ValueError:
        return f"client:{value[:128].casefold()}"


def apply_headers(headers, decision: RateLimitDecision) -> None:
    headers["RateLimit-Limit"] = str(decision.limit)
    headers["RateLimit-Remaining"] = str(decision.remaining)
    headers["RateLimit-Reset"] = str(decision.reset_seconds)
    if not decision.allowed:
        metrics.increment("rate_limit_events_total", surface=decision.surface)
        log_event(
            logging.getLogger(__name__),
            "rate_limit_exceeded",
            level=logging.WARNING,
            surface=decision.surface,
            retry_after=decision.reset_seconds,
        )
        headers["Retry-After"] = str(decision.reset_seconds)


def _is_expensive_read(request: Request) -> bool:
    path = request.url.path
    if path == "/answer":
        return bool(request.query_params.get("setting", "").strip())
    if path in {"/library", "/api/v1/library/settings", "/search", "/api/v1/search"}:
        return bool(request.query_params.get("q", "").strip())
    return False
