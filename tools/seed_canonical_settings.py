"""Seed the reviewed TuneForge 0.16.0 canonical-setting pilot.

Dry-run is the default. The seed is deliberately limited to 20 unambiguous
identities and exact normalized aliases across already-approved settings.
No fuzzy match is persisted, and every mapping uses the audited domain service.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import (
    CanonicalSetting,
    Manual,
    Setting,
    SettingCanonicalMapping,
)
from app.db.session import SessionLocal
from app.domain import canonical_settings as canonical_domain
from app.domain.review import effective_name
from app.normalization.canonical_settings import normalize_alias

BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-canonical-settings.sqlite3.backup"
)
EXPECTED_BACKUP_SHA256 = "61ff7ac1dcb024e2c59fa7a4c4a05292706cee139317fb1b06ebfc1381e0059d"
SEED_ACTOR = "local-reviewer"
SEED_NOTE = "Release 0.16.0 exact-label seed reviewed across accepted source settings."


@dataclass(frozen=True)
class SeedIdentity:
    canonical_id: str
    canonical_name: str
    description: str
    source_aliases: tuple[str, ...]
    expected_mappings: int
    expected_vendors: int


IDENTITIES = (
    SeedIdentity(
        "above-4g-decoding",
        "Above 4G Decoding",
        "Allows 64-bit PCIe devices to use memory-mapped address space above 4 GB.",
        ("Above 4G Decoding",),
        7,
        3,
    ),
    SeedIdentity(
        "resizable-bar-support",
        "Resizable BAR Support",
        "Allows compatible PCIe devices to expose a larger base address register.",
        ("Re-Size BAR Support",),
        9,
        4,
    ),
    SeedIdentity(
        "xhci-hand-off",
        "XHCI Hand-off",
        "Controls xHCI ownership hand-off between firmware and the operating system.",
        ("XHCI Hand-off",),
        11,
        4,
    ),
    SeedIdentity(
        "legacy-usb-support",
        "Legacy USB Support",
        "Controls firmware support for USB input and storage before OS drivers load.",
        ("Legacy USB Support",),
        9,
        3,
    ),
    SeedIdentity(
        "fast-boot",
        "Fast Boot",
        "Reduces firmware initialization work to shorten system startup.",
        ("Fast Boot",),
        9,
        4,
    ),
    SeedIdentity(
        "secure-boot",
        "Secure Boot",
        "Controls UEFI Secure Boot enforcement.",
        ("Secure Boot",),
        5,
        3,
    ),
    SeedIdentity(
        "secure-boot-mode",
        "Secure Boot Mode",
        "Selects the UEFI Secure Boot key-management mode.",
        ("Secure Boot Mode",),
        7,
        3,
    ),
    SeedIdentity(
        "network-stack",
        "Network Stack",
        "Controls the firmware network stack used for preboot networking.",
        ("Network Stack",),
        10,
        3,
    ),
    SeedIdentity(
        "ipv4-pxe-support",
        "IPv4 PXE Support",
        "Controls IPv4 network boot support through PXE.",
        ("IPv4 PXE Support",),
        7,
        3,
    ),
    SeedIdentity(
        "ipv6-pxe-support",
        "IPv6 PXE Support",
        "Controls IPv6 network boot support through PXE.",
        ("IPv6 PXE Support",),
        7,
        3,
    ),
    SeedIdentity(
        "iommu",
        "IOMMU",
        "Controls the platform input-output memory management unit.",
        ("IOMMU",),
        5,
        4,
    ),
    SeedIdentity(
        "amd-svm-mode",
        "AMD SVM Mode",
        "Controls AMD CPU hardware virtualization support.",
        ("SVM Mode",),
        4,
        3,
    ),
    SeedIdentity(
        "intel-vt-d",
        "Intel VT-d",
        "Controls Intel directed-I/O virtualization support.",
        ("VT-d",),
        5,
        2,
    ),
    SeedIdentity(
        "dram-frequency",
        "DRAM Frequency",
        "Selects the operating frequency of system memory.",
        ("DRAM Frequency",),
        8,
        3,
    ),
    SeedIdentity(
        "dram-vdd-voltage",
        "DRAM VDD Voltage",
        "Controls the primary DRAM supply voltage.",
        ("DRAM VDD Voltage",),
        5,
        2,
    ),
    SeedIdentity(
        "dram-vddq-voltage",
        "DRAM VDDQ Voltage",
        "Controls the DRAM input-output supply voltage.",
        ("DRAM VDDQ Voltage",),
        9,
        3,
    ),
    SeedIdentity(
        "memory-context-restore",
        "Memory Context Restore",
        "Controls reuse of saved memory-training context during boot.",
        ("Memory Context Restore",),
        5,
        4,
    ),
    SeedIdentity(
        "core-performance-boost",
        "Core Performance Boost",
        "Controls automatic CPU boost above the base operating frequency.",
        ("Core Performance Boost",),
        4,
        3,
    ),
    SeedIdentity(
        "package-c-state-limit",
        "Package C State Limit",
        "Sets the deepest processor package idle state the platform may use.",
        ("Package C State Limit",),
        5,
        3,
    ),
    SeedIdentity(
        "erp-ready",
        "ErP Ready",
        "Reduces standby power to meet ErP energy requirements.",
        ("ErP Ready",),
        6,
        2,
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file() or _sha256(BACKUP_PATH) != EXPECTED_BACKUP_SHA256:
        raise RuntimeError("The verified pre-canonicalization backup is missing or changed.")


def _candidate_map(session) -> dict[str, list[tuple[Setting, Manual]]]:
    aliases = {
        normalize_alias(alias): identity.canonical_id
        for identity in IDENTITIES
        for alias in identity.source_aliases
    }
    result = {identity.canonical_id: [] for identity in IDENTITIES}
    rows = session.execute(
        select(Setting, Manual)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(Setting.status == "approved")
        .order_by(Setting.id)
    )
    for setting, manual in rows:
        canonical_id = aliases.get(normalize_alias(effective_name(setting)))
        if canonical_id:
            result[canonical_id].append((setting, manual))
    return result


def _verify_candidates(candidates: dict[str, list[tuple[Setting, Manual]]]) -> None:
    for identity in IDENTITIES:
        rows = candidates[identity.canonical_id]
        vendors = {manual.vendor_id for _setting, manual in rows}
        if len(rows) != identity.expected_mappings or len(vendors) != identity.expected_vendors:
            raise RuntimeError(
                f"{identity.canonical_id}: expected {identity.expected_mappings} mappings "
                f"across {identity.expected_vendors} vendors; found {len(rows)} across "
                f"{len(vendors)}."
            )


def _ensure_identity(session, seed: SeedIdentity) -> CanonicalSetting:
    identity = session.get(CanonicalSetting, seed.canonical_id)
    if identity is None:
        return canonical_domain.create_identity(
            session,
            canonical_id=seed.canonical_id,
            name=seed.canonical_name,
            description=seed.description,
            aliases=seed.source_aliases,
            actor=SEED_ACTOR,
        )
    if identity.canonical_name != seed.canonical_name or identity.description != seed.description:
        raise RuntimeError(f"Existing identity {seed.canonical_id} differs from the seed.")
    for alias in seed.source_aliases:
        canonical_domain.add_alias(
            session,
            identity,
            alias=alias,
            actor=SEED_ACTOR,
        )
    return identity


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed seed")
    args = parser.parse_args()

    _verify_backup()
    with SessionLocal() as session:
        revision = session.execute(select(func.count()).select_from(CanonicalSetting)).scalar_one()
        candidates = _candidate_map(session)
        _verify_candidates(candidates)
        total = sum(len(rows) for rows in candidates.values())
        print(
            f"Canonical pilot: {len(IDENTITIES)} identities, {total} exact reviewed "
            f"mappings; existing identities={revision}."
        )
        if not args.apply:
            print("Dry run only; pass --apply after reviewing the pinned seed.")
            return

        for seed in IDENTITIES:
            _ensure_identity(session, seed)
            for setting, _manual in candidates[seed.canonical_id]:
                existing = session.get(SettingCanonicalMapping, setting.id)
                if existing is not None:
                    if existing.canonical_id != seed.canonical_id:
                        raise RuntimeError(
                            f"Setting {setting.id} already maps to {existing.canonical_id}."
                        )
                    continue
                canonical_domain.map_setting(
                    session,
                    setting,
                    canonical_id=seed.canonical_id,
                    note=SEED_NOTE,
                    actor=SEED_ACTOR,
                    mapping_method=canonical_domain.MAPPING_SEED,
                )

        identity_count = session.scalar(select(func.count()).select_from(CanonicalSetting))
        mapping_count = session.scalar(select(func.count()).select_from(SettingCanonicalMapping))
        print(
            f"Applied: canonical identities={identity_count}, confirmed mappings={mapping_count}."
        )


if __name__ == "__main__":
    main()
