from app.normalization.cpu import cpu_contract, normalize_cpu_model
from app.normalization.tuning import tuning_contract


def test_cpu_model_normalization_is_exact_and_stable():
    result = normalize_cpu_model("Ryzen 7 9800X3D")
    assert result.cpu_model_id == "amd-ryzen-7-9800x3d"
    assert result.architecture_id == "amd-zen-5"
    assert result.platform_id == "amd-am5"
    assert result.is_x3d
    assert normalize_cpu_model("Ryzen 7 7800X3D") is None


def test_cpu_contract_exposes_only_the_pilot_scope():
    contract = cpu_contract()
    assert contract["version"] == "1.0"
    assert [row["cpu_model_id"] for row in contract["models"]] == ["amd-ryzen-7-9800x3d"]


def test_tuning_contract_has_typed_values_units_scopes_and_goals():
    contract = tuning_contract()
    assert contract["version"] == "1.0"
    assert "signed_offset" in contract["value_kinds"]
    assert {"watt", "ampere", "megahertz", "celsius"} <= set(contract["units"])
    assert {"all_core", "per_core"} <= set(contract["scopes"])
    assert "daily_stability" in contract["goals"]
