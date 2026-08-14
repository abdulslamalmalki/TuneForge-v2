import json
import logging

from app.observability import (
    JsonLogFormatter,
    MetricsRegistry,
    report_exception,
    set_error_reporter,
)


def test_metrics_snapshot_contains_only_stable_labels_and_aggregates():
    registry = MetricsRegistry()
    registry.increment("provider_presentations_total", provider="openai", status="fallback")
    registry.increment("provider_presentations_total", provider="openai", status="fallback")
    registry.observe_ms("http_request_duration", 10.0, method="GET", route="/library")
    registry.observe_ms("http_request_duration", 30.0, method="GET", route="/library")

    snapshot = registry.snapshot()

    assert snapshot["counters"] == [
        {
            "name": "provider_presentations_total",
            "labels": {"provider": "openai", "status": "fallback"},
            "value": 2,
        }
    ]
    assert snapshot["timings"][0]["count"] == 2
    assert snapshot["timings"][0]["average_ms"] == 20.0
    assert snapshot["timings"][0]["maximum_ms"] == 30.0


def test_json_log_formatter_emits_structured_request_metadata_without_extra_fields():
    record = logging.LogRecord(
        name="tuneforge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_completed",
        args=(),
        exc_info=None,
    )
    record.request_id = "request-1234"
    record.event = "request_completed"
    record.observability_fields = {"route": "/answer", "status_code": 200}

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["request_id"] == "request-1234"
    assert payload["event"] == "request_completed"
    assert payload["route"] == "/answer"
    assert payload["status_code"] == 200


def test_error_reporter_hook_receives_bounded_context_and_never_breaks_request_path():
    captured = []
    set_error_reporter(lambda error, context: captured.append((error, context)))
    try:
        error = RuntimeError("internal failure")
        report_exception(error, route="/manuals/{manual_id}", detail="x" * 500)
        assert captured[0][0] is error
        assert captured[0][1]["route"] == "/manuals/{manual_id}"
        assert len(captured[0][1]["detail"]) == 200

        set_error_reporter(lambda _error, _context: 1 / 0)
        report_exception(error, route="/safe")
    finally:
        set_error_reporter(None)
