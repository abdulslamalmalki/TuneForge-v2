import csv
import io
import json

import pytest

from app.db.models import ManualApplicabilityTarget
from app.domain import hardware as hardware_domain
from app.domain import manuals as manuals_domain
from app.normalization.hardware import (
    CHIPSET_FAMILIES,
    CHIPSETS,
    MODEL_BROAD,
    MODEL_SPECIFIC,
    PLATFORMS,
    SCOPE_CHIPSET,
    SCOPE_CHIPSET_FAMILY,
    SCOPE_CHIPSET_SET,
    SCOPE_MODEL,
    SCOPE_PLATFORM,
    SCOPE_UNKNOWN,
    contract,
    document_type_for_applicability,
    normalize_applicability,
    normalize_hardware,
    normalize_model,
)
from app.normalization.vendors import normalized_key
from tools.audit_hardware_applicability import (
    ApplicabilityAuditInput,
    audit_values,
    build_report,
    staging_inputs,
)


def test_specific_model_matching_respects_vendor_and_aliases():
    result = normalize_model("Micro-Star", "MPG X670E CARBON WIFI Motherboard")
    assert result.status == MODEL_SPECIFIC
    assert result.model_id == "msi-mpg-x670e-carbon-wifi"
    assert result.canonical_name == "MPG X670E CARBON WIFI"
    assert result.vendor_id == "msi"
    assert result.chipset_id == "amd-x670e"
    assert result.platform_id == "amd-am5"

    wrong_vendor = normalize_model("ASUS", "MPG X670E CARBON WIFI")
    assert wrong_vendor.model_id is None

    asus = normalize_model("ASUS", "TUF GAMING X870-PLUS WIFI User Manual")
    assert asus.status == MODEL_SPECIFIC
    assert asus.model_id == "asus-tuf-gaming-x870-plus-wifi"
    assert asus.chipset_id == "amd-x870"
    assert asus.platform_id == "amd-am5"


def test_broad_series_label_is_not_misrepresented_as_a_board_model():
    result = normalize_model(
        "ASUS",
        "PRIME / ProArt / TUF GAMING AMD 800 Series",
    )
    assert result.status == MODEL_BROAD
    assert result.model_id is None
    assert result.canonical_name == "PRIME / ProArt / TUF GAMING AMD 800 Series"

    chipset_set = normalize_model("GIGABYTE", "AMD B550 / A520 Series")
    assert chipset_set.status == MODEL_BROAD
    assert chipset_set.model_id is None
    assert chipset_set.canonical_name == "AMD B550 / A520 Series"


def test_hardware_matching_avoids_overlapping_chipset_tokens():
    result = normalize_hardware("X570S")
    assert result.chipset_ids == ("amd-x570s",)
    assert result.family_ids == ("amd-500-series",)
    assert result.platform_ids == ("amd-am4",)


def test_amd_600_filename_does_not_match_intel_600_family():
    result = normalize_hardware(
        "AMD X670/B650 Series",
        "mb_manual_amd600series-bios_e_0104.pdf",
    )
    assert result.chipset_ids == ("amd-x670", "amd-b650")
    assert result.explicit_family_ids == ("amd-600-series",)
    assert result.platform_ids == ("amd-am5",)


@pytest.mark.parametrize(
    ("model", "chipset", "identity", "document_type", "scope", "targets"),
    [
        (
            "MPG X670E CARBON WIFI",
            "X670E",
            "MSI motherboard user guide",
            "user_manual",
            SCOPE_MODEL,
            ("msi-mpg-x670e-carbon-wifi",),
        ),
        (
            "TUF GAMING X870-PLUS WIFI",
            "AMD X870",
            "TUF GAMING X870-PLUS WIFI User Manual",
            "full_manual",
            SCOPE_MODEL,
            ("asus-tuf-gaming-x870-plus-wifi",),
        ),
        (
            "PRIME / ProArt / TUF GAMING AMD 800 Series",
            "AMD 800 Series",
            "AMD 800 Series BIOS Manual",
            "bios_guide",
            SCOPE_CHIPSET_FAMILY,
            ("amd-800-series",),
        ),
        (
            "B650M AORUS ELITE AX",
            "X670/B650",
            "AMD X670 B650 BIOS Setup",
            "bios_guide",
            SCOPE_CHIPSET_SET,
            ("amd-x670", "amd-b650"),
        ),
        (
            "",
            "Z890",
            "Intel BIOS",
            "bios_guide",
            SCOPE_CHIPSET,
            ("intel-z890",),
        ),
        (
            "AMD AM5 series",
            "AMD AM5 series",
            "AMD AM5 BIOS User Guide",
            "bios_guide",
            SCOPE_PLATFORM,
            ("amd-am5",),
        ),
    ],
)
def test_applicability_scope_prefers_the_narrowest_supported_source_claim(
    model, chipset, identity, document_type, scope, targets
):
    result = normalize_applicability(
        vendor="MSI" if model.startswith("MPG") else "ASUS",
        model_value=model,
        chipset_value=chipset,
        identity_text=identity,
        document_type=document_type,
    )
    assert result.scope == scope
    assert result.target_ids == targets


def test_model_hardware_conflict_stays_visible_for_review():
    result = normalize_applicability(
        vendor="MSI",
        model_value="MPG X670E CARBON WIFI",
        chipset_value="Intel Z790",
        identity_text="",
        document_type="user_manual",
    )
    assert result.scope == SCOPE_MODEL
    assert result.target_ids == ("msi-mpg-x670e-carbon-wifi",)
    assert "conflicts" in result.warnings[0]


def test_unknown_scope_is_explicit():
    result = normalize_applicability(
        vendor="ASUS",
        model_value="Mystery Board",
        chipset_value="",
        identity_text="",
        document_type="bios_guide",
    )
    assert result.scope == SCOPE_UNKNOWN
    assert result.target_ids == ()
    assert result.warnings


def test_contract_ids_and_aliases_are_unique():
    payload = contract()
    assert payload["version"] == "1.0"
    assert len({item.platform_id for item in PLATFORMS}) == len(PLATFORMS)
    assert len({item.family_id for item in CHIPSET_FAMILIES}) == len(CHIPSET_FAMILIES)
    assert len({item.chipset_id for item in CHIPSETS}) == len(CHIPSETS)

    aliases = {}
    for chipset in CHIPSETS:
        for alias in (chipset.canonical_name, *chipset.aliases):
            key = normalized_key(alias)
            owner = aliases.setdefault(key, chipset.chipset_id)
            assert owner == chipset.chipset_id


def test_audit_report_keeps_unknown_scope_and_model_separate():
    rows = audit_values(
        [
            ApplicabilityAuditInput(
                "production_manual",
                "1",
                "ASUS",
                "Mystery",
                "AMD 800 Series",
                "AMD 800 Series BIOS",
                "bios_guide",
            ),
            ApplicabilityAuditInput(
                "staging_manifest",
                "unknown.pdf",
                "MSI",
                "Mystery",
                "",
                "",
                "bios_guide",
            ),
        ]
    )
    report = build_report(rows[:1], rows[1:])
    assert report["summary"]["all_sources"]["resolved"] == 1
    assert report["summary"]["all_sources"]["unknown_scope"] == 1
    assert len(report["unknown_models_with_other_scope"]) == 1
    assert [row["record_id"] for row in report["conflicts"]] == ["unknown.pdf"]


def test_staging_reader_is_read_only_and_validates_columns(tmp_path):
    path = tmp_path / "manifest.csv"
    fieldnames = [
        "relative_path",
        "vendor",
        "filename",
        "metadata_title",
        "document_type",
        "platform",
        "chipsets",
    ]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "relative_path": "ASUS/guide.pdf",
                "vendor": "ASUS",
                "filename": "AMD_800_BIOS.pdf",
                "metadata_title": "AMD 800 Series BIOS Manual",
                "document_type": "bios_setup_guide",
                "platform": "AMD",
                "chipsets": "AMD 800 Series",
            }
        )
    (row,) = staging_inputs(path)
    assert row.record_id == "ASUS/guide.pdf"
    assert row.chipset_value == "AMD 800 Series"

    path.write_text("relative_path,vendor\nASUS/guide.pdf,ASUS\n", encoding="utf-8")
    with pytest.raises(ValueError, match="chipsets"):
        staging_inputs(path)


def test_legacy_full_manual_title_is_classified_for_scope_preview():
    assert document_type_for_applicability("", "MPG X670E CARBON WIFI Motherboard User Guide") == (
        "user_manual"
    )
    assert document_type_for_applicability("full_manual") == "user_manual"
    assert document_type_for_applicability("", "Intel 700 series BIOS User Guide") == ""


def test_manual_applicability_is_persisted_with_audited_raw_values(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="Asus_AMD_800_Series_BIOS_manual.pdf",
        vendor="ASUS",
        model_name="PRIME / ProArt / TUF GAMING AMD 800 Series",
        document_title="AMD 800 Series BIOS Manual",
        chipset="AMD 800 Series",
    )

    view = hardware_domain.get_manual_applicability(db_session, manual.id)
    assert view.scope == SCOPE_CHIPSET_FAMILY
    assert view.target_ids == ("amd-800-series",)
    assert view.targets[0].canonical_name == "AMD 800 Series"
    assert view.warnings == ()

    events = manuals_domain.list_manual_events(db_session, manual.id)
    detail = json.loads(events[-1].detail)
    assert detail["after"]["target_ids"] == ["amd-800-series"]
    assert detail["raw_model_name"] == "PRIME / ProArt / TUF GAMING AMD 800 Series"
    assert detail["raw_chipset"] == "AMD 800 Series"


def test_provenance_change_replaces_targets_without_losing_raw_provenance(
    db_session, pdf_bytes_factory
):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="guide.pdf",
        vendor="ASUS",
        model_name="AMD 800 Series",
        document_title="AMD 800 Series BIOS Manual",
        chipset="AMD 800 Series",
    )
    changed = manuals_domain.update_manual_provenance(
        db_session,
        manual,
        vendor="MSI",
        model_name="MPG X670E CARBON WIFI",
        document_title="MPG X670E CARBON WIFI Motherboard User Guide",
        source_url="",
        document_version="",
        published_on="",
        language="en",
        board_revision="",
        chipset="X670E",
    )

    assert changed is True
    assert manual.model_name == "MPG X670E CARBON WIFI"
    assert manual.chipset == "X670E"
    view = hardware_domain.get_manual_applicability(db_session, manual.id)
    assert view.scope == SCOPE_MODEL
    assert view.target_ids == ("msi-mpg-x670e-carbon-wifi",)
    target_rows = db_session.query(ManualApplicabilityTarget).filter_by(manual_id=manual.id)
    assert target_rows.count() == 1
    assert target_rows.one().motherboard_model_id == "msi-mpg-x670e-carbon-wifi"


def test_unknown_applicability_is_persisted_for_reviewer_attention(db_session, pdf_bytes_factory):
    manual = manuals_domain.create_manual(
        db_session,
        io.BytesIO(pdf_bytes_factory()),
        original_filename="mystery.pdf",
        vendor="ASUS",
        model_name="Mystery Board",
    )
    view = hardware_domain.get_manual_applicability(db_session, manual.id)
    assert view.scope == SCOPE_UNKNOWN
    assert view.targets == ()
    assert view.warnings == ("No normalized applicability target could be determined.",)
