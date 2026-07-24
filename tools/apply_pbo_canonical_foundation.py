"""Apply the reviewed TuneForge 0.18.0 PBO canonical foundation.

Dry-run is the default. The operation is pinned to the verified 0.17.0
production backup and 51 already-approved source settings. It creates no
tuning recommendations; it only establishes source-faithful identities needed
by the later trusted-evidence pilot.
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
from app.domain import canonical_settings
from app.domain.review import effective_name
from app.normalization.canonical_settings import normalize_alias

BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-pbo-foundation.sqlite3.backup"
)
EXPECTED_BACKUP_SHA256 = "35d8a32c693978998d6b3f3ab3bc1e463a43e89a3e4ccffe0dcd228b2e4cbd49"
ACTOR = "local-reviewer"
MAPPING_NOTE = "Release 0.18.0 PBO source name and description reviewed."


@dataclass(frozen=True)
class PboIdentity:
    canonical_id: str
    canonical_name: str
    description: str
    category_id: str
    rationale: str
    aliases: tuple[str, ...]
    settings: tuple[tuple[int, str], ...]


def _rows(name: str, *setting_ids: int) -> tuple[tuple[int, str], ...]:
    return tuple((setting_id, name) for setting_id in setting_ids)


IDENTITIES = (
    PboIdentity(
        "precision-boost-overdrive",
        "Precision Boost Overdrive",
        "Controls AMD Precision Boost Overdrive operation.",
        "cpu-performance",
        "Can raise processor power, voltage, temperature, and sustained boost behavior.",
        ("Ai Tweaker Precision Boost Overdrive", "AMD Overclocking Precision Boost Overdrive"),
        (
            *_rows("Precision Boost Overdrive", 50, 5256),
            *_rows("Ai Tweaker Precision Boost Overdrive", 5950),
            *_rows("AMD Overclocking Precision Boost Overdrive", 6541),
        ),
    ),
    PboIdentity(
        "pbo-limits",
        "PBO Limits",
        "Selects how Precision Boost Overdrive power and current limits are sourced.",
        "cpu-performance",
        "Can replace processor defaults with motherboard or manual power limits.",
        (),
        _rows("PBO Limits", 5257, 6542),
    ),
    PboIdentity(
        "package-power-tracking-limit",
        "Package Power Tracking Limit",
        "Sets the CPU package power tracking limit used by Precision Boost Overdrive.",
        "power-management",
        "Increasing the package power limit can raise electrical and thermal load.",
        ("PPT Limit", "PPT Limit [W]", "PPT Limit [mW]"),
        (
            *_rows("PPT Limit", 5952),
            *_rows("PPT Limit [W]", 5258),
            *_rows("PPT Limit [mW]", 6543),
        ),
    ),
    PboIdentity(
        "thermal-design-current-limit",
        "Thermal Design Current Limit",
        "Sets the sustained thermally constrained CPU current limit.",
        "power-management",
        "Increasing sustained current limits can raise VRM and processor temperature.",
        ("TDC Limit", "TDC Limit [A]", "TDC Limit [mA]"),
        (
            *_rows("TDC Limit", 5953),
            *_rows("TDC Limit [A]", 5259),
            *_rows("TDC Limit [mA]", 6544),
        ),
    ),
    PboIdentity(
        "electrical-design-current-limit",
        "Electrical Design Current Limit",
        "Sets the peak electrically constrained CPU current limit.",
        "power-management",
        "Increasing peak current limits can raise electrical and thermal stress.",
        ("EDC Limit", "EDC Limit [A]", "EDC Limit [mA]"),
        (
            *_rows("EDC Limit", 5954),
            *_rows("EDC Limit [A]", 5260),
            *_rows("EDC Limit [mA]", 6545),
        ),
    ),
    PboIdentity(
        "pbo-scalar-control",
        "PBO Scalar Control",
        "Selects automatic or manual control of the Precision Boost Overdrive scalar.",
        "cpu-performance",
        "Manual scalar control exposes a setting that can alter boost voltage behavior.",
        ("Precision Boost Overdrive Scalar Ctrl",),
        _rows("Precision Boost Overdrive Scalar Ctrl", 5261, 6546),
    ),
    PboIdentity(
        "pbo-scalar",
        "PBO Scalar",
        "Sets the Precision Boost Overdrive scalar value.",
        "cpu-performance",
        "Higher scalar values can extend elevated boost-voltage behavior.",
        ("Precision Boost Overdrive Scalar", "Customized Precision Boost Overdrive Scalar"),
        (
            *_rows("Precision Boost Overdrive Scalar", 5262, 5956, 6547),
            *_rows("Customized Precision Boost Overdrive Scalar", 5957),
        ),
    ),
    PboIdentity(
        "cpu-boost-clock-override",
        "CPU Boost Clock Override",
        "Controls whether the maximum automatic CPU boost clock is overridden.",
        "cpu-performance",
        "Changing the boost-clock ceiling can affect stability, performance, and temperature.",
        (),
        _rows("CPU Boost Clock Override", 5263, 5958, 6548),
    ),
    PboIdentity(
        "maximum-cpu-boost-clock-override-positive",
        "Maximum CPU Boost Clock Override Positive",
        "Sets a positive offset to the maximum automatic CPU boost clock.",
        "cpu-performance",
        "A positive frequency offset can cause instability and increase power or temperature.",
        (),
        _rows("Max CPU Boost Clock Override(+)", 5264, 5959, 6549),
    ),
    PboIdentity(
        "maximum-cpu-boost-clock-override-negative",
        "Maximum CPU Boost Clock Override Negative",
        "Sets a negative offset to the maximum automatic CPU boost clock.",
        "cpu-performance",
        "A negative frequency offset changes boost behavior and expected performance.",
        (),
        _rows("Max CPU Boost Clock Override(-)", 5265, 5960, 6550),
    ),
    PboIdentity(
        "platform-thermal-throttle-control",
        "Platform Thermal Throttle Control",
        "Selects automatic or manual control of the CPU thermal throttle limit.",
        "power-management",
        "Manual control exposes a processor thermal limit requiring hardware-specific review.",
        ("Platform Thermal Throttle Ctrl",),
        (
            *_rows("Platform Thermal Throttle Control", 5964),
            *_rows("Platform Thermal Throttle Ctrl", 6551),
        ),
    ),
    PboIdentity(
        "platform-thermal-throttle-limit",
        "Platform Thermal Throttle Limit",
        "Sets the maximum processor temperature used for platform thermal throttling.",
        "power-management",
        "Changing the thermal ceiling directly affects processor temperature policy.",
        ("Platform Thermal Throttle Limit(TjMax)",),
        (
            *_rows("Platform Thermal Throttle Limit", 5267, 5965, 6552),
            *_rows("Platform Thermal Throttle Limit(TjMax)", 241),
        ),
    ),
    PboIdentity(
        "curve-optimizer",
        "Curve Optimizer",
        "Selects AMD CPU voltage-frequency curve optimization scope and operation.",
        "cpu-performance",
        "Curve changes can create workload-specific instability that requires validation.",
        ("Ai Tweaker Curve Optimizer", "AMD Overclocking Curve Optimizer"),
        (
            *_rows("Curve Optimizer", 5273),
            *_rows("Ai Tweaker Curve Optimizer", 5967),
            *_rows("AMD Overclocking Curve Optimizer", 6558),
        ),
    ),
    PboIdentity(
        "all-core-curve-optimizer-sign",
        "All-Core Curve Optimizer Sign",
        "Sets the positive or negative Curve Optimizer direction for all CPU cores.",
        "cpu-performance",
        "The selected direction changes the all-core voltage-frequency curve.",
        ("All Core Curve Optimizer Sign",),
        _rows("All Core Curve Optimizer Sign", 5274, 5968, 6559),
    ),
    PboIdentity(
        "all-core-curve-optimizer-magnitude",
        "All-Core Curve Optimizer Magnitude",
        "Sets the Curve Optimizer magnitude applied to all CPU cores.",
        "cpu-performance",
        "Larger all-core curve shifts can cause instability on one or more cores.",
        ("All Core Curve Optimizer Magnitude",),
        _rows("All Core Curve Optimizer Magnitude", 5275, 5969, 6560),
    ),
    PboIdentity(
        "per-core-curve-optimizer-sign",
        "Per-Core Curve Optimizer Sign",
        "Sets the positive or negative Curve Optimizer direction for an individual CPU core.",
        "cpu-performance",
        "Per-core curve direction changes require validation for each affected core.",
        (
            "Core[n] Curve Optimizer Sign",
            "Core 0-63 Curve Optimizer Sign",
            "Core 0-7 Curve Optimizer Sign",
        ),
        (
            *_rows("Core[n] Curve Optimizer Sign", 5276),
            *_rows("Core 0-63 Curve Optimizer Sign", 5970),
            *_rows("Core 0-7 Curve Optimizer Sign", 6561),
        ),
    ),
    PboIdentity(
        "per-core-curve-optimizer-magnitude",
        "Per-Core Curve Optimizer Magnitude",
        "Sets the Curve Optimizer magnitude for an individual CPU core.",
        "cpu-performance",
        "Per-core curve magnitude changes can create core-specific instability.",
        (
            "Core[n]s Curve Optimizer Magnitude",
            "Core 0-63 Curve Optimizer Magnitude",
            "Core 0-7 Curve Optimizer Magnitude",
        ),
        (
            *_rows("Core[n]s Curve Optimizer Magnitude", 5277),
            *_rows("Core 0-63 Curve Optimizer Magnitude", 5971),
            *_rows("Core 0-7 Curve Optimizer Magnitude", 6562),
        ),
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
        raise RuntimeError("The verified pre-PBO-foundation backup is missing or changed.")


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _verify_baseline(session) -> None:
    expected = {
        CanonicalSetting: (20, 37),
        CanonicalSettingAlias: (32, 73),
        SettingCanonicalMapping: (153, 204),
        CanonicalMappingEvent: (153, 204),
        CanonicalSettingClassification: (20, 37),
        CanonicalClassificationEvent: (20, 54),
        CanonicalMappingSuggestion: 16,
        CanonicalSuggestionEvent: 32,
    }
    for model, allowed in expected.items():
        found = _count(session, model)
        values = allowed if isinstance(allowed, tuple) else (allowed,)
        if found not in values:
            raise RuntimeError(f"{model.__tablename__}: expected one of {values}, found {found}.")

    seen: set[int] = set()
    for identity in IDENTITIES:
        for setting_id, expected_name in identity.settings:
            if setting_id in seen:
                raise RuntimeError(f"Pinned setting {setting_id} appears more than once.")
            seen.add(setting_id)
            setting = session.get(Setting, setting_id)
            if setting is None or setting.status != "approved":
                raise RuntimeError(f"Pinned setting {setting_id} is missing or not approved.")
            if normalize_alias(effective_name(setting)) != normalize_alias(expected_name):
                raise RuntimeError(
                    f"Pinned setting {setting_id} no longer matches {expected_name!r}."
                )
    if len(seen) != 51:
        raise RuntimeError(f"Expected 51 unique reviewed source settings, found {len(seen)}.")


def _apply(session) -> None:
    for seed in IDENTITIES:
        identity = session.get(CanonicalSetting, seed.canonical_id)
        if identity is None:
            identity = canonical_settings.create_identity(
                session,
                canonical_id=seed.canonical_id,
                name=seed.canonical_name,
                description=seed.description,
                aliases=seed.aliases,
                actor=ACTOR,
            )
        elif (
            identity.canonical_name != seed.canonical_name
            or identity.description != seed.description
        ):
            raise RuntimeError(f"Existing identity {seed.canonical_id} differs from the seed.")
        for alias in seed.aliases:
            canonical_settings.add_alias(session, identity, alias=alias, actor=ACTOR)
        canonical_settings.classify_identity(
            session,
            identity,
            category_id=seed.category_id,
            risk_level="high",
            risk_rationale=seed.rationale,
            actor=ACTOR,
        )
        for setting_id, _expected_name in seed.settings:
            setting = session.get(Setting, setting_id)
            mapping = session.get(SettingCanonicalMapping, setting_id)
            if mapping is not None:
                if mapping.canonical_id != seed.canonical_id:
                    raise RuntimeError(
                        f"Setting {setting_id} already maps to {mapping.canonical_id}."
                    )
                continue
            canonical_settings.map_setting(
                session,
                setting,
                canonical_id=seed.canonical_id,
                note=MAPPING_NOTE,
                actor=ACTOR,
                mapping_method=canonical_settings.MAPPING_SEED,
            )


def _verify_final(session) -> None:
    expected = {
        CanonicalSetting: 37,
        CanonicalSettingAlias: 73,
        SettingCanonicalMapping: 204,
        CanonicalMappingEvent: 204,
        CanonicalSettingClassification: 37,
        CanonicalClassificationEvent: 54,
        CanonicalMappingSuggestion: 16,
        CanonicalSuggestionEvent: 32,
    }
    failures = []
    for model, value in expected.items():
        found = _count(session, model)
        if found != value:
            failures.append(f"{model.__tablename__}: expected {value}, found {found}")
    if failures:
        raise RuntimeError("PBO foundation verification failed:\n- " + "\n- ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed foundation")
    args = parser.parse_args()

    _verify_backup()
    with SessionLocal() as session:
        _verify_baseline(session)
        print("PBO foundation: 17 identities and 51 reviewed source mappings.")
        if not args.apply:
            print("Dry run only; pass --apply after reviewing the pinned operation.")
            return
        _apply(session)
        _verify_final(session)
        print("Applied: identities=37, aliases=73, mappings=204.")


if __name__ == "__main__":
    main()
