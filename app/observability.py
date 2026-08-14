"""Privacy-safe structured logging, request correlation, metrics, and error hooks."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

_REQUEST_ID = contextvars.ContextVar("tuneforge_request_id", default="-")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_METRIC_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PROCESS_STARTED = time.time()


@dataclass
class _Timing:
    count: int = 0
    total_ms: float = 0.0
    maximum_ms: float = 0.0


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._timings: dict[tuple[str, tuple[tuple[str, str], ...]], _Timing] = {}

    def increment(self, name: str, value: int = 1, **labels: object) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe_ms(self, name: str, duration_ms: float, **labels: object) -> None:
        key = self._key(name, labels)
        with self._lock:
            timing = self._timings.setdefault(key, _Timing())
            timing.count += 1
            timing.total_ms += max(0.0, duration_ms)
            timing.maximum_ms = max(timing.maximum_ms, duration_ms)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            timings = [
                {
                    "name": name,
                    "labels": dict(labels),
                    "count": timing.count,
                    "average_ms": round(timing.total_ms / timing.count, 3),
                    "maximum_ms": round(timing.maximum_ms, 3),
                }
                for (name, labels), timing in sorted(self._timings.items())
                if timing.count
            ]
        return {
            "process_started_at": datetime.fromtimestamp(_PROCESS_STARTED, UTC).isoformat(),
            "uptime_seconds": max(0, int(time.time() - _PROCESS_STARTED)),
            "counters": counters,
            "timings": timings,
        }

    def reset(self) -> None:
        """Test-only process reset; production code never clears operational history."""

        with self._lock:
            self._counters.clear()
            self._timings.clear()

    @staticmethod
    def _key(
        name: str, labels: Mapping[str, object]
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        if _METRIC_PATTERN.fullmatch(name) is None:
            raise ValueError("Metric names must be stable lowercase identifiers.")
        if any(_METRIC_PATTERN.fullmatch(str(key)) is None for key in labels):
            raise ValueError("Metric label names must be stable lowercase identifiers.")
        normalized = tuple(
            sorted(
                (str(key)[:40], str(value)[:80])
                for key, value in labels.items()
                if value is not None
            )
        )
        return name, normalized


metrics = MetricsRegistry()


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _validated_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        token = _REQUEST_ID.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                metrics.increment("http_request_exceptions_total", method=request.method)
                raise
            route = getattr(request.scope.get("route"), "path", "unmatched")
            duration_ms = (time.perf_counter() - started) * 1000
            labels = {
                "method": request.method,
                "route": route,
                "status_class": f"{response.status_code // 100}xx",
            }
            metrics.increment("http_requests_total", **labels)
            metrics.observe_ms(
                "http_request_duration", duration_ms, method=request.method, route=route
            )
            response.headers.setdefault("X-Request-ID", request_id)
            log_event(
                logging.getLogger("tuneforge.http"),
                "request_completed",
                method=request.method,
                route=route,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 3),
            )
            return response
        finally:
            _REQUEST_ID.reset(token)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "request_id": getattr(record, "request_id", None) or current_request_id(),
            "message": record.getMessage(),
        }
        fields = getattr(record, "observability_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id()
        return True


ErrorReporter = Callable[[Exception, Mapping[str, object]], None]
_error_reporter: ErrorReporter | None = None
_reporter_lock = threading.Lock()


def set_error_reporter(reporter: ErrorReporter | None) -> None:
    global _error_reporter
    with _reporter_lock:
        _error_reporter = reporter


def report_exception(error: Exception, **context: object) -> None:
    with _reporter_lock:
        reporter = _error_reporter
    if reporter is None:
        return
    safe_context = {key: _safe_value(value) for key, value in context.items()}
    try:
        reporter(error, safe_context)
    except Exception:
        logging.getLogger(__name__).exception("error reporting hook failed")


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields) -> None:
    safe_fields = {key: _safe_value(value) for key, value in fields.items()}
    logger.log(
        level,
        event,
        extra={"event": event, "observability_fields": safe_fields},
    )


def current_request_id() -> str:
    return _REQUEST_ID.get()


def _validated_request_id(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]
