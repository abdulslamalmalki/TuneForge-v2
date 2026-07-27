"""Apply TuneForge's reviewed v1.5 official-manual canonical identity batch.

Dry-run is the default. The operation is pinned to the verified v1.5 backup,
the revision-0019 production baseline, 35 reviewed source settings, and 16
deterministic identity proposals. Fifteen proposals are approved from cited
official-manual evidence; the ambiguous Clear Secure Boot Keys proposal is
rejected and its four sources remain unmapped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from app import config
from app.db.models import (
    CanonicalClassificationEvent,
    CanonicalIdentityEvent,
    CanonicalIdentityProposal,
    CanonicalIdentityProposalEvent,
    CanonicalIdentityProposalSetting,
    CanonicalMappingEvent,
    CanonicalSetting,
    CanonicalSettingAlias,
    CanonicalSettingClassification,
    Manual,
    Setting,
    SettingCanonicalMapping,
    SettingMenuContext,
    SettingMenuContextCitation,
    SettingMenuContextEvent,
)
from app.db.session import SessionLocal
from app.domain import canonical_identity_review, canonicalization
from app.domain.extraction_runs import utc_now_iso
from app.domain.review import effective_description, effective_name
from app.normalization.canonical_settings import normalize_alias
from app.operations.backup import inspect_backup

ROOT = Path(__file__).resolve().parents[1]
BACKUP_PATH = ROOT / "data" / "backups" / "tuneforge-v1.5-pre-backlog-review.zip"
EXPECTED_BACKUP_SHA256 = "535fca2617ff2d7db0e27c58cdbd1ec70036a21528b52acc30afac0fcb152504"
REPORT_JSON = ROOT / "data" / "reports" / "v1.5-canonicalization-final.json"
REPORT_MARKDOWN = ROOT / "docs" / "v1.5-canonicalization-report.md"
ACTOR = "v1.5-official-manual-reviewer"


@dataclass(frozen=True)
class ContextSeed:
    setting_id: int
    manual_id: int
    vendor_id: str
    page_number: int
    setting_name: str
    menu_path: str
    evidence_excerpt: str
    supporting_citations: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class DecisionSeed:
    canonical_id: str
    setting_ids: tuple[int, ...]
    rationale: str
    uncertainty: str


OFFICIAL_SOURCES = (
    {
        "manual_id": 2,
        "publisher": "ASRock",
        "title": "AMD 800 Series Motherboard Software/BIOS Setup Guide",
        "url": "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
    },
    {
        "manual_id": 8,
        "publisher": "ASUS",
        "title": "PRIME / ProArt / TUF GAMING AMD 800 Series BIOS Manual",
        "url": (
            "https://www.asus.com/us/supportonly/tuf%20gaming%20x870-plus%20wifi/helpdesk_manual/"
        ),
    },
    {
        "manual_id": 7,
        "publisher": "GIGABYTE",
        "title": "BIOS Setup (Intel 800 Series)",
        "url": (
            "https://www.gigabyte.com/Motherboard/Z890-AORUS-ELITE-WIFI7/support#support-manual"
        ),
    },
    {
        "manual_id": 11,
        "publisher": "ASUS",
        "title": "Intel 600 Series BIOS Manual",
        "url": (
            "https://www.asus.com/us/motherboards-components/motherboards/prime/"
            "prime-b660-plus-d4/helpdesk_manual?model2Name=PRIME-B660-PLUS-D4"
        ),
    },
    {
        "manual_id": 12,
        "publisher": "ASUS",
        "title": "Intel 700 Series BIOS Manual",
        "url": (
            "https://www.asus.com/motherboards-components/motherboards/tuf-gaming/"
            "tuf-gaming-b760m-plus/helpdesk_manual?model2Name=TUF-GAMING-B760M-PLUS"
        ),
    },
)


CONTEXTS = (
    ContextSeed(
        346,
        2,
        "asrock",
        53,
        "L1 Stream HW Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L1 Stream HW Prefetcher",
        "3.4.8 AMD CBS. CPU Common Options. Prefetcher Settings. L1 Stream HW Prefetcher.",
    ),
    ContextSeed(
        6170,
        8,
        "asus",
        57,
        "L1 Stream HW Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L1 Stream HW Prefetcher",
        "6.14 AMD CBS. CPU Common Options. Prefetcher settings. L1 Stream HW Prefetcher.",
    ),
    ContextSeed(
        347,
        2,
        "asrock",
        53,
        "L2 Stream HW Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L2 Stream HW Prefetcher",
        "3.4.8 AMD CBS. CPU Common Options. Prefetcher Settings. L2 Stream HW Prefetcher.",
    ),
    ContextSeed(
        6171,
        8,
        "asus",
        57,
        "L2 Stream HW Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L2 Stream HW Prefetcher",
        "6.14 AMD CBS. CPU Common Options. Prefetcher settings. L2 Stream HW Prefetcher.",
    ),
    ContextSeed(
        348,
        2,
        "asrock",
        53,
        "L1 Stride Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L1 Stride Prefetcher",
        "3.4.8 AMD CBS. CPU Common Options. Prefetcher Settings. L1 Stride Prefetcher.",
    ),
    ContextSeed(
        6172,
        8,
        "asus",
        57,
        "L1 Stride Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L1 Stride Prefetcher",
        "6.14 AMD CBS. CPU Common Options. Prefetcher settings. L1 Stride Prefetcher.",
    ),
    ContextSeed(
        349,
        2,
        "asrock",
        54,
        "L1 Region Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L1 Region Prefetcher",
        "L1 Region Prefetcher. Uses memory access history to fetch additional lines.",
        ((53, "3.4.8 AMD CBS. CPU Common Options. Prefetcher Settings."),),
    ),
    ContextSeed(
        6173,
        8,
        "asus",
        57,
        "L1 Region Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L1 Region Prefetcher",
        "6.14 AMD CBS. CPU Common Options. Prefetcher settings. L1 Region Prefetcher.",
    ),
    ContextSeed(
        351,
        2,
        "asrock",
        54,
        "L2 Up/Down Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher Settings > L2 Up/Down Prefetcher",
        "L2 Up/Down Prefetcher. Uses memory access history to determine whether "
        "to fetch the next or previous line.",
        ((53, "3.4.8 AMD CBS. CPU Common Options. Prefetcher Settings."),),
    ),
    ContextSeed(
        6175,
        8,
        "asus",
        58,
        "L2 Up/Down Prefetcher",
        "Advanced > AMD CBS > CPU Common Options > Prefetcher settings > L2 Up/Down Prefetcher",
        "L2 Up/Down Prefetcher. Uses memory access history to determine whether "
        "to fetch the next or previous line.",
        ((57, "6.14 AMD CBS. CPU Common Options. Prefetcher settings."),),
    ),
    ContextSeed(
        362,
        2,
        "asrock",
        55,
        "Location of private memory regions",
        "Advanced > AMD CBS > DF Common Options > Memory Addressing > "
        "Location of private memory regions",
        "Memory Addressing. Location of private memory regions.",
        ((53, "3.4.8 AMD CBS."), (54, "DF Common Options.")),
    ),
    ContextSeed(
        6213,
        8,
        "asus",
        61,
        "Location of private memory regions",
        "Advanced > AMD CBS > DF Common Options > Memory Addressing > "
        "Location of private memory regions",
        "Memory Addressing. Location of private memory regions.",
        ((57, "6.14 AMD CBS."), (60, "DF Common Options.")),
    ),
    ContextSeed(
        366,
        2,
        "asrock",
        55,
        "Freeze DF module queues on error",
        "Advanced > AMD CBS > DF Common Options > Freeze DF module queues on error",
        "Freeze DF module queues on error. Enables freezing of all DF queues on error.",
        ((53, "3.4.8 AMD CBS."), (54, "DF Common Options.")),
    ),
    ContextSeed(
        6218,
        8,
        "asus",
        61,
        "Freeze DF module queues on error",
        "Advanced > AMD CBS > DF Common Options > Freeze DF module queues on error",
        "Freeze DF module queues on error. Enables freezing of all DF queues on error.",
        ((57, "6.14 AMD CBS."), (60, "DF Common Options.")),
    ),
    ContextSeed(
        426,
        2,
        "asrock",
        60,
        "Chipselect Interleaving",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Addressing Options > Chipselect Interleaving",
        "DDR Addressing Options. Chipselect Interleaving.",
        ((53, "3.4.8 AMD CBS."), (59, "UMC Common Options. DDR Options.")),
    ),
    ContextSeed(
        6312,
        8,
        "asus",
        69,
        "Chipselect Interleaving",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Addressing Options > Chipselect Interleaving",
        "DDR Addressing Options. Chipselect Interleaving.",
        ((57, "6.14 AMD CBS."), (61, "UMC Common Options. DDR Options.")),
    ),
    ContextSeed(
        436,
        2,
        "asrock",
        61,
        "DDR Training Runtime Reduction",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR Training Runtime Reduction",
        "DDR Training Runtime Reduction. Force to Disable or Enable DDR Training "
        "Runtime Reduction.",
        ((53, "3.4.8 AMD CBS."), (60, "UMC Common Options. DDR Options. DDR Training Options.")),
    ),
    ContextSeed(
        6322,
        8,
        "asus",
        70,
        "DDR Training Runtime Reduction",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR Training Runtime Reduction",
        "DDR Training Runtime Reduction. Force Disable or Force Enable DDR Training "
        "Runtime Reduction.",
        (
            (57, "6.14 AMD CBS."),
            (61, "UMC Common Options. DDR Options."),
            (69, "DDR Training Options."),
        ),
    ),
    ContextSeed(
        451,
        2,
        "asrock",
        62,
        "MBIST Test Mode",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR Memory MBIST > MBIST Test Mode",
        "DDR Memory MBIST. MBIST Test Mode. Interface Mode or Data Eye Mode.",
        ((53, "3.4.8 AMD CBS."), (60, "UMC Common Options. DDR Options. DDR Training Options.")),
    ),
    ContextSeed(
        6338,
        8,
        "asus",
        71,
        "MBIST Test Mode",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR MBIST Options > MBIST Test Mode",
        "DDR MBIST Options. MBIST Test Mode. Interface Mode or Data Eye Mode.",
        (
            (57, "6.14 AMD CBS."),
            (61, "UMC Common Options. DDR Options."),
            (69, "DDR Training Options."),
        ),
    ),
    ContextSeed(
        452,
        2,
        "asrock",
        62,
        "MBIST Aggressors",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR Memory MBIST > MBIST Aggressors",
        "DDR Memory MBIST. MBIST Aggressors. Enable or disable MBIST aggressor test.",
        ((53, "3.4.8 AMD CBS."), (60, "UMC Common Options. DDR Options. DDR Training Options.")),
    ),
    ContextSeed(
        6339,
        8,
        "asus",
        71,
        "MBIST Aggressors",
        "Advanced > AMD CBS > UMC Common Options > DDR Options > "
        "DDR Training Options > DDR MBIST Options > MBIST Aggressors",
        "DDR MBIST Options. MBIST Aggressors. Enable or disable Memory Aggressor test.",
        (
            (57, "6.14 AMD CBS."),
            (61, "UMC Common Options. DDR Options."),
            (69, "DDR Training Options."),
        ),
    ),
    ContextSeed(
        475,
        2,
        "asrock",
        64,
        "GPU Host Translation Cache",
        "Advanced > AMD CBS > NBIO Common Options > GFX Configuration > GPU Host Translation Cache",
        "GFX Configuration. GPU Host Translation Cache.",
        ((53, "3.4.8 AMD CBS."), (63, "NBIO Common Options.")),
    ),
    ContextSeed(
        6364,
        8,
        "asus",
        73,
        "GPU Host Translation Cache",
        "Advanced > AMD CBS > NBIO Common Options > GFX Configuration > GPU Host Translation Cache",
        "GFX Configuration. GPU Host Translation Cache.",
        ((57, "6.14 AMD CBS."), (72, "NBIO Common Options.")),
    ),
    ContextSeed(
        479,
        2,
        "asrock",
        64,
        "PCIe loopback Mode",
        "Advanced > AMD CBS > NBIO Common Options > PCIe loopback Mode",
        "PCIe loopback Mode. Enable or disable PCIe loopback Mode.",
        ((53, "3.4.8 AMD CBS."), (63, "NBIO Common Options.")),
    ),
    ContextSeed(
        6369,
        8,
        "asus",
        73,
        "PCIe loopback Mode",
        "Advanced > AMD CBS > NBIO Common Options > PCIe loopback Mode",
        "PCIe loopback Mode. Enable or disable PcieLoopBackMode.",
        ((57, "6.14 AMD CBS."), (72, "NBIO Common Options.")),
    ),
    ContextSeed(
        481,
        2,
        "asrock",
        64,
        "Retimer margining support",
        "Advanced > AMD CBS > NBIO Common Options > Retimer margining support",
        "Retimer margining support. Enables support for margining a retimer.",
        ((53, "3.4.8 AMD CBS."), (63, "NBIO Common Options.")),
    ),
    ContextSeed(
        6372,
        8,
        "asus",
        73,
        "Retimer margining support",
        "Advanced > AMD CBS > NBIO Common Options > Retimer margining support",
        "Retimer margining support. Enables support for margining a retimer.",
        ((57, "6.14 AMD CBS."), (72, "NBIO Common Options.")),
    ),
    ContextSeed(
        5543,
        7,
        "gigabyte",
        11,
        "Core Ratio Extension Mode",
        "Tweaker > Advanced CPU Settings > Core Ratio Extension Mode",
        "Core Ratio Extension Mode. Enables or disables Core Ratio Above 85 "
        "Extension Mode; the documented OCMB limit changes from 85 to 120.",
        ((9, "Tweaker."), (10, "Advanced CPU Settings.")),
    ),
    ContextSeed(
        7406,
        11,
        "asus",
        39,
        "Core Ratio Extension Mode",
        "Ai Tweaker > Tweaker's Paradise > Core Ratio Extension Mode",
        "Core Ratio Extension Mode. Enables or disables Core Ratio Above 85 "
        "Extension Mode; the documented OCMB limit changes from 85 to 120.",
        ((17, "5. Ai Tweaker menu."), (38, "Tweaker's Paradise.")),
    ),
    ContextSeed(
        8057,
        12,
        "asus",
        41,
        "Core Ratio Extension Mode",
        "Ai Tweaker > Tweaker's Paradise > Core Ratio Extension Mode",
        "Tweaker's Paradise. Core Ratio Extension Mode. Enables or disables Core "
        "Ratio Above 85 Extension Mode; the documented OCMB limit changes from 85 to 120.",
        ((19, "5. Ai Tweaker menu."),),
    ),
)


DECISIONS = (
    DecisionSeed(
        "chipselect-interleaving",
        (426, 6312),
        "Same memory-interleaving function and options in two official AMD 800-series "
        "vendor manuals.",
        "None material.",
    ),
    DecisionSeed(
        "core-ratio-extension-mode",
        (5543, 7406, 8057),
        "All three manuals document Core Ratio Above 85 Extension Mode and the same "
        "OCMB limits of 85 when disabled and 120 when enabled.",
        "Menu hierarchy differs by vendor, but function and option effects are identical.",
    ),
    DecisionSeed(
        "ddr-training-runtime-reduction",
        (436, 6322),
        "Same DDR training runtime control, Auto behavior, and forced Enabled/Disabled choices.",
        "None material.",
    ),
    DecisionSeed(
        "freeze-df-module-queues-on-error",
        (366, 6218),
        "Same DF queue freeze and HWA sync-flood behavior in both official manuals.",
        "None material.",
    ),
    DecisionSeed(
        "gpu-host-translation-cache",
        (475, 6364),
        "Same GPU host translation cache toggle under NBIO GFX Configuration.",
        "None material.",
    ),
    DecisionSeed(
        "l1-region-prefetcher",
        (349, 6173),
        "Same L1 region-prefetch behavior, dependencies, and Auto/Disabled/Enabled choices.",
        "ASRock contains a typographical error in the prose; the function remains unambiguous.",
    ),
    DecisionSeed(
        "l1-stream-hw-prefetcher",
        (346, 6170),
        "Same L1 stream hardware prefetch control and option set.",
        "None material.",
    ),
    DecisionSeed(
        "l1-stride-prefetcher",
        (348, 6172),
        "Same instruction-history stride-prefetch behavior and option set.",
        "None material.",
    ),
    DecisionSeed(
        "l2-stream-hw-prefetcher",
        (347, 6171),
        "Same L2 stream hardware prefetch control and option set.",
        "None material.",
    ),
    DecisionSeed(
        "l2-up-down-prefetcher",
        (351, 6175),
        "Same next/previous-line prefetch behavior and option set.",
        "None material.",
    ),
    DecisionSeed(
        "location-of-private-memory-regions",
        (362, 6213),
        "Same PSP/SMU/CC6 private-region placement choices and constraints.",
        "Minor wording omission in the ASUS extraction does not change the documented "
        "function or choices.",
    ),
    DecisionSeed(
        "mbist-aggressors",
        (452, 6339),
        "Same MBIST memory-aggressor test toggle in both official manuals.",
        "Vendor submenu wording differs (DDR Memory MBIST versus DDR MBIST Options).",
    ),
    DecisionSeed(
        "mbist-test-mode",
        (451, 6338),
        "Same Interface Mode/Data Eye Mode MBIST behavior and choices.",
        "Vendor submenu wording differs (DDR Memory MBIST versus DDR MBIST Options).",
    ),
    DecisionSeed(
        "pcie-loopback-mode",
        (479, 6369),
        "Same PCIe loopback mode toggle under NBIO Common Options.",
        "Capitalization and compact spelling differ only in descriptive prose.",
    ),
    DecisionSeed(
        "retimer-margining-support",
        (481, 6372),
        "Same retimer margining support behavior and Auto/Enabled/Disabled semantics.",
        "None material.",
    ),
)

REVIEWED_DESCRIPTIONS = {
    "chipselect-interleaving": (
        "Interleaves memory blocks across the DRAM chip selects for node 0."
    ),
    "core-ratio-extension-mode": (
        "Extends the maximum CPU core ratio exposed through OCMB from 85 to 120 when enabled."
    ),
    "ddr-training-runtime-reduction": (
        "Controls whether firmware reduces DDR training runtime; Auto follows firmware "
        "defaults and may disable the reduction when memory overclocking is enabled."
    ),
    "freeze-df-module-queues-on-error": (
        "Freezes all Data Fabric queues on error and forces a synchronization flood on "
        "hardware assertion even when machine-check reporting is disabled."
    ),
    "gpu-host-translation-cache": ("Enables or disables the GPU host translation cache."),
    "l1-region-prefetcher": (
        "Uses memory access history to prefetch additional L1 cache lines when an "
        "instruction's access tends to be followed by nearby accesses."
    ),
    "l1-stream-hw-prefetcher": ("Enables or disables the L1 stream hardware prefetcher."),
    "l1-stride-prefetcher": (
        "Uses instruction-specific memory access history to prefetch additional L1 "
        "cache lines at a constant stride."
    ),
    "l2-stream-hw-prefetcher": ("Enables or disables the L2 stream hardware prefetcher."),
    "l2-up-down-prefetcher": (
        "Uses memory access history to prefetch the next or previous L2 cache line."
    ),
    "location-of-private-memory-regions": (
        "Controls whether PSP, SMU, and CC6 private memory regions are distributed or "
        "consolidated in DRAM."
    ),
    "mbist-aggressors": (
        "Enables or disables memory aggressor testing during DDR memory built-in self-test."
    ),
    "mbist-test-mode": (
        "Selects Interface Mode, Data Eye Mode, or both for DDR memory built-in self-test."
    ),
    "pcie-loopback-mode": "Enables or disables PCIe loopback mode.",
    "retimer-margining-support": ("Controls whether firmware supports margining a PCIe retimer."),
}

AMBIGUOUS_CANONICAL_ID = "clear-secure-boot-keys"
AMBIGUOUS_SETTING_IDS = (572, 6690, 7657, 8320)
AMBIGUITY_REASON = (
    "v1.5 official-manual review rejected this identity proposal. ASUS places the "
    "setting under Boot > Secure Boot > Key Management, while ASRock page 80 prints "
    "the same label at two hierarchy levels. The function text agrees, but the menu "
    "identity and hierarchy cannot be established without ambiguity. All four source "
    "settings remain unmapped pending clearer official evidence."
)


BASELINE_COUNTS = {
    CanonicalSetting: 41,
    CanonicalSettingAlias: 77,
    SettingCanonicalMapping: 212,
    CanonicalMappingEvent: 212,
    CanonicalIdentityEvent: 41,
    SettingMenuContext: 8,
    SettingMenuContextEvent: 8,
    SettingMenuContextCitation: 35,
    CanonicalIdentityProposal: 4,
    CanonicalIdentityProposalSetting: 8,
    CanonicalIdentityProposalEvent: 12,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError("The verified pre-v1.5 backup is missing.")
    if _sha256(BACKUP_PATH) != EXPECTED_BACKUP_SHA256:
        raise RuntimeError("The verified pre-v1.5 backup checksum changed.")
    inspection = inspect_backup(BACKUP_PATH)
    if inspection.database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("The verified backup has an unexpected database revision.")


def _expected_groups() -> dict[str, tuple[int, ...]]:
    values = {seed.canonical_id: seed.setting_ids for seed in DECISIONS}
    values[AMBIGUOUS_CANONICAL_ID] = AMBIGUOUS_SETTING_IDS
    return values


def _verify_manifest() -> None:
    context_ids = [seed.setting_id for seed in CONTEXTS]
    decision_ids = [setting_id for seed in DECISIONS for setting_id in seed.setting_ids]
    if len(context_ids) != 31 or len(set(context_ids)) != 31:
        raise RuntimeError("The menu-context manifest must contain 31 unique settings.")
    if set(context_ids) != set(decision_ids):
        raise RuntimeError("Menu-context and approval manifests disagree.")
    if set(REVIEWED_DESCRIPTIONS) != {seed.canonical_id for seed in DECISIONS}:
        raise RuntimeError("Every approved identity requires one reviewed description.")
    all_group_ids = [setting_id for values in _expected_groups().values() for setting_id in values]
    if len(all_group_ids) != 35 or len(set(all_group_ids)) != 35:
        raise RuntimeError("The proposal manifest must contain 35 unique settings.")
    if set(context_ids) & set(AMBIGUOUS_SETTING_IDS):
        raise RuntimeError("Ambiguous Secure Boot sources must not receive reviewed paths.")
    citation_keys = [
        (seed.setting_id, page)
        for seed in CONTEXTS
        for page, _ in ((seed.page_number, seed.evidence_excerpt), *seed.supporting_citations)
    ]
    if len(citation_keys) != len(set(citation_keys)):
        raise RuntimeError("A setting has duplicate menu citation pages.")


def _verify_baseline(session) -> canonicalization.CanonicalizationEvaluation:
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(
            f"Expected database revision {config.EXPECTED_DATABASE_REVISION}, found {revision}."
        )
    integrity = session.scalar(text("PRAGMA integrity_check"))
    if integrity != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    for model, expected in BASELINE_COUNTS.items():
        found = _count(session, model)
        if found != expected:
            raise RuntimeError(f"{model.__tablename__}: expected {expected}, found {found}.")
    unmapped = session.scalar(
        select(func.count())
        .select_from(Setting)
        .outerjoin(
            SettingCanonicalMapping,
            SettingCanonicalMapping.setting_id == Setting.id,
        )
        .where(
            Setting.status == "approved",
            SettingCanonicalMapping.setting_id.is_(None),
        )
    )
    if unmapped != 3809:
        raise RuntimeError(f"Expected 3,809 approved unmapped settings, found {unmapped}.")

    source_by_manual = {int(value["manual_id"]): value for value in OFFICIAL_SOURCES}
    for manual_id, source in source_by_manual.items():
        manual = session.get(Manual, manual_id)
        if manual is None or manual.source_url != source["url"]:
            raise RuntimeError(f"Official manual {manual_id} provenance changed.")

    seed_by_setting = {seed.setting_id: seed for seed in CONTEXTS}
    expected_setting_ids = set(seed_by_setting) | set(AMBIGUOUS_SETTING_IDS)
    rows = session.execute(
        select(Setting, Manual)
        .join(Manual, Manual.id == Setting.manual_id)
        .where(Setting.id.in_(expected_setting_ids))
    )
    found_setting_ids = set()
    for setting, manual in rows:
        found_setting_ids.add(setting.id)
        if setting.status != "approved" or setting.page_review_status not in {
            "correct",
            "corrected",
        }:
            raise RuntimeError(f"Pinned setting {setting.id} is no longer fully approved.")
        if session.get(SettingCanonicalMapping, setting.id) is not None:
            raise RuntimeError(f"Pinned setting {setting.id} is already mapped.")
        context_seed = seed_by_setting.get(setting.id)
        if context_seed is not None:
            actual_page = setting.edited_page_number or setting.page_number
            actual = (
                setting.manual_id,
                manual.vendor_id,
                actual_page,
                normalize_alias(effective_name(setting)),
            )
            expected = (
                context_seed.manual_id,
                context_seed.vendor_id,
                context_seed.page_number,
                normalize_alias(context_seed.setting_name),
            )
            if actual != expected:
                raise RuntimeError(
                    f"Pinned setting {setting.id} changed: expected {expected}, found {actual}."
                )
        if canonicalization.source_trust_reasons(setting, manual):
            raise RuntimeError(f"Pinned setting {setting.id} no longer passes trust gates.")
        if not effective_description(setting).strip():
            raise RuntimeError(f"Pinned setting {setting.id} lost its reviewed description.")
    if found_setting_ids != expected_setting_ids:
        raise RuntimeError("One or more pinned source settings are missing.")
    existing_contexts = session.scalar(
        select(func.count())
        .select_from(SettingMenuContext)
        .where(SettingMenuContext.setting_id.in_(expected_setting_ids))
    )
    if existing_contexts:
        raise RuntimeError("A pinned source already has menu context; baseline changed.")

    evaluation = canonicalization.evaluate_backlog(session)
    groups = {group.canonical_id: group.setting_ids for group in evaluation.proposed_groups}
    if groups != _expected_groups():
        raise RuntimeError(
            "Description-aware proposal set differs from the reviewed manifest: "
            + json.dumps({"expected": _expected_groups(), "actual": groups}, sort_keys=True)
        )
    if any(entry.automatic_eligible for entry in evaluation.entries):
        raise RuntimeError("Unexpected automatic mapping became eligible before this batch.")
    return evaluation


def _record_contexts(session) -> None:
    for seed in CONTEXTS:
        canonical_identity_review.review_menu_context(
            session,
            seed.setting_id,
            menu_path=seed.menu_path,
            source_page_number=seed.page_number,
            evidence_excerpt=seed.evidence_excerpt,
            supporting_citations=seed.supporting_citations,
            note=(
                "v1.5 official-manual hierarchy review; page and ordered menu path "
                "visually verified against the stored source PDF."
            ),
            actor=ACTOR,
            commit=False,
        )


def _approval_note(session, seed: DecisionSeed, confidence: int) -> str:
    rows = list(
        session.execute(
            select(Setting, Manual, SettingMenuContext)
            .join(Manual, Manual.id == Setting.manual_id)
            .join(SettingMenuContext, SettingMenuContext.setting_id == Setting.id)
            .where(Setting.id.in_(seed.setting_ids))
            .order_by(Setting.id)
        )
    )
    sources = "; ".join(
        f"{manual.vendor} manual {manual.id} p{setting.edited_page_number or setting.page_number} "
        f"({manual.source_url}) at {context.menu_path}"
        for setting, manual, context in rows
    )
    return (
        "v1.5 official-manual identity approval. Exact normalized source names agree; "
        "reviewed descriptions, behavior, choices, dependencies, document context, and "
        f"cited menu hierarchy agree. {seed.rationale} Evidence: {sources}. "
        f"Deterministic confidence: {confidence / 100:.2f}%. Remaining uncertainty: "
        f"{seed.uncertainty}"
    )


def _count_citations() -> int:
    return sum(1 + len(seed.supporting_citations) for seed in CONTEXTS)


def _snapshot_counts(session) -> dict[type, int]:
    models = (
        *BASELINE_COUNTS,
        CanonicalSettingClassification,
        CanonicalClassificationEvent,
    )
    return {model: _count(session, model) for model in models}


def _verify_final(session, baseline: dict[type, int]) -> None:
    expected_deltas = {
        CanonicalSetting: 15,
        CanonicalSettingAlias: 15,
        SettingCanonicalMapping: 31,
        CanonicalMappingEvent: 31,
        CanonicalIdentityEvent: 15,
        CanonicalSettingClassification: 15,
        CanonicalClassificationEvent: 15,
        SettingMenuContext: 31,
        SettingMenuContextEvent: 31,
        SettingMenuContextCitation: _count_citations(),
        CanonicalIdentityProposal: 16,
        CanonicalIdentityProposalSetting: 35,
        CanonicalIdentityProposalEvent: 32,
    }
    failures = []
    for model, delta in expected_deltas.items():
        found = _count(session, model)
        expected = baseline[model] + delta
        if found != expected:
            failures.append(f"{model.__tablename__}: expected {expected}, found {found}")
    for seed in DECISIONS:
        identity = session.get(CanonicalSetting, seed.canonical_id)
        if identity is None or identity.status != "active":
            failures.append(f"missing active identity: {seed.canonical_id}")
        mapped_ids = tuple(
            session.scalars(
                select(SettingCanonicalMapping.setting_id)
                .where(SettingCanonicalMapping.canonical_id == seed.canonical_id)
                .order_by(SettingCanonicalMapping.setting_id)
            )
        )
        if mapped_ids != seed.setting_ids:
            failures.append(
                f"{seed.canonical_id}: expected mappings {seed.setting_ids}, found {mapped_ids}"
            )
    for setting_id in AMBIGUOUS_SETTING_IDS:
        if session.get(SettingCanonicalMapping, setting_id) is not None:
            failures.append(f"ambiguous setting {setting_id} was mapped")
        if session.get(SettingMenuContext, setting_id) is not None:
            failures.append(f"ambiguous setting {setting_id} received menu context")
    ambiguous = session.scalar(
        select(CanonicalIdentityProposal).where(
            CanonicalIdentityProposal.canonical_id == AMBIGUOUS_CANONICAL_ID
        )
    )
    if ambiguous is None or ambiguous.status != "rejected":
        failures.append("Clear Secure Boot Keys proposal is not rejected")
    integrity = session.scalar(text("PRAGMA integrity_check"))
    if integrity != "ok":
        failures.append(f"SQLite integrity check: {integrity}")
    evaluation = canonicalization.evaluate_backlog(session)
    if len(evaluation.entries) != 3778:
        failures.append(
            f"approved unmapped backlog: expected 3778, found {len(evaluation.entries)}"
        )
    if failures:
        raise RuntimeError("v1.5 verification failed:\n- " + "\n- ".join(failures))


def _build_report(session, *, generated_at: str) -> dict[str, object]:
    evaluation = canonicalization.evaluate_backlog(session)
    approved = []
    for seed in DECISIONS:
        proposal = session.scalar(
            select(CanonicalIdentityProposal).where(
                CanonicalIdentityProposal.canonical_id == seed.canonical_id
            )
        )
        source_rows = session.execute(
            select(Setting, Manual, SettingMenuContext)
            .join(Manual, Manual.id == Setting.manual_id)
            .join(SettingMenuContext, SettingMenuContext.setting_id == Setting.id)
            .where(Setting.id.in_(seed.setting_ids))
            .order_by(Setting.id)
        )
        sources = []
        for setting, manual, context in source_rows:
            citations = session.scalars(
                select(SettingMenuContextCitation)
                .where(SettingMenuContextCitation.event_id == context.current_event_id)
                .order_by(SettingMenuContextCitation.page_number)
            )
            sources.append(
                {
                    "setting_id": setting.id,
                    "setting_name": effective_name(setting),
                    "description": effective_description(setting),
                    "manual_id": manual.id,
                    "manual_title": manual.document_title,
                    "publisher": manual.vendor,
                    "official_url": manual.source_url,
                    "setting_page": setting.edited_page_number or setting.page_number,
                    "reviewed_menu_path": context.menu_path,
                    "citations": [
                        {
                            "page_number": citation.page_number,
                            "evidence_excerpt": citation.evidence_excerpt,
                        }
                        for citation in citations
                    ],
                }
            )
        approved.append(
            {
                "canonical_id": seed.canonical_id,
                "canonical_name": proposal.canonical_name,
                "canonical_description": REVIEWED_DESCRIPTIONS[seed.canonical_id],
                "proposal_id": proposal.id,
                "confidence_basis_points": proposal.confidence_basis_points,
                "rationale": seed.rationale,
                "remaining_uncertainty": seed.uncertainty,
                "review_note": proposal.review_note,
                "source_settings": sources,
            }
        )
    return {
        "generated_at": generated_at,
        "release": "v1.5",
        "engine_version": canonicalization.ENGINE_VERSION,
        "database_revision": config.EXPECTED_DATABASE_REVISION,
        "verified_backup": {
            "path": str(BACKUP_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": EXPECTED_BACKUP_SHA256,
        },
        "summary": {
            "approved_unmapped_reviewed": 3809,
            "new_canonical_settings_created": len(DECISIONS),
            "new_mappings_created": len(CONTEXTS),
            "remaining_manual_review": len(evaluation.entries),
            "intentionally_unmapped_ambiguous_settings": len(AMBIGUOUS_SETTING_IDS),
        },
        "official_sources_used": list(OFFICIAL_SOURCES),
        "approved_canonical_identities": approved,
        "auto_approved_existing_identity_mappings": [],
        "ambiguous_or_conflicting": [
            {
                "canonical_id": AMBIGUOUS_CANONICAL_ID,
                "setting_ids": list(AMBIGUOUS_SETTING_IDS),
                "decision": "rejected_identity_proposal_left_unmapped",
                "reason": AMBIGUITY_REASON,
            }
        ],
        "remaining_manual_review": [asdict(entry) for entry in evaluation.entries],
    }


def _markdown_report(payload: dict[str, object]) -> str:
    summary = payload["summary"]
    lines = [
        "# TuneForge v1.5 Canonicalization Report",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "The full approved-but-unmapped backlog was evaluated by the conservative "
        "canonicalization engine. Fifteen identities were then approved only after "
        "cross-manual review of official source descriptions and cited BIOS menu paths.",
        "",
        "## Outcome",
        "",
        f"- Approved unmapped settings evaluated: {summary['approved_unmapped_reviewed']}",
        f"- New canonical identities: {summary['new_canonical_settings_created']}",
        f"- New reviewer-approved mappings: {summary['new_mappings_created']}",
        f"- Remaining unmapped settings: {summary['remaining_manual_review']}",
        "- Intentionally unresolved Secure Boot records: "
        f"{summary['intentionally_unmapped_ambiguous_settings']}",
        "",
        "## Approved identities",
        "",
    ]
    for decision in payload["approved_canonical_identities"]:
        lines.append(
            f"- **{decision['canonical_name']}** (`{decision['canonical_id']}`): "
            f"{len(decision['source_settings'])} mappings at "
            f"{decision['confidence_basis_points'] / 100:.2f}% confidence. "
            f"{decision['rationale']} Remaining uncertainty: "
            f"{decision['remaining_uncertainty']}"
        )
    lines.extend(
        [
            "",
            "## Intentionally unresolved",
            "",
            "`Clear Secure Boot Keys` remains unmapped. ASUS documents it under Key "
            "Management, while ASRock page 80 prints the label at two hierarchy levels. "
            "The proposal was rejected rather than guessing; the decision remains audited "
            "and reversible.",
            "",
            "## Detailed evidence",
            "",
            "The complete machine-readable report, including every approved source, menu "
            "citation, rationale, confidence assessment, and every remaining backlog record, "
            "is written to `data/reports/v1.5-canonicalization-final.json`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_reports(payload: dict[str, object]) -> None:
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MARKDOWN.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    REPORT_MARKDOWN.write_text(_markdown_report(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the reviewed v1.5 batch")
    args = parser.parse_args()

    _verify_manifest()
    _verify_backup()
    with SessionLocal() as session:
        evaluation = _verify_baseline(session)
        print(
            f"Preflight passed: {len(evaluation.entries)} approved unmapped settings, "
            f"{len(evaluation.proposed_groups)} reviewed candidate groups."
        )
        print(
            "Planned outcome: 15 identities, 31 mappings, 4 intentionally unmapped "
            "Secure Boot records."
        )
        if not args.apply:
            print("Dry run only; pass --apply to commit the pinned reviewed batch.")
            return 0

        baseline = _snapshot_counts(session)
        try:
            _record_contexts(session)
            reviewer_report = canonicalization.run_reviewer(session, commit=False)
            if reviewer_report.auto_approved_mappings:
                raise RuntimeError("The batch unexpectedly auto-approved an existing identity.")
            proposals = {
                proposal.canonical_id: proposal
                for proposal in session.scalars(select(CanonicalIdentityProposal))
            }
            if not set(_expected_groups()).issubset(proposals):
                raise RuntimeError("The reviewer did not persist every pinned identity proposal.")
            group_by_id = {
                group.canonical_id: group
                for group in canonicalization.evaluate_backlog(session).proposed_groups
            }
            for seed in DECISIONS:
                group = group_by_id.get(seed.canonical_id)
                if group is None or group.setting_ids != seed.setting_ids:
                    raise RuntimeError(f"Proposal evidence changed for {seed.canonical_id}.")
                if not group.automatic_eligible:
                    raise RuntimeError(f"Proposal is not approval-ready: {seed.canonical_id}.")
                note = _approval_note(session, seed, group.confidence_basis_points)
                canonical_identity_review.approve_identity_proposal(
                    session,
                    proposals[seed.canonical_id].id,
                    note=note,
                    actor=ACTOR,
                    canonical_description=REVIEWED_DESCRIPTIONS[seed.canonical_id],
                    commit=False,
                )
            canonical_identity_review.reject_identity_proposal(
                session,
                proposals[AMBIGUOUS_CANONICAL_ID].id,
                reason=AMBIGUITY_REASON,
                actor=ACTOR,
                commit=False,
            )
            _verify_final(session, baseline)
            session.commit()
        except Exception:
            session.rollback()
            raise

        payload = _build_report(session, generated_at=utc_now_iso())
        _write_reports(payload)
        print(f"Applied: identities={len(DECISIONS)}, mappings={len(CONTEXTS)}.")
        print(f"Remaining approved unmapped: {payload['summary']['remaining_manual_review']}.")
        print(f"JSON report: {REPORT_JSON}")
        print(f"Summary report: {REPORT_MARKDOWN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
