"""Approved-settings search (FR-24, FR-25): matching, safety, display fields."""

import io

import pytest

from app.db.models import Manual
from app.domain import acceptance, canonical_settings, review
from app.domain import manuals as manuals_domain
from app.domain import settings as settings_domain
from app.domain.extraction_runs import get_run_for_manual
from app.extraction.settings_parser import SettingCandidate
from app.search import query as search_query


def _approved_setting(
    db_session,
    pdf_bytes,
    *,
    name="Ai Overclock Tuner",
    description="Selects the memory overclocking profile.",
    vendor="ASUS",
    model_name="X670",
    page_number=3,
):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes),
        original_filename="manual.pdf",
        vendor=vendor,
        model_name=model_name,
    )
    run = get_run_for_manual(db_session, manual.id)
    (setting,) = settings_domain.create_draft_settings(
        db_session,
        manual_id=manual.id,
        extraction_run_id=run.id,
        candidates=[SettingCandidate(name, description, page_number)],
    )
    db_session.commit()
    review.approve_setting(db_session, setting)
    return setting


def test_matches_name_and_description_with_display_fields(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory())

    for keywords in ("overclock", "memory"):  # name hit and description hit
        (result,) = search_query.search_approved(db_session, keywords)
        assert result.setting_id == setting.id
        assert result.effective_name == "Ai Overclock Tuner"
        assert result.manual_label == "ASUS X670"
        assert result.page_number == 3


def test_only_approved_settings_are_searchable(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory())
    review.reopen_setting(db_session, setting)  # back to draft
    assert search_query.search_approved(db_session, "memory") == []


def test_search_uses_corrected_page_reference(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory(), page_number=3)
    manual = db_session.get(Manual, setting.manual_id)
    acceptance.review_page_reference(db_session, manual, setting.id, page_number=1)
    (result,) = search_query.search_approved(db_session, "memory")
    assert result.page_number == 1


def test_search_matches_effective_not_extracted_values(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory())
    review.reopen_setting(db_session, setting)
    review.edit_draft_setting(
        db_session, setting, name="XMP Profile Selector", description="Chooses the XMP tables."
    )
    review.approve_setting(db_session, setting)

    (result,) = search_query.search_approved(db_session, "XMP")
    assert result.effective_name == "XMP Profile Selector"
    # The pre-edit name is no longer reachable (FR-24: effective values).
    assert search_query.search_approved(db_session, "Tuner") == []


def test_manual_label_falls_back_to_filename(db_session, pdf_bytes_factory):
    _approved_setting(db_session, pdf_bytes_factory(), vendor="", model_name="")
    (result,) = search_query.search_approved(db_session, "memory")
    assert result.manual_label == "manual.pdf"


def test_snippet_highlights_matches_and_escapes_html(db_session, pdf_bytes_factory):
    _approved_setting(
        db_session,
        pdf_bytes_factory(),
        description="Controls the <b>memory</b> profile applied at boot.",
    )
    (result,) = search_query.search_approved(db_session, "memory")
    assert "<mark>memory</mark>" in str(result.snippet)
    assert "&lt;b&gt;" in str(result.snippet)  # description HTML is escaped
    assert "<b>" not in str(result.snippet)


def test_multi_term_queries_require_every_term(db_session, pdf_bytes_factory):
    _approved_setting(db_session, pdf_bytes_factory())
    assert len(search_query.search_approved(db_session, "memory profile")) == 1
    assert search_query.search_approved(db_session, "memory nonexistent") == []


@pytest.mark.parametrize(
    "hostile",
    ['"', 'name:overclock OR "', "NEAR(", "a OR (", "*", ")", '" OR 1=1 --', "-memory"],
)
def test_hostile_queries_never_raise(db_session, pdf_bytes_factory, hostile):
    _approved_setting(db_session, pdf_bytes_factory())
    results = search_query.search_approved(db_session, hostile)
    assert isinstance(results, list)  # sanitized to literal keywords, no MATCH error


def test_blank_queries_return_nothing(db_session):
    assert search_query.search_approved(db_session, "") == []
    assert search_query.search_approved(db_session, '   ""  ') == []


def test_search_exposes_and_filters_confirmed_canonical_mapping(db_session, pdf_bytes_factory):
    setting = _approved_setting(db_session, pdf_bytes_factory())
    identity = canonical_settings.create_identity(
        db_session,
        name="Memory Profile Selection",
    )
    canonical_settings.map_setting(
        db_session,
        setting,
        canonical_id=identity.canonical_id,
    )
    canonical_settings.classify_identity(
        db_session,
        identity,
        category_id="memory",
        risk_level="high",
        risk_rationale="The setting changes memory behavior.",
    )

    (result,) = search_query.search_approved(
        db_session,
        "memory",
        canonical_id=identity.canonical_id,
    )
    assert result.canonical_id == identity.canonical_id
    assert result.canonical_name == "Memory Profile Selection"
    assert result.category_id == "memory"
    assert result.category_name == "Memory"
    assert result.risk_level == "high"
    assert (
        len(
            search_query.search_approved(
                db_session,
                "memory",
                category_id="memory",
                risk_level="high",
            )
        )
        == 1
    )
    assert (
        search_query.search_approved(
            db_session,
            "memory",
            category_id="boot",
        )
        == []
    )
    assert (
        search_query.search_approved(
            db_session,
            "memory",
            canonical_id="different-identity",
        )
        == []
    )


def test_vendor_specific_names_resolve_to_the_same_cited_equivalence_view(
    db_session,
    pdf_bytes_factory,
):
    identity = canonical_settings.create_identity(
        db_session,
        name="PBO Scalar",
        aliases=("Precision Boost Scalar Control",),
    )
    asus = _approved_setting(
        db_session,
        pdf_bytes_factory(texts=["ASUS PBO scalar manual text. " * 20]),
        name="PBO Scalar",
        vendor="ASUS",
        model_name="ROG X870E",
        page_number=12,
    )
    msi = _approved_setting(
        db_session,
        pdf_bytes_factory(texts=["MSI CPU boost scalar manual text. " * 20]),
        name="CPU Boost Scalar",
        vendor="MSI",
        model_name="MEG X870E",
        page_number=27,
    )
    for setting in (asus, msi):
        canonical_settings.map_setting(
            db_session,
            setting,
            canonical_id=identity.canonical_id,
            note="Vendor-equivalent meaning reviewed against the source manual.",
        )

    canonical_result = search_query.search_approved(db_session, "PBO Scalar")[0]
    vendor_result = search_query.search_approved(db_session, "CPU Boost Scalar")[0]
    for result in (canonical_result, vendor_result):
        assert result.canonical_id == "pbo-scalar"
        assert result.canonical_name == "PBO Scalar"
        assert [item.vendor_name for item in result.equivalent_vendor_names] == ["ASUS", "MSI"]
        assert [item.setting_name for item in result.equivalent_vendor_names] == [
            "PBO Scalar",
            "CPU Boost Scalar",
        ]
        assert [item.citations[0].setting_id for item in result.equivalent_vendor_names] == [
            asus.id,
            msi.id,
        ]
        assert [item.citations[0].page_number for item in result.equivalent_vendor_names] == [
            12,
            27,
        ]

    alias_results = search_query.search_approved(db_session, "Precision Boost Scalar Control")
    assert {result.setting_id for result in alias_results} == {asus.id, msi.id}
    assert {result.canonical_id for result in alias_results} == {"pbo-scalar"}


def test_exact_signed_vendor_names_do_not_cross_opposite_canonical_identities(
    db_session,
    pdf_bytes_factory,
):
    positive = canonical_settings.create_identity(
        db_session,
        name="Maximum CPU Boost Clock Override Positive",
    )
    negative = canonical_settings.create_identity(
        db_session,
        name="Maximum CPU Boost Clock Override Negative",
    )
    positive_setting = _approved_setting(
        db_session,
        pdf_bytes_factory(texts=["Official positive boost override documentation. " * 20]),
        name="Max CPU Boost Clock Override(+)",
        vendor="MSI",
        model_name="X870",
    )
    negative_setting = _approved_setting(
        db_session,
        pdf_bytes_factory(texts=["Official negative boost override documentation. " * 20]),
        name="Max CPU Boost Clock Override(-)",
        vendor="MSI",
        model_name="X870",
    )
    canonical_settings.map_setting(
        db_session,
        positive_setting,
        canonical_id=positive.canonical_id,
        note="The official manual identifies the positive override control.",
    )
    canonical_settings.map_setting(
        db_session,
        negative_setting,
        canonical_id=negative.canonical_id,
        note="The official manual identifies the negative override control.",
    )

    positive_results = search_query.search_approved(
        db_session,
        "Max CPU Boost Clock Override(+)",
    )
    negative_results = search_query.search_approved(
        db_session,
        "Max CPU Boost Clock Override(-)",
    )

    assert {result.setting_id for result in positive_results} == {positive_setting.id}
    assert {result.canonical_id for result in positive_results} == {
        positive.canonical_id
    }
    assert {result.setting_id for result in negative_results} == {negative_setting.id}
    assert {result.canonical_id for result in negative_results} == {
        negative.canonical_id
    }
    assert (
        search_query.search_approved(
            db_session,
            "Max CPU Boost Clock Override(+)",
            canonical_id=negative.canonical_id,
        )
        == []
    )
