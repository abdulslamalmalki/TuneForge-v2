"""Apply the reviewed TuneForge 0.17.0 canonical-mapping Phase 2 corpus.

Dry-run is the default. The operation is pinned to the verified 0.16.0
production backup, the 20-identity pilot, and 16 source setting IDs. It adds
reviewed aliases and classifications, generates exact-alias suggestions, then
accepts those suggestions through the same audited reviewer service as the UI.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import (
    CanonicalClassificationEvent,
    CanonicalMappingEvent,
    CanonicalMappingSuggestion,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingClassification,
    CanonicalSuggestionEvent,
    Setting,
    SettingCanonicalMapping,
)
from app.db.session import SessionLocal
from app.domain import canonical_settings, canonical_suggestions
from app.domain.review import effective_name
from app.normalization.canonical_settings import normalize_alias

BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-canonical-phase2.sqlite3.backup"
)
EXPECTED_BACKUP_SHA256 = "17d6bfb6d7d36366be8812f1d3ffa3658f1e605aa40575b78d37cd7d0461f79a"
ACTOR = "local-reviewer"
REVIEW_NOTE = "Release 0.17.0 source name and description reviewed."


@dataclass(frozen=True)
class ClassificationSeed:
    canonical_id: str
    category_id: str
    risk_level: str
    rationale: str


CLASSIFICATIONS = (
    ClassificationSeed(
        "above-4g-decoding",
        "pcie-resource",
        "moderate",
        "Changes firmware PCIe address-space allocation above 4 GB.",
    ),
    ClassificationSeed(
        "resizable-bar-support",
        "pcie-resource",
        "moderate",
        "Changes PCIe BAR resource allocation and requires compatible hardware.",
    ),
    ClassificationSeed(
        "xhci-hand-off",
        "peripheral-io",
        "moderate",
        "Changes USB controller ownership during operating-system startup.",
    ),
    ClassificationSeed(
        "legacy-usb-support",
        "peripheral-io",
        "moderate",
        "Can affect preboot keyboard, mouse, and USB storage availability.",
    ),
    ClassificationSeed(
        "fast-boot",
        "boot",
        "low",
        "Skips some firmware initialization and may reduce preboot device availability.",
    ),
    ClassificationSeed(
        "secure-boot",
        "security",
        "high",
        "Changes enforcement of trusted bootloaders and platform boot security.",
    ),
    ClassificationSeed(
        "secure-boot-mode",
        "security",
        "high",
        "Changes Secure Boot key-management behavior and trust policy.",
    ),
    ClassificationSeed(
        "network-stack",
        "network-boot",
        "moderate",
        "Enables firmware networking and can alter available boot paths.",
    ),
    ClassificationSeed(
        "ipv4-pxe-support",
        "network-boot",
        "moderate",
        "Enables IPv4 network boot and changes preboot network exposure.",
    ),
    ClassificationSeed(
        "ipv6-pxe-support",
        "network-boot",
        "moderate",
        "Enables IPv6 network boot and changes preboot network exposure.",
    ),
    ClassificationSeed(
        "iommu",
        "virtualization",
        "high",
        "Changes DMA remapping and device isolation behavior.",
    ),
    ClassificationSeed(
        "amd-svm-mode",
        "virtualization",
        "moderate",
        "Enables processor virtualization used by hypervisors and security features.",
    ),
    ClassificationSeed(
        "intel-vt-d",
        "virtualization",
        "high",
        "Changes directed-I/O virtualization and DMA remapping.",
    ),
    ClassificationSeed(
        "dram-frequency",
        "memory",
        "high",
        "Changing memory frequency can cause instability or failure to boot.",
    ),
    ClassificationSeed(
        "dram-vdd-voltage",
        "memory",
        "high",
        "Changing DRAM supply voltage can cause instability or hardware stress.",
    ),
    ClassificationSeed(
        "dram-vddq-voltage",
        "memory",
        "high",
        "Changing DRAM I/O voltage can cause instability or hardware stress.",
    ),
    ClassificationSeed(
        "memory-context-restore",
        "memory",
        "high",
        "Reusing memory-training state can affect boot reliability and stability.",
    ),
    ClassificationSeed(
        "core-performance-boost",
        "cpu-performance",
        "moderate",
        "Changes automatic CPU boost behavior, power use, and thermals.",
    ),
    ClassificationSeed(
        "package-c-state-limit",
        "power-management",
        "moderate",
        "Changes processor idle-state depth, latency, and power behavior.",
    ),
    ClassificationSeed(
        "erp-ready",
        "power-management",
        "low",
        "Reduces standby power and may disable wake or powered-port behavior.",
    ),
)

ALIASES = (
    (
        "above-4g-decoding",
        "Above 4G memory/ Crypto Currency mining",
        (4510, 5116),
    ),
    ("above-4g-decoding", "Above 4G MMIO BIOS assignment", (5621,)),
    ("network-stack", "NetWork Stack Driver Support", (147, 5697, 6870, 7053)),
    ("intel-vt-d", "Intel VT-D Tech", (5056,)),
    ("core-performance-boost", "Core Performance Boost (Note 1)", (6761,)),
    ("package-c-state-limit", "Package C State limit (Note)", (6949,)),
    ("erp-ready", "ErP", (71, 5608, 6786, 6978)),
    ("amd-svm-mode", "SVM Enable", (6207,)),
    ("fast-boot", "Boot Fast Boot", (1266,)),
)

EXPECTED_SETTING_TARGETS = {
    setting_id: canonical_id
    for canonical_id, _alias, setting_ids in ALIASES
    for setting_id in setting_ids
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != EXPECTED_BACKUP_SHA256:
        raise RuntimeError("The verified pre-Phase-2 backup is missing or changed.")


def _verify_baseline(session) -> None:
    expected = {
        CanonicalSetting: 20,
        CanonicalSettingAlias: (23, 32),
        SettingCanonicalMapping: (137, 153),
        CanonicalMappingEvent: (137, 153),
    }
    for model, allowed in expected.items():
        count = session.scalar(select(func.count()).select_from(model))
        values = allowed if isinstance(allowed, tuple) else (allowed,)
        if count not in values:
            raise RuntimeError(f"{model.__tablename__}: expected one of {values}, found {count}.")
    for canonical_id, alias, setting_ids in ALIASES:
        for setting_id in setting_ids:
            setting = session.get(Setting, setting_id)
            if setting is None or setting.status != "approved":
                raise RuntimeError(f"Pinned setting {setting_id} is missing or not approved.")
            if normalize_alias(effective_name(setting)) != normalize_alias(alias):
                raise RuntimeError(
                    f"Pinned setting {setting_id} no longer matches reviewed alias {alias!r}."
                )
        if session.get(CanonicalSetting, canonical_id) is None:
            raise RuntimeError(f"Canonical identity {canonical_id} is missing.")


def _apply(session) -> None:
    for seed in CLASSIFICATIONS:
        identity = session.get(CanonicalSetting, seed.canonical_id)
        canonical_settings.classify_identity(
            session,
            identity,
            category_id=seed.category_id,
            risk_level=seed.risk_level,
            risk_rationale=seed.rationale,
            actor=ACTOR,
        )
    for canonical_id, alias, _setting_ids in ALIASES:
        identity = session.get(CanonicalSetting, canonical_id)
        canonical_settings.add_alias(
            session,
            identity,
            alias=alias,
            actor=ACTOR,
        )

    canonical_suggestions.refresh_suggestions(session)
    suggestions = {
        row.setting_id: row
        for row in session.scalars(select(CanonicalMappingSuggestion))
        if row.setting_id in EXPECTED_SETTING_TARGETS
    }
    if set(suggestions) != set(EXPECTED_SETTING_TARGETS):
        raise RuntimeError("The generated suggestion IDs differ from the reviewed set.")
    for setting_id, canonical_id in EXPECTED_SETTING_TARGETS.items():
        suggestion = suggestions[setting_id]
        if suggestion.canonical_id != canonical_id:
            raise RuntimeError(f"Suggestion {suggestion.id} has an unexpected target.")
        if suggestion.status == canonical_suggestions.STATUS_PENDING:
            canonical_suggestions.accept_suggestion(
                session,
                suggestion.id,
                note=REVIEW_NOTE,
                actor=ACTOR,
            )
        elif suggestion.status != canonical_suggestions.STATUS_ACCEPTED:
            raise RuntimeError(f"Suggestion {suggestion.id} was previously dismissed.")


def _verify_final(session) -> None:
    expected = {
        CanonicalSetting: 20,
        CanonicalSettingAlias: 32,
        SettingCanonicalMapping: 153,
        CanonicalMappingEvent: 153,
        CanonicalSettingClassification: 20,
        CanonicalClassificationEvent: 20,
        CanonicalMappingSuggestion: 16,
        CanonicalSuggestionEvent: 32,
    }
    failures = []
    for model, value in expected.items():
        found = session.scalar(select(func.count()).select_from(model))
        if found != value:
            failures.append(f"{model.__tablename__}: expected {value}, found {found}")
    pending = session.scalar(
        select(func.count())
        .select_from(CanonicalMappingSuggestion)
        .where(CanonicalMappingSuggestion.status != canonical_suggestions.STATUS_ACCEPTED)
    )
    if pending:
        failures.append(f"unaccepted reviewed suggestions: expected 0, found {pending}")
    if failures:
        raise RuntimeError("Phase 2 verification failed:\n- " + "\n- ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed Phase 2 set")
    args = parser.parse_args()

    _verify_backup()
    with SessionLocal() as session:
        _verify_baseline(session)
        print(
            "Canonical Phase 2: 20 classifications, 9 reviewed aliases, "
            "16 exact-alias review decisions."
        )
        if not args.apply:
            print("Dry run only; pass --apply after reviewing the pinned operation.")
            return
        _apply(session)
        _verify_final(session)
        print("Applied: aliases=32, mappings=153, accepted suggestions=16.")


if __name__ == "__main__":
    main()
