import io

import pytest

from app.db.models import ExtractionRun, Setting
from app.domain import canonical_settings, canonical_suggestions, manuals, review
from app.domain.errors import CanonicalSettingError, ReviewActionError
from app.normalization.canonical_settings import canonical_slug, normalize_alias


def _setting(
    db_session,
    pdf_bytes_factory,
    *,
    name="Re-Size BAR Support",
    vendor="ASUS",
    filename="manual.pdf",
    status="approved",
    marker="one",
):
    manual = manuals.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory(texts=[f"Canonical test {marker}. " * 30])),
        original_filename=filename,
        vendor=vendor,
        model_name="AMD 800 Series",
    )
    run = db_session.query(ExtractionRun).filter_by(manual_id=manual.id).one()
    setting = Setting(
        manual_id=manual.id,
        extraction_run_id=run.id,
        extracted_name=name,
        extracted_description="Controls the tested feature.",
        page_number=1,
        status=status,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    db_session.add(setting)
    db_session.commit()
    return setting


def test_alias_normalization_is_conservative_and_slug_is_stable():
    assert normalize_alias("  Re-Size   BAR Support ") == "re size bar support"
    assert normalize_alias("Re_Size BAR Support") == "re_size bar support"
    assert canonical_slug("Resizable BAR Support") == "resizable-bar-support"


def test_identity_deduplicates_display_aliases_with_the_same_normalized_key(db_session):
    identity = canonical_settings.create_identity(
        db_session,
        name="All-Core Curve Optimizer Sign",
        aliases=("All Core Curve Optimizer Sign",),
    )
    detail = canonical_settings.get_detail(db_session, identity.canonical_id)
    assert [alias.normalized_alias for alias in detail.aliases] == ["all core curve optimizer sign"]


def test_exact_alias_suggestion_requires_approved_unmapped_setting(db_session, pdf_bytes_factory):
    identity = canonical_settings.create_identity(
        db_session,
        name="Resizable BAR Support",
        aliases=("Re-Size BAR Support",),
    )
    setting = _setting(db_session, pdf_bytes_factory)

    (suggestion,) = canonical_settings.suggest_for_setting(db_session, setting)
    assert suggestion.canonical_id == identity.canonical_id
    assert suggestion.matched_alias == "Re-Size BAR Support"

    setting.status = "draft"
    db_session.commit()
    assert canonical_settings.suggest_for_setting(db_session, setting) == []


def test_mapping_remapping_and_unmapping_are_audited(db_session, pdf_bytes_factory):
    first = canonical_settings.create_identity(db_session, name="Resizable BAR Support")
    second = canonical_settings.create_identity(db_session, name="Above 4G Decoding")
    setting = _setting(db_session, pdf_bytes_factory)

    assert canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=first.canonical_id,
        note="Exact source label reviewed.",
    )
    mapping = canonical_settings.get_mapping(db_session, setting.id)
    assert mapping.canonical_id == first.canonical_id
    assert mapping.mapping_method == canonical_settings.MAPPING_REVIEWER

    assert canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=second.canonical_id,
        note="Corrected after source comparison.",
    )
    canonical_settings.unmap_setting(
        db_session,
        setting,
        reason="The controls are not semantically equivalent.",
    )
    assert canonical_settings.get_mapping(db_session, setting.id) is None
    assert [
        event.action for event in canonical_settings.list_mapping_events(db_session, setting.id)
    ] == ["mapped", "remapped", "unmapped"]


def test_only_approved_settings_can_be_mapped(db_session, pdf_bytes_factory):
    identity = canonical_settings.create_identity(db_session, name="Fast Boot")
    draft = _setting(
        db_session,
        pdf_bytes_factory,
        name="Fast Boot",
        status="draft",
    )
    with pytest.raises(CanonicalSettingError, match="Only approved"):
        canonical_settings.map_setting(
            db_session,
            draft,
            canonical_id=identity.canonical_id,
        )


def test_mapped_setting_must_be_unmapped_before_reopen(db_session, pdf_bytes_factory):
    identity = canonical_settings.create_identity(db_session, name="Fast Boot")
    setting = _setting(db_session, pdf_bytes_factory, name="Fast Boot")
    canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=identity.canonical_id,
        note="Meaning reviewed before mapping.",
    )

    with pytest.raises(ReviewActionError, match="Remove the canonical mapping"):
        review.reopen_setting(db_session, setting)
    assert setting.status == "approved"
    assert canonical_settings.get_mapping(db_session, setting.id) is not None

    canonical_settings.unmap_setting(
        db_session,
        setting,
        reason="Reopening the source review requires mapping reconsideration.",
    )
    review.reopen_setting(db_session, setting)
    assert setting.status == "draft"


def test_catalog_counts_mappings_and_distinct_vendors(db_session, pdf_bytes_factory):
    identity = canonical_settings.create_identity(db_session, name="Fast Boot")
    asus = _setting(
        db_session,
        pdf_bytes_factory,
        name="Fast Boot",
        vendor="ASUS",
        filename="asus.pdf",
        marker="asus",
    )
    msi = _setting(
        db_session,
        pdf_bytes_factory,
        name="Fast Boot",
        vendor="MSI",
        filename="msi.pdf",
        marker="msi",
    )
    for setting in (asus, msi):
        canonical_settings.map_setting(
            db_session,
            setting,
            canonical_id=identity.canonical_id,
        )

    (row,) = canonical_settings.list_catalog(db_session)
    assert row.mapping_count == 2
    assert row.vendor_count == 2
    detail = canonical_settings.get_detail(db_session, identity.canonical_id)
    assert [item.vendor_id for item in detail.mappings] == ["asus", "msi"]


def test_classification_is_validated_and_append_only_audited(db_session):
    identity = canonical_settings.create_identity(db_session, name="Fast Boot")
    initial = canonical_settings.get_classification(db_session, identity.canonical_id)
    assert (initial.category_id, initial.risk_level) == (
        "uncategorized",
        "unclassified",
    )

    changed = canonical_settings.classify_identity(
        db_session,
        identity,
        category_id="boot",
        risk_level="low",
        risk_rationale="Changes startup behavior but does not alter operating voltage.",
    )
    assert changed
    classification = canonical_settings.get_classification(
        db_session,
        identity.canonical_id,
    )
    assert classification.category_name == "Boot"
    assert classification.risk_level == "low"
    assert [
        event.action
        for event in canonical_settings.list_classification_events(
            db_session,
            identity.canonical_id,
        )
    ] == ["classified", "reclassified"]

    with pytest.raises(CanonicalSettingError, match="risk level"):
        canonical_settings.classify_identity(
            db_session,
            identity,
            category_id="boot",
            risk_level="unsafe",
            risk_rationale="Invalid risk value.",
        )


def test_persistent_exact_alias_queue_accepts_and_dismisses_with_audit(
    db_session,
    pdf_bytes_factory,
):
    identity = canonical_settings.create_identity(
        db_session,
        name="Resizable BAR Support",
        aliases=("Re-Size BAR Support",),
    )
    accepted = _setting(
        db_session,
        pdf_bytes_factory,
        filename="accepted.pdf",
        marker="accepted",
    )
    dismissed = _setting(
        db_session,
        pdf_bytes_factory,
        filename="dismissed.pdf",
        marker="dismissed",
    )
    _setting(
        db_session,
        pdf_bytes_factory,
        name="Re-Size BAR Control",
        filename="near-match.pdf",
        marker="near",
    )

    assert canonical_suggestions.refresh_suggestions(db_session) == 2
    assert canonical_suggestions.refresh_suggestions(db_session) == 0
    queue = canonical_suggestions.list_queue(db_session)
    assert {row.setting_id for row in queue} == {accepted.id, dismissed.id}
    assert {row.match_type for row in queue} == {"exact_alias"}

    accepted_row = next(row for row in queue if row.setting_id == accepted.id)
    canonical_suggestions.accept_suggestion(
        db_session,
        accepted_row.suggestion_id,
        note="Source meaning reviewed.",
    )
    assert canonical_settings.get_mapping(db_session, accepted.id).canonical_id == (
        identity.canonical_id
    )
    assert [
        event.action
        for event in canonical_suggestions.list_events(
            db_session,
            accepted_row.suggestion_id,
        )
    ] == ["generated", "accepted"]

    dismissed_row = next(row for row in queue if row.setting_id == dismissed.id)
    canonical_suggestions.dismiss_suggestion(
        db_session,
        dismissed_row.suggestion_id,
        reason="Source description shows a narrower control.",
    )
    assert canonical_settings.get_mapping(db_session, dismissed.id) is None
    assert canonical_suggestions.status_counts(db_session) == {
        "pending": 0,
        "accepted": 1,
        "dismissed": 1,
    }
    assert canonical_suggestions.refresh_suggestions(db_session) == 0


def test_canonical_identity_deprecation_is_audited_reversible_and_blocks_mapping(
    db_session,
    pdf_bytes_factory,
):
    identity = canonical_settings.create_identity(db_session, name="Fast Boot")
    setting = _setting(
        db_session,
        pdf_bytes_factory,
        name="Fast Boot",
    )

    assert canonical_settings.set_identity_status(
        db_session,
        identity,
        status=canonical_settings.IDENTITY_DEPRECATED,
        reason="Superseded after reviewer correction.",
    )
    with pytest.raises(CanonicalSettingError, match="Deprecated"):
        canonical_settings.map_setting(
            db_session,
            setting,
            canonical_id=identity.canonical_id,
        )
    assert (
        canonical_settings.list_catalog(
            db_session,
            include_deprecated=False,
        )
        == []
    )

    assert canonical_settings.set_identity_status(
        db_session,
        identity,
        status=canonical_settings.IDENTITY_ACTIVE,
        reason="Reviewer confirmed the original identity remains valid.",
    )
    canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=identity.canonical_id,
    )
    assert [
        event.action
        for event in canonical_settings.list_identity_events(
            db_session,
            identity.canonical_id,
        )
    ] == ["created", "deprecated", "reactivated"]


def test_canonical_identity_description_revision_is_audited_and_transactional(
    db_session,
):
    identity = canonical_settings.create_identity(
        db_session,
        name="Core Ratio Extension Mode",
        description="Extracted text with OCR noise.",
    )

    assert canonical_settings.revise_identity_description(
        db_session,
        identity,
        description="Extends the documented maximum CPU core ratio when enabled.",
        reason="Reviewer replaced OCR text with a cited canonical summary.",
        commit=False,
    )
    assert identity.description == ("Extends the documented maximum CPU core ratio when enabled.")
    assert [
        event.action
        for event in canonical_settings.list_identity_events(
            db_session,
            identity.canonical_id,
        )
    ] == ["created", "description_revised"]

    db_session.rollback()

    identity = db_session.get(type(identity), identity.canonical_id)
    assert identity.description == "Extracted text with OCR noise."
    assert [
        event.action
        for event in canonical_settings.list_identity_events(
            db_session,
            identity.canonical_id,
        )
    ] == ["created"]
