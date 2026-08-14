import pytest
from fastapi.testclient import TestClient

from app import config
from app.auth.middleware import AuthBoundaryMiddleware
from app.db.models import CanonicalClassificationEvent
from app.db.session import SessionLocal
from app.main import create_app
from app.web.routes.auth import _safe_next

ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "this is a strong local password"


def _required_client(monkeypatch) -> TestClient:
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    return TestClient(create_app())


def _setup(client: TestClient, *, username: str = "owner"):
    return client.post(
        "/setup",
        data={
            "display_name": "TuneForge Owner",
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
            "next": "/admin",
        },
        headers=ORIGIN,
        follow_redirects=False,
    )


def test_public_product_stays_open_while_curator_surfaces_require_setup(monkeypatch):
    with _required_client(monkeypatch) as client:
        public = client.get("/", follow_redirects=False)
        protected = client.get("/admin", follow_redirects=False)
        internal_api = client.get("/api/v1/manuals")
        recommendation_api = client.get("/api/v1/recommendation-claims")
        recommendation_page = client.get("/recommendation-claims", follow_redirects=False)
        release_api = client.get("/api/v1/release-readiness")
        interface_api = client.get("/api/v1/interface/questions")
        library_page = client.get("/library")
        library_api = client.get("/api/v1/library/settings")
        comparison = client.get("/compare")
        submission = client.get("/upload-manual")
        route_api = client.get(
            "/api/v1/interface/route", params={"question": "What is PBO Scalar?"}
        )

    assert public.status_code == 303
    assert public.headers["location"] == "/library"
    assert protected.status_code == 303
    assert protected.headers["location"].startswith("/setup?next=")
    assert internal_api.status_code == 503
    assert recommendation_api.status_code == 503
    assert recommendation_page.status_code == 303
    assert recommendation_page.headers["location"].startswith("/setup?next=")
    assert release_api.status_code == 503
    assert interface_api.status_code == 200
    assert library_page.status_code == 200
    assert library_api.status_code == 200
    assert comparison.status_code == 200
    assert submission.status_code == 200
    assert route_api.status_code == 200
    assert public.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in public.headers["content-security-policy"]


def test_non_core_public_workflows_move_behind_the_admin_boundary(monkeypatch):
    app = create_app()
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    with TestClient(app) as owner:
        _setup(owner)
    with TestClient(app) as anonymous:
        guided = anonymous.get("/assistant", follow_redirects=False)
        system = anonymous.get("/system-context", follow_redirects=False)

    assert guided.status_code == 303
    assert guided.headers["location"].startswith("/login?next=")
    assert system.status_code == 303
    assert system.headers["location"].startswith("/login?next=")


def test_first_run_setup_creates_secure_session_and_named_audit_actor(monkeypatch):
    with _required_client(monkeypatch) as client:
        rejected = client.post(
            "/setup",
            data={
                "display_name": "TuneForge Owner",
                "username": "owner",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
            },
            follow_redirects=False,
        )
        created = _setup(client)
        dashboard = client.get("/admin")
        canonical = client.post(
            "/canonical-settings",
            data={
                "name": "Authenticated Test Setting",
                "description": "An identity created by a signed-in curator.",
                "aliases": "",
            },
            headers=ORIGIN,
            follow_redirects=False,
        )

    assert rejected.status_code == 403
    assert created.status_code == 303
    cookie = created.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert dashboard.status_code == 200
    assert "TuneForge Owner" in dashboard.text
    assert "Sign out" in dashboard.text
    assert canonical.status_code == 303
    with SessionLocal() as session:
        event = (
            session.query(CanonicalClassificationEvent)
            .order_by(CanonicalClassificationEvent.id.desc())
            .first()
        )
        assert event is not None
        assert event.actor == "owner"


def test_login_is_generic_and_preserves_only_safe_local_return_paths(monkeypatch):
    app = create_app()
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    with TestClient(app) as owner:
        _setup(owner)
    with TestClient(app) as anonymous:
        wrong = anonymous.post(
            "/login",
            data={"username": "owner", "password": "wrong password", "next": "//evil.test"},
            headers=ORIGIN,
        )
        signed_in = anonymous.post(
            "/login",
            data={"username": "owner", "password": PASSWORD, "next": "//evil.test"},
            headers=ORIGIN,
            follow_redirects=False,
        )

    assert wrong.status_code == 400
    assert "Username or password is incorrect" in wrong.text
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/admin"


def test_reviewer_can_curate_but_cannot_manage_accounts(monkeypatch):
    app = create_app()
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    with TestClient(app) as owner:
        _setup(owner)
        created = owner.post(
            "/admin/users",
            data={
                "display_name": "Evidence Reviewer",
                "username": "reviewer",
                "role": "reviewer",
                "password": "reviewer password is long enough",
                "password_confirm": "reviewer password is long enough",
            },
            headers=ORIGIN,
            follow_redirects=False,
        )
        assert created.status_code == 303

    with TestClient(app) as reviewer:
        login = reviewer.post(
            "/login",
            data={
                "username": "reviewer",
                "password": "reviewer password is long enough",
                "next": "/review",
            },
            headers=ORIGIN,
            follow_redirects=False,
        )
        queue = reviewer.get("/review")
        accounts = reviewer.get("/admin/users")

    assert login.headers["location"] == "/review"
    assert queue.status_code == 200
    assert accounts.status_code == 403


def test_unauthenticated_internal_api_is_401_after_setup(monkeypatch):
    app = create_app()
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    with TestClient(app) as owner:
        _setup(owner)
    with TestClient(app) as anonymous:
        response = anonymous.get("/api/v1/manuals")
        unsafe = anonymous.post(
            "/api/v1/canonical-suggestions/refresh",
            headers={"X-TuneForge-Request": "1"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Cookie"
    assert unsafe.status_code == 401


def test_public_authorization_allowlist_is_method_aware():
    assert AuthBoundaryMiddleware._is_public("GET", "/library/canonical/example")
    assert not AuthBoundaryMiddleware._is_public("POST", "/library/canonical/example")
    assert AuthBoundaryMiddleware._is_public("POST", "/answer")
    assert not AuthBoundaryMiddleware._is_public("PUT", "/answer")


@pytest.mark.parametrize(
    "value",
    ("//evil.test", "/\\evil.test", "https://evil.test", "/admin\nlocation:evil"),
)
def test_return_path_rejects_browser_url_confusion(value):
    assert _safe_next(value) == "/admin"


def test_production_responses_enable_transport_and_sensitive_cache_protection(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "TRUSTED_HOSTS", ("testserver",))
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)

    with TestClient(create_app()) as client:
        health = client.get("/api/v1/health")
        answer = client.get("/answer")

    assert health.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert health.headers["cross-origin-opener-policy"] == "same-origin"
    assert answer.headers["cache-control"] == "no-store"


def test_login_rate_limit_is_generic_and_returns_retry_headers(monkeypatch):
    app = create_app()
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    with TestClient(app) as owner:
        _setup(owner)

    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "RATE_LIMIT_LOGIN", "2/60")
    with TestClient(app) as anonymous:
        responses = [
            anonymous.post(
                "/login",
                data={"username": "owner", "password": "incorrect password"},
                headers=ORIGIN,
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [400, 400, 429]
    assert responses[2].text == "Too many requests. Try again later."
    assert responses[2].headers["ratelimit-limit"] == "2"
    assert responses[2].headers["ratelimit-remaining"] == "0"
    assert int(responses[2].headers["retry-after"]) >= 1


def test_only_expensive_library_search_is_rate_limited(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "RATE_LIMIT_SEARCH", "1/60")
    with TestClient(create_app()) as client:
        ordinary = client.get("/library")
        first = client.get("/library", params={"q": "PBO"})
        blocked = client.get("/library", params={"q": "PBO"})

    assert ordinary.status_code == 200
    assert "ratelimit-limit" not in ordinary.headers
    assert first.status_code == 200
    assert first.headers["ratelimit-remaining"] == "0"
    assert blocked.status_code == 429


def test_public_upload_limit_runs_before_pdf_processing(monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "RATE_LIMIT_PUBLIC_UPLOAD", "1/3600")
    with TestClient(create_app()) as client:
        first = client.post("/upload-manual", data={})
        blocked = client.post("/upload-manual", data={})

    assert first.status_code == 400
    assert blocked.status_code == 429
