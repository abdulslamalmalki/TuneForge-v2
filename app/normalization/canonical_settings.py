"""Conservative normalization helpers for canonical BIOS-setting identities."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)
_DASHES = re.compile(r"-+")

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
    return " ".join(_NON_ALNUM.sub(" ", normalized).split())


def canonical_slug(value: str) -> str:
    slug = _NON_ALNUM.sub("-", unicodedata.normalize("NFKC", value).casefold())
    return _DASHES.sub("-", slug).strip("-")
