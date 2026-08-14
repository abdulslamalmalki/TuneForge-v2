"""Public/API boundaries for explicitly unreviewed trusted-web research."""

from app.research import trusted_web


def _view() -> trusted_web.TrustedWebResearchView:
    return trusted_web.TrustedWebResearchView(
        run_id=42,
        query="What does PBO do in BIOS?",
        status="succeeded",
        notice="Live official-source research is not reviewer-approved TuneForge knowledge.",
        sources=(
            trusted_web.TrustedWebSourceView(
                publisher_id="amd",
                publisher_name="AMD",
                title="AMD PBO documentation",
                source_url="https://www.amd.com/en/resources/pbo.html",
                evidence_kind="official_page_excerpt",
                excerpt_text="Precision Boost Overdrive extends automatic boost behavior.",
                content_sha256="a" * 64,
                retrieved_at="2026-08-14T00:00:00+00:00",
            ),
        ),
    )


def test_public_research_api_is_explicit_and_structured(client, monkeypatch):
    monkeypatch.setattr(trusted_web, "research", lambda session, question: _view())

    response = client.post(
        "/api/v1/interface/trusted-web-research",
        params={"question": "What does PBO do in BIOS?"},
        headers={"X-TuneForge-Request": "1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["run_id"] == 42
    assert body["sources"][0]["publisher_id"] == "amd"
    assert body["sources"][0]["evidence_kind"] == "official_page_excerpt"
    assert "reviewer-approved" in body["notice"]


def test_ask_page_separates_live_research_from_verified_answer(client, monkeypatch):
    monkeypatch.setattr(trusted_web, "research", lambda session, question: _view())

    response = client.post(
        "/answer",
        data={"setting": "What does PBO do in BIOS?", "trusted_web": "1"},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 200
    assert "TuneForge cannot verify this answer yet" in response.text
    assert "Live trusted-source research" in response.text
    assert "Not reviewed by TuneForge" in response.text
    assert "AMD PBO documentation" in response.text
    assert "Precision Boost Overdrive extends automatic boost behavior." in response.text


def test_read_only_answer_link_never_invokes_external_research(client, monkeypatch):
    called = False

    def forbidden_research(_session, _question):
        nonlocal called
        called = True
        raise AssertionError("GET must not invoke external research")

    monkeypatch.setattr(trusted_web, "research", forbidden_research)
    response = client.get(
        "/answer",
        params={"setting": "Unknown BIOS question", "trusted_web": "1"},
    )

    assert response.status_code == 200
    assert not called


def test_research_endpoint_schema_is_published(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/interface/trusted-web-research"]["post"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/TrustedWebResearchOut")
    assert client.get("/api/v1/interface/trusted-web-research").status_code == 405
