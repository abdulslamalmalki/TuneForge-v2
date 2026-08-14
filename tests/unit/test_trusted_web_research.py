"""Safe, separately labeled research over allowlisted official websites."""

import hashlib

import pytest
from sqlalchemy import func, select

from app.db.models import (
    EvidenceSource,
    TrustedWebResearchEvent,
    TrustedWebResearchResult,
    TrustedWebResearchRun,
)
from app.research import trusted_web


def _search_html(*urls: tuple[str, str]) -> str:
    return "".join(
        f'<a class="result__a" href="{url}">{title}</a>'
        f'<a class="result__snippet">Official result snippet.</a>'
        for title, url in urls
    )


def test_research_stores_official_excerpt_and_append_only_events(db_session):
    body = b"official page bytes"

    def search_fetch(_url: str) -> str:
        return _search_html(
            ("AMD PBO documentation", "https://www.amd.com/en/resources/pbo.html"),
            ("Community answer", "https://www.reddit.com/r/overclocking/example"),
        )

    def page_fetch(url: str) -> trusted_web.FetchedOfficialPage:
        return trusted_web.FetchedOfficialPage(
            final_url=url,
            title="AMD Precision Boost Overdrive",
            text=(
                "Precision Boost Overdrive extends automatic CPU boost behavior. "
                "Review platform limits before changing PBO settings."
            ),
            content_sha256=hashlib.sha256(body).hexdigest(),
        )

    view = trusted_web.research(
        db_session,
        "What does PBO do in BIOS?",
        search_fetch=search_fetch,
        page_fetch=page_fetch,
    )

    assert view.status == "succeeded"
    assert len(view.sources) == 1
    assert view.sources[0].publisher_id == "amd"
    assert view.sources[0].evidence_kind == "official_page_excerpt"
    assert "Precision Boost Overdrive" in (view.sources[0].excerpt_text or "")
    run = db_session.get(TrustedWebResearchRun, view.run_id)
    assert run is not None
    assert run.result_count == 1
    result = db_session.scalar(
        select(TrustedWebResearchResult).where(TrustedWebResearchResult.run_id == view.run_id)
    )
    assert result is not None
    assert result.content_sha256 == hashlib.sha256(body).hexdigest()
    actions = list(
        db_session.scalars(
            select(TrustedWebResearchEvent.action)
            .where(TrustedWebResearchEvent.run_id == view.run_id)
            .order_by(TrustedWebResearchEvent.id)
        )
    )
    assert actions == ["started", "succeeded"]
    assert db_session.scalar(select(func.count(EvidenceSource.id))) == 0


def test_official_link_survives_page_fetch_failure_without_becoming_excerpt(db_session):
    def page_fetch(_url: str) -> trusted_web.FetchedOfficialPage:
        raise RuntimeError("simulated fetch failure")

    view = trusted_web.research(
        db_session,
        "ASUS UEFI secure boot documentation",
        search_fetch=lambda _url: _search_html(
            ("ASUS Secure Boot", "https://www.asus.com/support/faq/1049829/")
        ),
        page_fetch=page_fetch,
    )

    assert view.status == "succeeded"
    assert view.sources[0].evidence_kind == "official_link_only"
    assert view.sources[0].excerpt_text is None
    assert view.sources[0].content_sha256 is None


def test_non_bios_question_refuses_before_network_and_is_audited(db_session):
    called = False

    def search_fetch(_url: str) -> str:
        nonlocal called
        called = True
        return ""

    view = trusted_web.research(
        db_session,
        "What is the weather tomorrow?",
        search_fetch=search_fetch,
    )

    assert view.status == "refused"
    assert view.sources == ()
    assert not called
    actions = list(
        db_session.scalars(
            select(TrustedWebResearchEvent.action)
            .where(TrustedWebResearchEvent.run_id == view.run_id)
            .order_by(TrustedWebResearchEvent.id)
        )
    )
    assert actions == ["started", "refused"]


def test_allowlist_rejects_lookalikes_credentials_ports_and_http():
    assert trusted_web.is_allowlisted_official_url("https://docs.amd.com/r/en-US/example")
    assert trusted_web.is_allowlisted_official_url("https://www.uefi.org/specifications")
    assert not trusted_web.is_allowlisted_official_url("https://amd.com.evil.example/pbo")
    assert not trusted_web.is_allowlisted_official_url("https://amd.com@evil.example/pbo")
    assert not trusted_web.is_allowlisted_official_url("https://amd.com:444/pbo")
    assert not trusted_web.is_allowlisted_official_url("http://amd.com/pbo")
    assert not trusted_web.is_allowlisted_official_url("https://amd.com/pbo\nhttps://evil.test")
    assert not trusted_web.is_allowlisted_official_url(
        "https://amd.com/" + ("a" * 2100)
    )


def test_search_with_only_nonofficial_results_refuses(db_session):
    view = trusted_web.research(
        db_session,
        "PBO BIOS settings",
        search_fetch=lambda _url: _search_html(
            ("Community PBO", "https://example.com/pbo")
        ),
    )

    assert view.status == "refused"
    assert view.sources == ()
    assert "No allowlisted official source" in view.notice


def test_dns_policy_rejects_private_metadata_and_mixed_answers(monkeypatch):
    private_answers = [
        (0, 0, 0, "", ("169.254.169.254", 443)),
        (0, 0, 0, "", ("10.0.0.5", 443)),
    ]
    monkeypatch.setattr(
        trusted_web.socket, "getaddrinfo", lambda *_args, **_kwargs: private_answers
    )
    with pytest.raises(trusted_web.TrustedWebResearchError, match="public IPs"):
        trusted_web._resolve_public_addresses("https://amd.com/example")

    mixed_answers = [
        (0, 0, 0, "", ("8.8.8.8", 443)),
        (0, 0, 0, "", ("127.0.0.1", 443)),
    ]
    monkeypatch.setattr(trusted_web.socket, "getaddrinfo", lambda *_args, **_kwargs: mixed_answers)
    with pytest.raises(trusted_web.TrustedWebResearchError, match="public IPs"):
        trusted_web._resolve_public_addresses("https://amd.com/example")


def test_https_fetch_connects_to_the_prevalidated_address(monkeypatch):
    connected = []

    class FakeResponse:
        status = 200

        @staticmethod
        def getheader(name):
            return {"Content-Type": "text/html", "Content-Length": "15"}.get(name)

        @staticmethod
        def read(_limit):
            return b"<p>official</p>"

    class FakeConnection:
        def __init__(self, host, address):
            connected.append((host, address))

        def request(self, method, target, headers):
            connected.append((method, target, headers["Accept-Encoding"]))

        @staticmethod
        def getresponse():
            return FakeResponse()

        @staticmethod
        def close():
            return None

    monkeypatch.setattr(
        trusted_web, "_resolve_public_addresses", lambda _url: ("8.8.8.8",)
    )
    monkeypatch.setattr(trusted_web, "_PinnedHTTPSConnection", FakeConnection)

    status, _, content_type, body = trusted_web._pinned_get(
        "https://amd.com/path?q=pbo"
    )

    assert status == 200
    assert content_type == "text/html"
    assert body == b"<p>official</p>"
    assert connected[0] == ("amd.com", "8.8.8.8")
    assert connected[1] == ("GET", "/path?q=pbo", "identity")


def test_every_redirect_is_revalidated_before_network_access(monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        return 302, "https://amd.com.evil.example/internal", "text/html", b""

    monkeypatch.setattr(trusted_web, "_pinned_get", fake_get)

    with pytest.raises(trusted_web.TrustedWebResearchError, match="allowlisted"):
        trusted_web._fetch_official_page("https://amd.com/start")
    assert calls == ["https://amd.com/start"]


def test_search_redirect_cannot_leave_configured_service_host(monkeypatch):
    monkeypatch.setattr(
        trusted_web,
        "_pinned_get",
        lambda _url: (302, "https://evil.example/internal", "text/html", b""),
    )

    with pytest.raises(trusted_web.TrustedWebResearchError, match="trusted host"):
        trusted_web._fetch_pinned_search_html(
            "https://lite.duckduckgo.com/lite/?q=official+bios"
        )
