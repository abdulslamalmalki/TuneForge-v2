import io
import json

from sqlalchemy import select

from app.db.models import (
    CanonicalIdentityProposal,
    CanonicalMappingSuggestion,
    CanonicalSetting,
    CanonicalSuggestionEvent,
    ExtractionRun,
    Setting,
    SettingCanonicalMapping,
)
from app.domain import (
    canonical_identity_review,
    canonical_settings,
    canonicalization,
    manuals,
)


def _trusted_setting(
    db_session,
    pdf_bytes_factory,
    *,
    vendor: str,
    name: str,
    description: str,
    marker: str,
    status: str = "approved",
    page_review_status: str = "correct",
    extra_pages: int = 0,
):
    vendor_id = vendor.casefold()
    manual = manuals.create_manual(
        db_session,
        io.BytesIO(
            pdf_bytes_factory(
                texts=[
                    f"{marker} official BIOS guide. " * 30,
                    *(
                        f"{marker} official BIOS hierarchy page {page}. " * 20
                        for page in range(2, extra_pages + 2)
                    ),
                ]
            )
        ),
        original_filename=f"{marker}.pdf",
        vendor=vendor,
        model_name="AMD 800 Series",
        document_title=f"{vendor} AMD 800 Series BIOS Guide",
        source_url=f"https://www.{vendor_id}.com/support/{marker}",
        chipset="AMD 800 Series",
    )
    manual.acceptance_reviewed_at = "2026-07-23T00:00:00+00:00"
    manual.acceptance_reviewer = "test-reviewer"
    run = db_session.scalar(
        select(ExtractionRun).where(ExtractionRun.manual_id == manual.id)
    )
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name=name,
        extracted_description=description,
        page_number=1,
        page_review_status=page_review_status,
        status=status,
        created_at="2026-07-23T00:00:00+00:00",
        updated_at="2026-07-23T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    return setting


def _candidate_pair(db_session, pdf_bytes_factory):
    description = "Controls the scalar used by Precision Boost Overdrive."
    identity = canonical_settings.create_identity(
        db_session,
        name="Precision Boost Overdrive Scalar",
    )
    canonical_settings.add_alias(
        db_session,
        identity,
        alias="PBO Scalar",
        vendor_id="asus",
    )
    support = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="MSI",
        name="PBO Scalar",
        description=description,
        marker="msi-support",
    )
    canonical_settings.map_setting(
        db_session,
        support,
        canonical_id=identity.canonical_id,
        note="Official MSI wording reviewed.",
    )
    candidate = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="PBO Scalar",
        description=description,
        marker="asus-candidate",
    )
    return identity, support, candidate


def test_backlog_contains_only_approved_unmapped_settings(
    db_session,
    pdf_bytes_factory,
):
    approved = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="Approved Control",
        description="An approved source setting.",
        marker="approved",
    )
    _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="MSI",
        name="Draft Control",
        description="A draft source setting.",
        marker="draft",
        status="draft",
    )
    mapped = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASRock",
        name="Mapped Control",
        description="An already mapped source setting.",
        marker="mapped",
    )
    identity = canonical_settings.create_identity(db_session, name="Mapped Control")
    canonical_settings.map_setting(
        db_session,
        mapped,
        canonical_id=identity.canonical_id,
        note="Reviewed mapping.",
    )

    evaluation = canonicalization.evaluate_backlog(db_session)

    assert [entry.setting_id for entry in evaluation.entries] == [approved.id]


def test_missing_menu_context_stays_pending_with_audited_evidence(
    db_session,
    pdf_bytes_factory,
):
    identity, support, candidate = _candidate_pair(db_session, pdf_bytes_factory)

    report = canonicalization.run_reviewer(db_session)

    assert report.auto_approved_mappings == ()
    assert report.remaining_manual_review_count == 1
    assert report.pending_suggestion_count == 1
    assert db_session.get(SettingCanonicalMapping, candidate.id) is None
    suggestion = db_session.scalar(
        select(CanonicalMappingSuggestion).where(
            CanonicalMappingSuggestion.setting_id == candidate.id,
            CanonicalMappingSuggestion.canonical_id == identity.canonical_id,
        )
    )
    assert suggestion.status == "pending"
    assert suggestion.match_type == canonicalization.MATCH_CONTEXTUAL
    assert suggestion.confidence_basis_points == 9000
    assert suggestion.engine_version == canonicalization.ENGINE_VERSION
    evidence = json.loads(suggestion.evidence_json)
    assert evidence["automatic_eligible"] is False
    assert evidence["support_setting_ids"] == [support.id]
    assert next(
        signal for signal in evidence["signals"] if signal["key"] == "menu_context"
    )["state"] == "unavailable"
    assert [
        event.action
        for event in db_session.scalars(
            select(CanonicalSuggestionEvent)
            .where(CanonicalSuggestionEvent.suggestion_id == suggestion.id)
            .order_by(CanonicalSuggestionEvent.id)
        )
    ] == ["contextual_generated"]


def test_all_mandatory_signals_can_auto_map_and_reversal_is_audited(
    db_session,
    pdf_bytes_factory,
    monkeypatch,
):
    identity, support, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    monkeypatch.setattr(
        canonicalization,
        "_menu_context",
        lambda setting: (
            "advanced amd overclocking"
            if setting.id in {support.id, candidate.id}
            else None
        ),
    )

    report = canonicalization.run_reviewer(db_session)

    assert [item.setting_id for item in report.auto_approved_mappings] == [candidate.id]
    mapping = db_session.get(SettingCanonicalMapping, candidate.id)
    assert mapping.canonical_id == identity.canonical_id
    assert mapping.mapping_method == canonical_settings.MAPPING_AUTOMATIC
    assert mapping.actor == canonicalization.AUTOMATIC_ACTOR

    canonicalization.reverse_automatic_mapping(
        db_session,
        candidate.id,
        reason="Reviewer found the menu contexts are not equivalent.",
    )
    assert db_session.get(SettingCanonicalMapping, candidate.id) is None
    assert [
        event.action
        for event in canonical_settings.list_mapping_events(db_session, candidate.id)
    ] == ["mapped", "unmapped"]


def test_unverified_support_evidence_cannot_auto_map(
    db_session,
    pdf_bytes_factory,
    monkeypatch,
):
    _, support, candidate = _candidate_pair(db_session, pdf_bytes_factory)
    support.page_review_status = "pending"
    db_session.commit()
    monkeypatch.setattr(canonicalization, "_menu_context", lambda setting: "advanced")

    report = canonicalization.run_reviewer(db_session)

    assert report.auto_approved_mappings == ()
    assert db_session.get(SettingCanonicalMapping, candidate.id) is None


def test_cross_vendor_new_identity_group_remains_human_reviewed_without_menu_paths(
    db_session,
    pdf_bytes_factory,
):
    description = "Controls a documented feature with identical vendor behavior."
    first = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="Shared New Control",
        description=description,
        marker="new-asus",
    )
    second = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="MSI",
        name="Shared New Control",
        description=description,
        marker="new-msi",
    )

    evaluation = canonicalization.evaluate_backlog(db_session)

    assert len(evaluation.proposed_groups) == 1
    group = evaluation.proposed_groups[0]
    assert group.setting_ids == (first.id, second.id)
    assert group.vendor_ids == ("asus", "msi")
    assert group.confidence_basis_points == 9000
    assert group.automatic_eligible is False
    assert any("menu context is unavailable" in reason for reason in group.reasons)
    report = canonicalization.run_reviewer(db_session)
    assert report.new_canonical_settings == ()
    assert db_session.get(CanonicalSetting, group.canonical_id) is None


def test_reviewed_menu_context_is_cited_audited_and_revocable(
    db_session,
    pdf_bytes_factory,
):
    setting = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="Pattern Length",
        description="Specifies the pattern length.",
        marker="menu-context",
        extra_pages=1,
    )

    canonical_identity_review.review_menu_context(
        db_session,
        setting.id,
        menu_path="Advanced > AMD CBS > DDR Training Options",
        source_page_number=1,
        evidence_excerpt="Pattern Length Specifies the pattern length.",
        supporting_citations=(
            (2, "AMD CBS contains the reviewed DDR Training Options hierarchy."),
        ),
        note="Breadcrumb and setting were visually reviewed.",
    )
    view = canonical_identity_review.get_menu_context(db_session, setting.id)
    assert view.menu_path == "Advanced > AMD CBS > DDR Training Options"
    assert view.source_page_number == 1
    assert view.source_url == "https://www.asus.com/support/menu-context"
    assert [(citation.page_number, citation.evidence_excerpt) for citation in view.citations] == [
        (1, "Pattern Length Specifies the pattern length."),
        (2, "AMD CBS contains the reviewed DDR Training Options hierarchy."),
    ]
    assert [
        event.action
        for event in canonical_identity_review.list_menu_context_events(
            db_session,
            setting.id,
        )
    ] == ["approved"]

    canonical_identity_review.revoke_menu_context(
        db_session,
        setting.id,
        reason="The breadcrumb applies to a neighboring subsection.",
    )
    assert canonical_identity_review.get_menu_context(db_session, setting.id).status == (
        canonical_identity_review.MENU_CONTEXT_REVOKED
    )
    assert [
        event.action
        for event in canonical_identity_review.list_menu_context_events(
            db_session,
            setting.id,
        )
    ] == ["approved", "revoked"]


def test_canonical_identity_proposal_requires_menu_evidence_then_approves_atomically(
    db_session,
    pdf_bytes_factory,
):
    description = "Specifies the pattern length."
    first = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASUS",
        name="Pattern Length",
        description=description,
        marker="proposal-asus",
    )
    second = _trusted_setting(
        db_session,
        pdf_bytes_factory,
        vendor="ASRock",
        name="Pattern Length",
        description=description,
        marker="proposal-asrock",
    )

    first_report = canonicalization.run_reviewer(db_session)
    assert first_report.identity_proposals_created_count == 1
    (proposal,) = canonical_identity_review.list_identity_proposals(db_session)
    assert proposal.status == canonical_identity_review.PROPOSAL_PENDING
    assert proposal.approval_ready is False
    assert proposal.confidence_basis_points == 9000

    for setting in (first, second):
        canonical_identity_review.review_menu_context(
            db_session,
            setting.id,
            menu_path="Advanced > AMD CBS > DDR Training Options",
            source_page_number=1,
            evidence_excerpt="Pattern Length Specifies the pattern length.",
        )
    refreshed = canonicalization.run_reviewer(db_session)
    assert refreshed.identity_proposals_refreshed_count == 1
    proposal = canonical_identity_review.get_identity_proposal(
        db_session,
        proposal.proposal_id,
    )
    assert proposal.approval_ready is True
    assert proposal.confidence_basis_points == 10000

    identity = canonical_identity_review.approve_identity_proposal(
        db_session,
        proposal.proposal_id,
        note="Exact behavior and menu hierarchy agree in both official manuals.",
    )
    assert identity.canonical_id == "pattern-length"
    assert identity.status == canonical_settings.IDENTITY_ACTIVE
    assert {
        db_session.get(SettingCanonicalMapping, setting.id).canonical_id
        for setting in (first, second)
    } == {"pattern-length"}
    assert (
        db_session.get(CanonicalIdentityProposal, proposal.proposal_id).status
        == canonical_identity_review.PROPOSAL_APPROVED
    )
    assert [
        event.action
        for event in canonical_identity_review.list_identity_proposal_events(
            db_session,
            proposal.proposal_id,
        )
    ] == ["generated", "refreshed", "approved"]


def test_identity_proposal_rejection_can_be_reopened_without_losing_history(
    db_session,
    pdf_bytes_factory,
):
    description = "Controls a reviewed cross-vendor feature."
    for vendor in ("ASUS", "MSI"):
        _trusted_setting(
            db_session,
            pdf_bytes_factory,
            vendor=vendor,
            name="Shared Review Control",
            description=description,
            marker=f"reopen-{vendor.casefold()}",
        )
    canonicalization.run_reviewer(db_session)
    (proposal,) = canonical_identity_review.list_identity_proposals(db_session)

    canonical_identity_review.reject_identity_proposal(
        db_session,
        proposal.proposal_id,
        reason="The menu hierarchy has not been verified.",
    )
    canonical_identity_review.reopen_identity_proposal(
        db_session,
        proposal.proposal_id,
        reason="New official menu evidence is now available.",
    )

    assert canonical_identity_review.get_identity_proposal(
        db_session,
        proposal.proposal_id,
    ).status == canonical_identity_review.PROPOSAL_PENDING
    assert [
        event.action
        for event in canonical_identity_review.list_identity_proposal_events(
            db_session,
            proposal.proposal_id,
        )
    ] == ["generated", "rejected", "reopened"]
