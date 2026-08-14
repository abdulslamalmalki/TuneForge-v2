"""Deterministic hardware identity and manual-applicability normalization."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from app.normalization.cpu import cpu_contract
from app.normalization.vendors import normalize_vendor, normalized_key

NORMALIZATION_VERSION = "1.0"

MODEL_SPECIFIC = "specific_model"
MODEL_BROAD = "broad_scope_label"
MODEL_UNKNOWN = "unknown"
MODEL_BLANK = "blank"

SCOPE_MODEL = "motherboard_model"
SCOPE_CHIPSET = "chipset"
SCOPE_CHIPSET_SET = "chipset_set"
SCOPE_CHIPSET_FAMILY = "chipset_family"
SCOPE_PLATFORM = "platform"
SCOPE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlatformDefinition:
    platform_id: str
    canonical_name: str
    cpu_vendor: str
    socket_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ChipsetFamilyDefinition:
    family_id: str
    canonical_name: str
    platform_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ChipsetDefinition:
    chipset_id: str
    canonical_name: str
    family_id: str
    platform_id: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class MotherboardModelDefinition:
    model_id: str
    vendor_id: str
    canonical_name: str
    chipset_id: str
    platform_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class MotherboardFamilyDefinition:
    family_id: str
    vendor_id: str
    canonical_name: str


@dataclass(frozen=True)
class ModelNormalization:
    raw_value: str
    normalized_key: str
    status: str
    model_id: str | None
    canonical_name: str | None
    matched_alias: str | None
    vendor_id: str | None
    chipset_id: str | None
    platform_id: str | None


@dataclass(frozen=True)
class HardwareNormalization:
    raw_value: str
    identity_text: str
    chipset_ids: tuple[str, ...]
    explicit_family_ids: tuple[str, ...]
    family_ids: tuple[str, ...]
    platform_ids: tuple[str, ...]


@dataclass(frozen=True)
class ApplicabilityNormalization:
    scope: str
    target_ids: tuple[str, ...]
    model: ModelNormalization
    hardware: HardwareNormalization
    warnings: tuple[str, ...]


PLATFORMS = (
    PlatformDefinition("amd-am4", "AMD AM4", "AMD", "AM4", ("AM4", "AMD AM4")),
    PlatformDefinition("amd-am5", "AMD AM5", "AMD", "AM5", ("AM5", "AMD AM5")),
    PlatformDefinition(
        "intel-lga1200",
        "Intel LGA1200",
        "Intel",
        "LGA1200",
        ("LGA1200", "LGA 1200", "Intel LGA1200"),
    ),
    PlatformDefinition(
        "intel-lga1700",
        "Intel LGA1700",
        "Intel",
        "LGA1700",
        ("LGA1700", "LGA 1700", "Intel LGA1700"),
    ),
    PlatformDefinition(
        "intel-lga1851",
        "Intel LGA1851",
        "Intel",
        "LGA1851",
        ("LGA1851", "LGA 1851", "Intel LGA1851"),
    ),
)

CHIPSET_FAMILIES = (
    ChipsetFamilyDefinition("amd-400-series", "AMD 400 Series", "amd-am4", ("AMD 400 Series",)),
    ChipsetFamilyDefinition("amd-500-series", "AMD 500 Series", "amd-am4", ("AMD 500 Series",)),
    ChipsetFamilyDefinition("amd-600-series", "AMD 600 Series", "amd-am5", ("AMD 600 Series",)),
    ChipsetFamilyDefinition("amd-800-series", "AMD 800 Series", "amd-am5", ("AMD 800 Series",)),
    ChipsetFamilyDefinition(
        "intel-400-series",
        "Intel 400 Series",
        "intel-lga1200",
        ("Intel 400 Series",),
    ),
    ChipsetFamilyDefinition(
        "intel-500-series",
        "Intel 500 Series",
        "intel-lga1200",
        ("Intel 500 Series",),
    ),
    ChipsetFamilyDefinition(
        "intel-600-series",
        "Intel 600 Series",
        "intel-lga1700",
        ("Intel 600 Series",),
    ),
    ChipsetFamilyDefinition(
        "intel-700-series",
        "Intel 700 Series",
        "intel-lga1700",
        ("Intel 700 Series",),
    ),
    ChipsetFamilyDefinition(
        "intel-800-series",
        "Intel 800 Series",
        "intel-lga1851",
        ("Intel 800 Series",),
    ),
)


def _chipset(
    chipset_id: str,
    canonical_name: str,
    family_id: str,
    platform_id: str,
) -> ChipsetDefinition:
    return ChipsetDefinition(
        chipset_id=chipset_id,
        canonical_name=canonical_name,
        family_id=family_id,
        platform_id=platform_id,
        aliases=(canonical_name.split(" ", 1)[-1],),
    )


CHIPSETS = (
    _chipset("amd-x470", "AMD X470", "amd-400-series", "amd-am4"),
    _chipset("amd-b450", "AMD B450", "amd-400-series", "amd-am4"),
    _chipset("amd-x570s", "AMD X570S", "amd-500-series", "amd-am4"),
    _chipset("amd-x570", "AMD X570", "amd-500-series", "amd-am4"),
    _chipset("amd-b550", "AMD B550", "amd-500-series", "amd-am4"),
    _chipset("amd-a520", "AMD A520", "amd-500-series", "amd-am4"),
    _chipset("amd-x670e", "AMD X670E", "amd-600-series", "amd-am5"),
    _chipset("amd-x670", "AMD X670", "amd-600-series", "amd-am5"),
    _chipset("amd-b650e", "AMD B650E", "amd-600-series", "amd-am5"),
    _chipset("amd-b650", "AMD B650", "amd-600-series", "amd-am5"),
    _chipset("amd-x870e", "AMD X870E", "amd-800-series", "amd-am5"),
    _chipset("amd-x870", "AMD X870", "amd-800-series", "amd-am5"),
    _chipset("amd-b850", "AMD B850", "amd-800-series", "amd-am5"),
    _chipset("amd-b840", "AMD B840", "amd-800-series", "amd-am5"),
    _chipset("intel-z490", "Intel Z490", "intel-400-series", "intel-lga1200"),
    _chipset("intel-z590", "Intel Z590", "intel-500-series", "intel-lga1200"),
    _chipset("intel-b560", "Intel B560", "intel-500-series", "intel-lga1200"),
    _chipset("intel-h570", "Intel H570", "intel-500-series", "intel-lga1200"),
    _chipset("intel-h510", "Intel H510", "intel-500-series", "intel-lga1200"),
    _chipset("intel-z690", "Intel Z690", "intel-600-series", "intel-lga1700"),
    _chipset("intel-b660", "Intel B660", "intel-600-series", "intel-lga1700"),
    _chipset("intel-h670", "Intel H670", "intel-600-series", "intel-lga1700"),
    _chipset("intel-h610", "Intel H610", "intel-600-series", "intel-lga1700"),
    _chipset("intel-z790", "Intel Z790", "intel-700-series", "intel-lga1700"),
    _chipset("intel-b760", "Intel B760", "intel-700-series", "intel-lga1700"),
    _chipset("intel-h770", "Intel H770", "intel-700-series", "intel-lga1700"),
    _chipset("intel-z890", "Intel Z890", "intel-800-series", "intel-lga1851"),
    _chipset("intel-b860", "Intel B860", "intel-800-series", "intel-lga1851"),
    _chipset("intel-h810", "Intel H810", "intel-800-series", "intel-lga1851"),
)

MOTHERBOARD_MODELS = (
    MotherboardModelDefinition(
        "asus-tuf-gaming-x870-plus-wifi",
        "asus",
        "TUF GAMING X870-PLUS WIFI",
        "amd-x870",
        "amd-am5",
        ("TUF GAMING X870-PLUS WIFI",),
    ),
    MotherboardModelDefinition(
        "asrock-b450m-steel-legend",
        "asrock",
        "B450M Steel Legend",
        "amd-b450",
        "amd-am4",
        ("B450M Steel Legend",),
    ),
    MotherboardModelDefinition(
        "asrock-x870e-taichi-white",
        "asrock",
        "X870E Taichi White",
        "amd-x870e",
        "amd-am5",
        ("X870E Taichi White",),
    ),
    MotherboardModelDefinition(
        "gigabyte-b650m-aorus-elite-ax",
        "gigabyte",
        "B650M AORUS ELITE AX",
        "amd-b650",
        "amd-am5",
        ("B650M AORUS ELITE AX",),
    ),
    MotherboardModelDefinition(
        "gigabyte-x470-aorus-gaming-7-wifi",
        "gigabyte",
        "X470 AORUS GAMING 7 WIFI",
        "amd-x470",
        "amd-am4",
        ("X470 AORUS GAMING 7 WIFI",),
    ),
    MotherboardModelDefinition(
        "gigabyte-x570s-aorus-pro-ax",
        "gigabyte",
        "X570S AORUS PRO AX",
        "amd-x570s",
        "amd-am4",
        ("X570S AORUS PRO AX",),
    ),
    MotherboardModelDefinition(
        "gigabyte-z590-aorus-xtreme-wb",
        "gigabyte",
        "Z590 AORUS XTREME WB",
        "intel-z590",
        "intel-lga1200",
        ("Z590 AORUS XTREME WB",),
    ),
    MotherboardModelDefinition(
        "gigabyte-z790-aorus-xtreme-x-ice",
        "gigabyte",
        "Z790 AORUS XTREME X ICE",
        "intel-z790",
        "intel-lga1700",
        ("Z790 AORUS XTREME X ICE",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-b550-gaming-edge-wifi",
        "msi",
        "MPG B550 GAMING EDGE WIFI",
        "amd-b550",
        "amd-am4",
        ("MPG B550 GAMING EDGE WIFI",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-x570s-carbon-max-wifi",
        "msi",
        "MPG X570S CARBON MAX WIFI",
        "amd-x570s",
        "amd-am4",
        ("MPG X570S CARBON MAX WIFI",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-x670e-carbon-wifi",
        "msi",
        "MPG X670E CARBON WIFI",
        "amd-x670e",
        "amd-am5",
        ("MPG X670E CARBON WIFI",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-z590-gaming-carbon-wifi",
        "msi",
        "MPG Z590 GAMING CARBON WIFI",
        "intel-z590",
        "intel-lga1200",
        ("MPG Z590 GAMING CARBON WIFI",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-z590-gaming-force",
        "msi",
        "MPG Z590 GAMING FORCE",
        "intel-z590",
        "intel-lga1200",
        ("MPG Z590 GAMING FORCE",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-z690-carbon-wifi",
        "msi",
        "MPG Z690 CARBON WIFI",
        "intel-z690",
        "intel-lga1700",
        ("MPG Z690 CARBON WIFI",),
    ),
    MotherboardModelDefinition(
        "msi-mpg-z690-force-wifi",
        "msi",
        "MPG Z690 FORCE WIFI",
        "intel-z690",
        "intel-lga1700",
        ("MPG Z690 FORCE WIFI",),
    ),
)

MOTHERBOARD_FAMILIES = (
    MotherboardFamilyDefinition("asus-tuf-gaming", "asus", "ASUS TUF Gaming"),
    MotherboardFamilyDefinition("asrock-steel-legend", "asrock", "ASRock Steel Legend"),
    MotherboardFamilyDefinition("asrock-taichi", "asrock", "ASRock Taichi"),
    MotherboardFamilyDefinition("gigabyte-aorus", "gigabyte", "GIGABYTE AORUS"),
    MotherboardFamilyDefinition("msi-mpg", "msi", "MSI MPG"),
)


def motherboard_family_id(model: MotherboardModelDefinition) -> str | None:
    name = model.canonical_name.casefold()
    if model.vendor_id == "asus" and "tuf gaming" in name:
        return "asus-tuf-gaming"
    if model.vendor_id == "asrock" and "steel legend" in name:
        return "asrock-steel-legend"
    if model.vendor_id == "asrock" and "taichi" in name:
        return "asrock-taichi"
    if model.vendor_id == "gigabyte" and "aorus" in name:
        return "gigabyte-aorus"
    if model.vendor_id == "msi" and name.startswith("mpg "):
        return "msi-mpg"
    return None


_BROAD_MODEL_PATTERNS = (
    re.compile(r"\b(?:AMD|INTEL)\s+\d{3}\s+SERIES\b", re.IGNORECASE),
    re.compile(
        r"\b(?:AMD|INTEL)\s+[A-Z]\d{3}(?:\s*/\s*[A-Z]\d{3})+\s+SERIES\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bAMD\s+AM[45]\s+SERIES\b", re.IGNORECASE),
    re.compile(r"\bPRIME\b.*\bPROART\b.*\bTUF\s+GAMING\b", re.IGNORECASE),
)


def normalize_model(vendor: str | None, value: str | None) -> ModelNormalization:
    raw = value or ""
    key = normalized_key(raw)
    vendor_id = normalize_vendor(vendor).vendor_id
    if not key:
        return ModelNormalization(raw, key, MODEL_BLANK, None, None, None, vendor_id, None, None)
    candidates = [
        model for model in MOTHERBOARD_MODELS if vendor_id is None or model.vendor_id == vendor_id
    ]
    for model in candidates:
        for alias in (model.canonical_name, *model.aliases):
            alias_key = normalized_key(alias)
            if key == alias_key or alias_key in key:
                return ModelNormalization(
                    raw,
                    key,
                    MODEL_SPECIFIC,
                    model.model_id,
                    model.canonical_name,
                    alias,
                    model.vendor_id,
                    model.chipset_id,
                    model.platform_id,
                )
    if any(pattern.search(raw.replace("_", " ")) for pattern in _BROAD_MODEL_PATTERNS):
        return ModelNormalization(
            raw, key, MODEL_BROAD, None, " ".join(raw.split()), None, vendor_id, None, None
        )
    return ModelNormalization(raw, key, MODEL_UNKNOWN, None, None, None, vendor_id, None, None)


def normalize_hardware(value: str | None, identity_text: str = "") -> HardwareNormalization:
    raw = value or ""
    combined = f"{raw} {identity_text}".strip()
    chipset_ids = tuple(
        definition.chipset_id for definition in CHIPSETS if _contains_chipset(combined, definition)
    )
    explicit_family_ids = tuple(
        definition.family_id
        for definition in CHIPSET_FAMILIES
        if any(_contains_phrase(combined, alias) for alias in definition.aliases)
    )
    derived_family_ids = tuple(
        definition.family_id for definition in CHIPSETS if definition.chipset_id in chipset_ids
    )
    family_ids = _ordered_unique((*explicit_family_ids, *derived_family_ids))
    explicit_platform_ids = tuple(
        definition.platform_id
        for definition in PLATFORMS
        if any(_contains_phrase(combined, alias) for alias in definition.aliases)
    )
    family_platform_ids = tuple(
        definition.platform_id
        for definition in CHIPSET_FAMILIES
        if definition.family_id in family_ids
    )
    chipset_platform_ids = tuple(
        definition.platform_id for definition in CHIPSETS if definition.chipset_id in chipset_ids
    )
    return HardwareNormalization(
        raw_value=raw,
        identity_text=identity_text,
        chipset_ids=chipset_ids,
        explicit_family_ids=explicit_family_ids,
        family_ids=family_ids,
        platform_ids=_ordered_unique(
            (*explicit_platform_ids, *family_platform_ids, *chipset_platform_ids)
        ),
    )


def normalize_applicability(
    *,
    vendor: str | None,
    model_value: str | None,
    chipset_value: str | None,
    identity_text: str,
    document_type: str | None,
) -> ApplicabilityNormalization:
    model = normalize_model(vendor, model_value)
    hardware = normalize_hardware(chipset_value, identity_text)
    warnings: list[str] = []
    if len(hardware.platform_ids) > 1:
        warnings.append("Hardware signals span multiple platforms.")
    if (
        model.status == MODEL_SPECIFIC
        and hardware.platform_ids
        and model.platform_id not in hardware.platform_ids
    ):
        warnings.append("The source model conflicts with the detected hardware platform.")

    is_user_manual = document_type_for_applicability(document_type) == "user_manual"
    if is_user_manual and model.model_id:
        scope, targets = SCOPE_MODEL, (model.model_id,)
    elif len(hardware.chipset_ids) > 1:
        scope, targets = SCOPE_CHIPSET_SET, hardware.chipset_ids
    elif hardware.explicit_family_ids:
        scope, targets = SCOPE_CHIPSET_FAMILY, hardware.explicit_family_ids
    elif hardware.chipset_ids:
        scope, targets = SCOPE_CHIPSET, hardware.chipset_ids
    elif hardware.platform_ids:
        scope, targets = SCOPE_PLATFORM, hardware.platform_ids
    elif model.model_id:
        scope, targets = SCOPE_MODEL, (model.model_id,)
    else:
        scope, targets = SCOPE_UNKNOWN, ()
        warnings.append("No normalized applicability target could be determined.")
    return ApplicabilityNormalization(scope, targets, model, hardware, tuple(warnings))


def document_type_for_applicability(value: str | None, title: str = "") -> str:
    """Normalize document types, including pre-eligibility legacy full manuals."""
    normalized = (value or "").strip().casefold()
    if normalized in {"full_manual", "full_user_manual", "user_manual"}:
        return "user_manual"
    if not normalized and "motherboard user guide" in title.casefold():
        return "user_manual"
    return normalized


def contract() -> dict[str, object]:
    return {
        "version": NORMALIZATION_VERSION,
        "platforms": [asdict(item) for item in PLATFORMS],
        "chipset_families": [asdict(item) for item in CHIPSET_FAMILIES],
        "chipsets": [asdict(item) for item in CHIPSETS],
        "motherboard_families": [asdict(item) for item in MOTHERBOARD_FAMILIES],
        "motherboard_models": [asdict(item) for item in MOTHERBOARD_MODELS],
        "cpus": cpu_contract(),
        "applicability_scopes": [
            SCOPE_MODEL,
            SCOPE_CHIPSET,
            SCOPE_CHIPSET_SET,
            SCOPE_CHIPSET_FAMILY,
            SCOPE_PLATFORM,
            SCOPE_UNKNOWN,
        ],
    }


def _contains_chipset(text: str, definition: ChipsetDefinition) -> bool:
    tokens = (definition.canonical_name.split(" ", 1)[-1], *definition.aliases)
    return any(
        re.search(rf"(?<![A-Z0-9]){re.escape(token.upper())}(?![A-Z0-9])", text.upper())
        for token in tokens
    )


def _contains_phrase(text: str, phrase: str) -> bool:
    return normalized_key(phrase) in normalized_key(text)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
