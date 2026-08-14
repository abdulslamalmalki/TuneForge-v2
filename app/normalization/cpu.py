"""Stable CPU identities for tuning-evidence applicability."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.normalization.vendors import normalized_key

CPU_NORMALIZATION_VERSION = "1.0"


@dataclass(frozen=True)
class CpuVendorDefinition:
    vendor_id: str
    canonical_name: str


@dataclass(frozen=True)
class CpuArchitectureDefinition:
    architecture_id: str
    vendor_name: str
    canonical_name: str


@dataclass(frozen=True)
class CpuFamilyDefinition:
    family_id: str
    architecture_id: str
    platform_id: str
    canonical_name: str


@dataclass(frozen=True)
class CpuModelDefinition:
    cpu_model_id: str
    family_id: str
    architecture_id: str
    platform_id: str
    canonical_name: str
    is_x3d: bool
    aliases: tuple[str, ...]


CPU_VENDORS = (
    CpuVendorDefinition("amd", "AMD"),
    CpuVendorDefinition("intel", "Intel"),
)

CPU_ARCHITECTURES = (CpuArchitectureDefinition("amd-zen-5", "AMD", "AMD Zen 5"),)

CPU_FAMILIES = (
    CpuFamilyDefinition(
        "amd-ryzen-9000-desktop",
        "amd-zen-5",
        "amd-am5",
        "AMD Ryzen 9000 Series Desktop",
    ),
)

CPU_MODELS = (
    CpuModelDefinition(
        "amd-ryzen-7-9800x3d",
        "amd-ryzen-9000-desktop",
        "amd-zen-5",
        "amd-am5",
        "AMD Ryzen 7 9800X3D",
        True,
        ("Ryzen 7 9800X3D", "Ryzen 9800X3D", "9800X3D"),
    ),
)


def normalize_cpu_model(value: str | None) -> CpuModelDefinition | None:
    key = normalized_key(value or "")
    if not key:
        return None
    for model in CPU_MODELS:
        if key in {
            normalized_key(model.cpu_model_id),
            normalized_key(model.canonical_name),
            *(normalized_key(alias) for alias in model.aliases),
        }:
            return model
    return None


def cpu_contract() -> dict[str, object]:
    return {
        "version": CPU_NORMALIZATION_VERSION,
        "vendors": [asdict(item) for item in CPU_VENDORS],
        "architectures": [asdict(item) for item in CPU_ARCHITECTURES],
        "families": [asdict(item) for item in CPU_FAMILIES],
        "models": [asdict(item) for item in CPU_MODELS],
    }
