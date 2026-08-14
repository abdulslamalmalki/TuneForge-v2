from types import SimpleNamespace

from app.security.rate_limit import RateLimitRule, SlidingWindowRateLimiter, request_identity


def test_sliding_window_allows_limit_then_returns_retry_time():
    now = [100.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0])
    rule = RateLimitRule("ask", 2, 60)

    first = limiter.check(rule, "client:203.0.113.2")
    second = limiter.check(rule, "client:203.0.113.2")
    blocked = limiter.check(rule, "client:203.0.113.2")

    assert first.allowed and first.remaining == 1
    assert second.allowed and second.remaining == 0
    assert not blocked.allowed and blocked.reset_seconds == 60

    now[0] = 161.0
    renewed = limiter.check(rule, "client:203.0.113.2")
    assert renewed.allowed and renewed.remaining == 1


def test_rate_limit_buckets_are_isolated_by_surface_and_identity():
    limiter = SlidingWindowRateLimiter(clock=lambda: 100.0)
    rule = RateLimitRule("ask", 1, 60)

    assert limiter.check(rule, "client:one").allowed
    assert limiter.check(RateLimitRule("search", 1, 60), "client:one").allowed
    assert limiter.check(rule, "client:two").allowed
    assert not limiter.check(rule, "client:one").allowed


def test_authenticated_identity_never_uses_cookie_or_request_content():
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    principal = SimpleNamespace(user_id=42)
    assert request_identity(request, principal) == "user:42"
    assert request_identity(request, None) == "client:127.0.0.1"
