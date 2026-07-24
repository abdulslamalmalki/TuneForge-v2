"""Citation-safe envelopes for the future optional model interface."""

import json

from sqlalchemy import delete

from app.db.models import (
    ExtractionRun,
    ManualApplicability,
    ManualApplicabilityTarget,
    Setting,
)
from app.db.session import SessionLocal
from app.domain import canonical_settings, review

SOURCE_DATA = {
    "publisher_id": "amd",
    "title": "Ryzen Master User Guide",
    "source_url": "https://docs.amd.com/r/en-US/68886-ryzen-master-user-guide",
    "document_id": "68886",
    "document_version": "3.1.0",
    "published_on": "2026-05-20",
    "language": "en",
}


def _create_identity(client):
    response = client.post(
        "/api/v1/canonical-settings",
        json={
            "name": "Precision Boost Overdrive",
            "description": "AMD automatic boost control.",
            "aliases": ["AMD Overclocking PBO"],
        },
    )
    assert response.status_code == 200
    return response.json()["canonical_id"]


def _seed_supported_claim(client, pdf_bytes_factory):
    canonical_id = _create_identity(client)
    source = client.post(
        "/api/v1/evidence-sources",
        files={
            "file": (
                "interface-source.pdf",
                pdf_bytes_factory(
                    texts=[
                        "6.3.2 Precision Boost Overdrive\n"
                        + ("PBO can operate up to motherboard limits. " * 12)
                    ]
                ),
                "application/pdf",
            )
        },
        data=SOURCE_DATA,
    ).json()
    source = client.post(
        f"/api/v1/evidence-sources/{source['id']}/review",
        json={"decision": "approved", "note": "Official source reviewed."},
    ).json()
    passage = source["passages"][0]
    claim_text = "PBO can operate up to motherboard limits."
    claim = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": claim_text,
            "claim_type": "behavior",
            "passage_ids": [passage["id"]],
            "canonical_ids": [canonical_id],
        },
    ).json()
    approved = client.post(
        f"/api/v1/guidance-claims/{claim['id']}/review",
        json={"decision": "approved", "note": "Exact factual claim reviewed."},
    )
    assert approved.status_code == 200
    return canonical_id, claim["id"], passage["id"], claim_text


def _register_pointer(client, canonical_ids):
    response = client.post(
        "/api/v1/referral-pointers",
        json={
            "publisher_id": "amd",
            "title": "Ryzen Master User Guide",
            "source_url": SOURCE_DATA["source_url"],
            "document_id": "68886",
            "document_version": "3.1.0",
            "canonical_ids": canonical_ids,
            "review_note": "Official pointer metadata reviewed.",
        },
    )
    assert response.status_code == 200


def _mark_canonical_observed_in_am5(client, pdf_bytes_factory, canonical_id):
    uploaded = client.post(
        "/manuals",
        files={
            "file": (
                "guided-am5-manual.pdf",
                pdf_bytes_factory(texts=["AM5 BIOS setting source text. " * 20]),
                "application/pdf",
            )
        },
        data={
            "vendor": "ASUS",
            "model_name": "AMD AM5 Series",
            "document_title": "AMD AM5 BIOS Manual",
            "eligibility_workflow": "legacy",
        },
        follow_redirects=False,
    )
    manual_id = int(uploaded.headers["location"].rsplit("/", 1)[1])
    with SessionLocal() as session:
        applicability = session.get(ManualApplicability, manual_id)
        applicability.scope = "platform"
        applicability.model_status = "broad"
        applicability.detected_model_id = None
        applicability.warnings_json = json.dumps([])
        session.execute(
            delete(ManualApplicabilityTarget).where(
                ManualApplicabilityTarget.manual_id == manual_id
            )
        )
        session.add(
            ManualApplicabilityTarget(
                manual_id=manual_id,
                ordinal=0,
                platform_id="amd-am5",
                chipset_family_id=None,
                chipset_id=None,
                motherboard_model_id=None,
            )
        )
        run = session.query(ExtractionRun).filter_by(manual_id=manual_id).one()
        setting = Setting(
            manual_id=manual_id,
            extraction_run_id=run.id,
            extracted_name="Precision Boost Overdrive",
            extracted_description="Controls documented automatic boost behavior.",
            page_number=10,
            status="draft",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(setting)
        session.commit()
        review.approve_setting(session, setting)
        canonical_settings.map_setting(
            session,
            setting,
            canonical_id=canonical_id,
            note="Reviewed AM5 observation for guided-answer testing.",
        )


def _all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _all_keys(item)


def test_supported_envelope_contains_exact_claim_and_metadata_only(client, pdf_bytes_factory):
    canonical_id, claim_id, passage_id, claim_text = _seed_supported_claim(
        client, pdf_bytes_factory
    )
    response = client.get(
        "/api/v1/interface/presentation",
        params={"setting": canonical_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "1.0"
    assert body["outcome"] == "supported"
    assert body["resolution_status"] == "resolved"
    assert body["recommendation_status"] == "not_available"
    assert body["refusal_message"] is None
    assert body["referral_pointers"] == []
    assert len(body["statements"]) == 1
    statement = body["statements"][0]
    assert statement["claim_id"] == claim_id
    assert statement["text"] == claim_text
    assert statement["citations"][0]["passage_id"] == passage_id
    assert statement["citations"][0]["source_url"] == SOURCE_DATA["source_url"]
    assert "passage_text" not in set(_all_keys(body))
    assert "review_note" not in set(_all_keys(body))


def test_resolved_identity_without_claims_returns_refusal_and_safe_pointer(client):
    canonical_id = _create_identity(client)
    _register_pointer(client, [canonical_id])
    response = client.get(
        "/api/v1/interface/presentation",
        params={"setting": "AMD Overclocking PBO"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused"
    assert body["resolution_status"] == "resolved"
    assert body["resolved_by"] == "reviewed_alias"
    assert body["statements"] == []
    assert body["refusal_message"] == body["scope_notice"]
    assert len(body["referral_pointers"]) == 1
    pointer = body["referral_pointers"][0]
    assert set(pointer) == {
        "pointer_id",
        "publisher_id",
        "publisher_name",
        "title",
        "source_url",
        "document_id",
        "document_version",
    }
    gap = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert gap["reason"] == "no_accepted_guidance_claims"


def test_unresolved_input_returns_structured_refusal_and_general_pointer(client):
    _register_pointer(client, [])
    response = client.get(
        "/api/v1/interface/presentation",
        params={"setting": "best settings for my CPU"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused"
    assert body["resolution_status"] == "unresolved"
    assert body["canonical_id"] is None
    assert body["statements"] == []
    assert "No exact canonical setting" in body["refusal_message"]
    assert "will not use model memory" in body["recommendation_notice"]
    assert len(body["referral_pointers"]) == 1
    gap = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert gap["reason"] == "no_exact_guidance_match"


def test_invalid_hardware_context_is_validation_error_not_knowledge_gap(client):
    response = client.get(
        "/api/v1/interface/presentation",
        params={
            "setting": "missing setting",
            "motherboard_model_id": "unknown-board",
            "cpu_model_id": "unknown-cpu",
        },
    )
    assert response.status_code == 400
    assert client.get("/api/v1/refusal-gaps/events").json() == []


def test_presentation_schema_is_explicit_in_openapi(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/interface/presentation"]["get"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/InterfacePresentationOut")


def test_guided_questions_focus_supported_answer_on_required_claim_type(client, pdf_bytes_factory):
    canonical_id, _, passage_id, claim_text = _seed_supported_claim(client, pdf_bytes_factory)
    _mark_canonical_observed_in_am5(client, pdf_bytes_factory, canonical_id)
    extra = client.post(
        "/api/v1/guidance-claims",
        json={
            "claim_text": "This extra definition is accepted but not requested.",
            "claim_type": "definition",
            "passage_ids": [passage_id],
            "canonical_ids": [canonical_id],
        },
    ).json()
    client.post(
        f"/api/v1/guidance-claims/{extra['id']}/review",
        json={"decision": "approved", "note": "Factual but outside this question type."},
    )

    questions = client.get("/api/v1/interface/questions").json()
    assert len(questions) == 12
    pbo = next(row for row in questions if row["question_id"] == "pbo-behavior")
    assert pbo["outcome"] == "supported"
    assert pbo["required_claim_types"] == ["behavior"]

    response = client.get("/api/v1/interface/questions/pbo-behavior/answer")
    assert response.status_code == 200
    body = response.json()
    assert body["presentation"]["outcome"] == "supported"
    assert body["presentation"]["resolved_by"] == "guided_question"
    assert [item["claim_type"] for item in body["presentation"]["statements"]] == ["behavior"]
    assert body["presentation"]["statements"][0]["text"] == claim_text
    assert "This extra definition" not in str(body)


def test_guided_unsupported_capability_refuses_without_related_fact_inference(client):
    response = client.get("/api/v1/interface/questions/pbo-scalar-recommendation/answer")
    assert response.status_code == 200
    body = response.json()
    assert body["question"]["capability"] == "recommendation"
    assert body["presentation"]["outcome"] == "refused"
    assert body["presentation"]["resolution_status"] == "capability_unavailable"
    assert body["presentation"]["statements"] == []
    assert "will not invent values" in body["presentation"]["refusal_message"]


def test_guided_answer_page_lists_questions_and_renders_refusal(client):
    page = client.get(
        "/assistant",
        params={"question_id": "bios-version-changes"},
    )
    assert page.status_code == 200
    assert "Guided AM5 answers" in page.text
    assert "What changed in this setting in BIOS version X?" in page.text
    assert "capability unavailable" not in page.text.lower()
    assert "BIOS-version applicability" in page.text
    assert "No language model is connected" in page.text


def test_public_setting_answer_uses_safe_envelope_not_raw_passage(client, pdf_bytes_factory):
    canonical_id, _, _, claim_text = _seed_supported_claim(client, pdf_bytes_factory)
    page = client.get("/answer", params={"setting": canonical_id})
    assert page.status_code == 200
    assert "Evidence available" in page.text
    assert "Precision Boost Overdrive" in page.text
    assert claim_text in page.text
    assert page.text.count(claim_text) == 1
    assert "Advanced Micro Devices, Inc." in page.text
    assert "Immutable evidence passage" not in page.text
    assert "TuneForge source record" not in page.text
    assert "/evidence-sources/" not in page.text


def test_public_question_routes_to_focused_guided_answer(client, pdf_bytes_factory):
    canonical_id, _, _, claim_text = _seed_supported_claim(client, pdf_bytes_factory)
    _mark_canonical_observed_in_am5(client, pdf_bytes_factory, canonical_id)

    page = client.get("/answer", params={"setting": "What does Precision Boost Overdrive do?"})

    assert page.status_code == 200
    assert "Matched a reviewed TuneForge question" in page.text
    assert claim_text in page.text
    assert "Evidence available" in page.text


def test_public_wrapper_is_transparent_and_unknown_gap_keeps_original_question(
    client, pdf_bytes_factory
):
    _seed_supported_claim(client, pdf_bytes_factory)
    supported = client.get("/answer", params={"setting": "What is Precision Boost Overdrive?"})
    refused = client.get("/answer", params={"setting": "What is Imaginary Turbo Mode?"})

    assert supported.status_code == 200
    assert "Interpreted as the setting" in supported.text
    assert "Precision Boost Overdrive" in supported.text
    assert refused.status_code == 200
    gap = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert gap["query_text"] == "What is Imaginary Turbo Mode?"


def test_public_setting_answer_refuses_unknown_input_without_guessing(client):
    page = client.get("/answer", params={"setting": "best settings for everything"})
    assert page.status_code == 200
    assert "TuneForge cannot verify this answer yet" in page.text
    assert "will not guess from model memory" in page.text
    gap = client.get("/api/v1/refusal-gaps/events").json()[0]
    assert gap["surface"] == "guidance"
    assert gap["reason"] == "no_exact_guidance_match"


def test_unknown_guided_question_fails_closed(client):
    api = client.get("/api/v1/interface/questions/not-a-question/answer")
    assert api.status_code == 404
    page = client.get("/assistant", params={"question_id": "not-a-question"})
    assert page.status_code == 404
