"""Conservative normalization helpers for canonical BIOS-setting identities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)
_DASHES = re.compile(r"-+")
_PARENTHESIZED_SIGN = re.compile(r"\(\s*([+\-\u2212])\s*\)")
_TRADEMARK_MARKER = re.compile(r"\(\s*(?:r|tm)\s*\)|[®™]", re.IGNORECASE)

RISK_UNCLASSIFIED = "unclassified"
RISK_LOW = "low"
RISK_MODERATE = "moderate"
RISK_HIGH = "high"
RISK_CRITICAL = "critical"
RISK_LEVELS = (
    RISK_UNCLASSIFIED,
    RISK_LOW,
    RISK_MODERATE,
    RISK_HIGH,
    RISK_CRITICAL,
)


@dataclass(frozen=True)
class CategoryDefinition:
    category_id: str
    canonical_name: str
    description: str
    ordinal: int


CATEGORIES = (
    CategoryDefinition(
        "uncategorized",
        "Uncategorized",
        "Canonical settings awaiting reviewer classification.",
        0,
    ),
    CategoryDefinition(
        "boot",
        "Boot",
        "Firmware startup behavior and boot-time initialization.",
        10,
    ),
    CategoryDefinition(
        "security",
        "Security",
        "Platform trust, key enforcement, and security controls.",
        20,
    ),
    CategoryDefinition(
        "virtualization",
        "Virtualization",
        "CPU and device virtualization capabilities.",
        30,
    ),
    CategoryDefinition(
        "memory",
        "Memory",
        "DRAM frequency, training, timing, and voltage controls.",
        40,
    ),
    CategoryDefinition(
        "cpu-performance",
        "CPU Performance",
        "Processor boosting, ratios, and performance behavior.",
        50,
    ),
    CategoryDefinition(
        "power-management",
        "Power Management",
        "Idle states, standby power, and energy behavior.",
        60,
    ),
    CategoryDefinition(
        "peripheral-io",
        "Peripheral I/O",
        "USB and other peripheral initialization controls.",
        70,
    ),
    CategoryDefinition(
        "pcie-resource",
        "PCIe Resources",
        "PCIe address-space and resource-allocation controls.",
        80,
    ),
    CategoryDefinition(
        "network-boot",
        "Network Boot",
        "Preboot networking and PXE controls.",
        90,
    ),
)


def normalize_alias(value: str) -> str:
    """Normalize an alias for exact suggestion matching.

    This intentionally does not expand acronyms or infer semantic similarity.
    Punctuation and whitespace differences are normalized; semantic aliases
    must be explicitly attached to a canonical identity by a reviewer.
    """
    normalized = unicodedata.normalize("NFKC", value).casefold()
    # A parenthesized sign is semantic in firmware labels such as
    # "Max CPU Boost Clock Override(+)" and "...(-)". Treating both signs
    # as disposable punctuation made two opposite controls collide.
    normalized = _PARENTHESIZED_SIGN.sub(
        lambda match: " positive " if match.group(1) == "+" else " negative ",
        normalized,
    )
    return " ".join(_NON_ALNUM.sub(" ", normalized).split())


def normalize_lookup_variants(value: str) -> tuple[str, ...]:
    """Return exact lookup keys plus a typography-only trademark variant.

    Stored alias normalization remains unchanged. Removing an explicit registered/trademark
    marker at lookup time is safe because callers must still require one unique reviewed
    canonical identity; this does not expand acronyms or infer semantic equivalence.
    """

    variants = [normalize_alias(value)]
    without_trademark = normalize_alias(_TRADEMARK_MARKER.sub(" ", value))
    if without_trademark and without_trademark not in variants:
        variants.append(without_trademark)
    return tuple(item for item in variants if item)


def canonical_slug(value: str) -> str:
    slug = _NON_ALNUM.sub("-", unicodedata.normalize("NFKC", value).casefold())
    return _DASHES.sub("-", slug).strip("-")
