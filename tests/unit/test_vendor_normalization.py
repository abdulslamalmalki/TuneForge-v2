import csv

import pytest

from app.normalization.vendors import (
    STATUS_BLANK,
    STATUS_RECOGNIZED,
    STATUS_UNKNOWN,
    VENDORS,
    canonical_vendor_name,
    contract,
    normalize_vendor,
    normalized_key,
)
from tools.audit_vendor_normalization import (
    VendorAuditInput,
    audit_values,
    build_report,
    staging_inputs,
)


@pytest.mark.parametrize(
    ("value", "vendor_id", "canonical_name"),
    [
        ("ASUS", "asus", "ASUS"),
        ("ASUSTeK Computer Inc.", "asus", "ASUS"),
        ("Republic of Gamers", "asus", "ASUS"),
        ("ROG", "asus", "ASUS"),
        ("MSI", "msi", "MSI"),
        (" Micro-Star International Co., Ltd. ", "msi", "MSI"),
        ("Gigabyte Technology", "gigabyte", "GIGABYTE"),
        ("GIGA-BYTE Technology", "gigabyte", "GIGABYTE"),
        ("AORUS", "gigabyte", "GIGABYTE"),
        ("ASRock Incorporation", "asrock", "ASRock"),
    ],
)
def test_recognizes_canonical_names_manufacturer_aliases_and_subbrands(
    value, vendor_id, canonical_name
):
    result = normalize_vendor(value)
    assert result.status == STATUS_RECOGNIZED
    assert result.vendor_id == vendor_id
    assert result.canonical_name == canonical_name
    assert result.raw_value == value
    assert canonical_vendor_name(value) == canonical_name


def test_matching_ignores_case_spacing_and_punctuation():
    assert normalized_key("  Micro--Star International  ") == "microstarinternational"
    result = normalize_vendor("gIgAbYtE technology co ltd")
    assert result.vendor_id == "gigabyte"
    assert result.changed is True


def test_unknown_and_blank_values_remain_visible_for_review():
    unknown = normalize_vendor("Example Boards")
    assert unknown.status == STATUS_UNKNOWN
    assert unknown.raw_value == "Example Boards"
    assert unknown.canonical_name is None
    assert unknown.changed is False

    blank = normalize_vendor("  ")
    assert blank.status == STATUS_BLANK
    assert blank.raw_value == "  "
    assert blank.canonical_name is None


def test_contract_has_unique_stable_ids_and_alias_keys():
    payload = contract()
    assert payload["version"] == "1.0"
    assert [vendor["vendor_id"] for vendor in payload["vendors"]] == [
        "asus",
        "msi",
        "gigabyte",
        "asrock",
    ]
    owners = {}
    for vendor in VENDORS:
        for alias in (vendor.vendor_id, vendor.canonical_name, *vendor.aliases):
            key = normalized_key(alias)
            assert key
            assert owners.setdefault(key, vendor.vendor_id) == vendor.vendor_id


def test_audit_report_separates_changes_from_conflicts():
    production = audit_values(
        [
            VendorAuditInput("production_manual", "1", "ASUS"),
            VendorAuditInput("production_manual", "2", "Gigabyte"),
        ]
    )
    staging = audit_values(
        [
            VendorAuditInput("staging_manifest", "MSI/guide.pdf", "Micro-Star"),
            VendorAuditInput("staging_manifest", "Unknown/guide.pdf", "Unknown"),
        ]
    )
    report = build_report(production, staging)
    assert report["summary"]["all_sources"] == {
        "total": 4,
        "recognized": 3,
        "unchanged": 1,
        "proposed_changes": 2,
        "unknown": 1,
        "blank": 0,
        "canonical_vendors": {"asus": 1, "gigabyte": 1, "msi": 1},
    }
    assert [row["record_id"] for row in report["conflicts"]] == ["Unknown/guide.pdf"]
    assert [row["record_id"] for row in report["proposed_changes"]] == [
        "2",
        "MSI/guide.pdf",
    ]


def test_staging_manifest_reader_is_read_only_and_validates_columns(tmp_path):
    path = tmp_path / "manifest.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["relative_path", "vendor"])
        writer.writeheader()
        writer.writerow({"relative_path": "ASUS/guide.pdf", "vendor": "ASUSTeK"})
    assert staging_inputs(path) == [
        VendorAuditInput("staging_manifest", "ASUS/guide.pdf", "ASUSTeK")
    ]

    path.write_text("filename,vendor\nguide.pdf,ASUS\n", encoding="utf-8")
    with pytest.raises(ValueError, match="relative_path"):
        staging_inputs(path)
