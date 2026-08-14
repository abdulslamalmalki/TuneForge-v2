import pytest

from app.domain import system_context
from app.domain.errors import SystemContextError


def _resolved(db_session):
    return system_context.resolve(
        db_session,
        motherboard_model_id="asrock-x870e-taichi-white",
        cpu_model_id="amd-ryzen-7-9800x3d",
    )


def test_resolves_reviewed_am5_hardware_identity_graph(db_session):
    context = _resolved(db_session)

    assert context.platform_id == "amd-am5"
    assert context.vendor_name == "ASRock"
    assert context.motherboard_model_name == "X870E Taichi White"
    assert context.chipset_id == "amd-x870e"
    assert context.chipset_family_id == "amd-800-series"
    assert context.cpu_model_name == "AMD Ryzen 7 9800X3D"
    assert context.bios_version is None
    assert context.bios_version_status == "not_provided"
    assert context.coverage_status == "identity_resolved"
    assert "not provided" in context.warnings[0]


def test_accepts_optional_user_provided_bios_version_without_inference(db_session):
    context = system_context.resolve(
        db_session,
        motherboard_model_id="asrock-x870e-taichi-white",
        cpu_model_id="amd-ryzen-7-9800x3d",
        bios_version="  3.20  ",
    )

    assert context.bios_version == "3.20"
    assert context.bios_version_status == "provided_unverified"
    assert "matched only against explicit reviewed version scopes" in context.warnings[0]

    with pytest.raises(SystemContextError, match="64 characters"):
        system_context.resolve(
            db_session,
            motherboard_model_id="asrock-x870e-taichi-white",
            cpu_model_id="amd-ryzen-7-9800x3d",
            bios_version="x" * 65,
        )


def test_am5_pilot_options_exclude_other_platforms(db_session):
    motherboards = system_context.list_pilot_motherboards(db_session)
    cpus = system_context.list_pilot_cpus(db_session)

    assert motherboards
    assert cpus
    assert {item.platform_id for item in motherboards} == {"amd-am5"}
    assert {item.platform_id for item in cpus} == {"amd-am5"}
    assert "asus-tuf-gaming-x870-plus-wifi" in {item.model_id for item in motherboards}
    assert "gigabyte-z790-aorus-xtreme-x-ice" not in {item.model_id for item in motherboards}


def test_refuses_unknown_incomplete_and_non_am5_contexts(db_session):
    with pytest.raises(SystemContextError, match="both a motherboard and CPU"):
        system_context.resolve_optional(
            db_session,
            motherboard_model_id="asrock-x870e-taichi-white",
        )
    with pytest.raises(SystemContextError, match="known motherboard"):
        system_context.resolve(
            db_session,
            motherboard_model_id="invented-board",
            cpu_model_id="amd-ryzen-7-9800x3d",
        )
    with pytest.raises(SystemContextError, match="different platforms"):
        system_context.resolve(
            db_session,
            motherboard_model_id="msi-mpg-z690-carbon-wifi",
            cpu_model_id="amd-ryzen-7-9800x3d",
        )


def test_refuses_vendor_filter_that_conflicts_with_selected_motherboard(db_session):
    with pytest.raises(SystemContextError, match="vendor filter does not match"):
        system_context.validate_vendor_filter(_resolved(db_session), "msi")

    system_context.validate_vendor_filter(_resolved(db_session), "")
    system_context.validate_vendor_filter(_resolved(db_session), "ASRock")


@pytest.mark.parametrize(
    ("vendor_id", "scope", "target_ids", "expected"),
    [
        ("asrock", "motherboard_model", ("asrock-x870e-taichi-white",), "exact_model"),
        ("asrock", "chipset", ("amd-x870e",), "chipset"),
        ("asrock", "chipset_family", ("amd-800-series",), "chipset_family"),
        ("asrock", "platform", ("amd-am5",), "platform"),
        ("asrock", "unknown", (), "unverified"),
        ("asrock", "chipset", ("intel-z790",), "incompatible"),
        ("msi", "platform", ("amd-am5",), "incompatible"),
    ],
)
def test_classifies_manual_scope_without_inferring_exact_coverage(
    db_session,
    vendor_id,
    scope,
    target_ids,
    expected,
):
    match = system_context.classify_manual_targets(
        _resolved(db_session),
        manual_vendor_id=vendor_id,
        applicability_scope=scope,
        target_ids=target_ids,
    )
    assert match.status == expected
