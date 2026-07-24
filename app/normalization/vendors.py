"""Canonical motherboard-vendor identities and deterministic alias matching."""

from __future__ import annotations

from dataclasses import asdict, dataclass

NORMALIZATION_VERSION = "1.0"
STATUS_RECOGNIZED = "recognized"
STATUS_UNKNOWN = "unknown"
STATUS_BLANK = "blank"


@dataclass(frozen=True)
class VendorDefinition:
    vendor_id: str
    canonical_name: str
    aliases: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class VendorNormalization:
    raw_value: str
    normalized_key: str
    status: str
    vendor_id: str | None
    canonical_name: str | None
    matched_alias: str | None

    @property
    def changed(self) -> bool:
        return (
            self.status == STATUS_RECOGNIZED
            and " ".join(self.raw_value.split()) != self.canonical_name
        )

    def as_dict(self) -> dict[str, object]:
        return {**asdict(self), "changed": self.changed}


VENDORS = (
    VendorDefinition(
        vendor_id="asus",
        canonical_name="ASUS",
        aliases=(
            "ASUS",
            "ASUSTeK",
            "ASUSTeK Computer",
            "ASUSTeK Computer Inc.",
            "ASUSTeK Computer Incorporated",
            "Republic of Gamers",
            "ROG",
        ),
    ),
    VendorDefinition(
        vendor_id="msi",
        canonical_name="MSI",
        aliases=(
            "MSI",
            "Micro-Star",
            "Micro Star",
            "Micro-Star International",
            "Micro-Star International Co., Ltd.",
        ),
    ),
    VendorDefinition(
        vendor_id="gigabyte",
        canonical_name="GIGABYTE",
        aliases=(
            "GIGABYTE",
            "Gigabyte Technology",
            "GIGABYTE Technology Co., Ltd.",
            "GIGA-BYTE Technology",
            "AORUS",
        ),
    ),
    VendorDefinition(
        vendor_id="asrock",
        canonical_name="ASRock",
        aliases=(
            "ASRock",
            "ASRock Inc.",
            "ASRock Incorporation",
        ),
    ),
)


def normalized_key(value: str | None) -> str:
    """Return the punctuation-, spacing-, and case-insensitive lookup key."""
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def normalize_vendor(value: str | None) -> VendorNormalization:
    """Classify a raw vendor label without changing or discarding the input."""
    raw_value = value or ""
    key = normalized_key(raw_value)
    if not key:
        return VendorNormalization(
            raw_value=raw_value,
            normalized_key="",
            status=STATUS_BLANK,
            vendor_id=None,
            canonical_name=None,
            matched_alias=None,
        )
    match = _ALIAS_LOOKUP.get(key)
    if match is None:
        return VendorNormalization(
            raw_value=raw_value,
            normalized_key=key,
            status=STATUS_UNKNOWN,
            vendor_id=None,
            canonical_name=None,
            matched_alias=None,
        )
    definition, alias = match
    return VendorNormalization(
        raw_value=raw_value,
        normalized_key=key,
        status=STATUS_RECOGNIZED,
        vendor_id=definition.vendor_id,
        canonical_name=definition.canonical_name,
        matched_alias=alias,
    )


def canonical_vendor_name(value: str | None) -> str | None:
    """Return the canonical display name for a recognized vendor label."""
    return normalize_vendor(value).canonical_name


def vendor_definition(value: str | None) -> VendorDefinition | None:
    """Return the canonical definition for a recognized ID, name, or alias."""
    result = normalize_vendor(value)
    return _VENDORS_BY_ID.get(result.vendor_id) if result.vendor_id else None


def contract() -> dict[str, object]:
    """Return the stable, serializable Phase 1 normalization contract."""
    return {
        "version": NORMALIZATION_VERSION,
        "vendors": [definition.as_dict() for definition in VENDORS],
    }


def _build_alias_lookup() -> dict[str, tuple[VendorDefinition, str]]:
    lookup: dict[str, tuple[VendorDefinition, str]] = {}
    for definition in VENDORS:
        for alias in (definition.vendor_id, definition.canonical_name, *definition.aliases):
            key = normalized_key(alias)
            existing = lookup.get(key)
            if existing is not None and existing[0].vendor_id != definition.vendor_id:
                raise RuntimeError(
                    f"Vendor alias {alias!r} conflicts with {existing[0].canonical_name}."
                )
            if existing is None:
                lookup[key] = (definition, alias)
    return lookup


_VENDORS_BY_ID = {definition.vendor_id: definition for definition in VENDORS}
_ALIAS_LOOKUP = _build_alias_lookup()
