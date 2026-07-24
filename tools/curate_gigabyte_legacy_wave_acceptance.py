"""Apply the reviewed GIGABYTE legacy-platform acceptance manifests.

Dry-run is the default. Pass ``--apply`` only against manuals 9 and 10 after
verifying the pinned pre-ingestion backup. Decisions use the normal review and
acceptance services so edits, decisions, page checks, missed-setting records,
and certification remain in the immutable audit history.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from app.db.models import ExtractionRun, Manual, Setting
from app.db.session import SessionLocal
from app.domain import acceptance, review

BACKUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "knowledge_builder.pre-gigabyte-legacy-wave.sqlite3.backup"
)
EXPECTED_RUN_PARSER = "1.3"


@dataclass(frozen=True)
class MissedSpec:
    page: int
    name: str
    description: str


@dataclass(frozen=True)
class Manifest:
    manual_id: int
    sha256: str
    drafts: int
    label: str
    read_only_ids: frozenset[int]
    fragment_ids: frozenset[int]
    additional_configurable_ids: frozenset[int] = frozenset()
    name_edits: dict[int, str] = field(default_factory=dict)
    missed: tuple[MissedSpec, ...] = ()


MANIFESTS = (
    Manifest(
        manual_id=9,
        sha256="14814b731f11f56522bea71ea506b4182de44e3ad729169b81a075fc84f8bb90",
        drafts=142,
        label="GIGABYTE AMD B550/A520",
        read_only_ids=frozenset(
            {
                6812,  # Mass Storage Devices
                6818,  # Chipset SATA Port
                6839,  # Case Open
                6840,  # split voltage-monitor label
                6841,  # split voltage-monitor label and display prose
                6849,  # Temperature
                6850,  # Fan Speed
                6851,  # Flow Rate
                6857,  # Access Level
            }
        ),
        fragment_ids=frozenset(
            {
                6779,  # first line of combined voltage-control label
                6780,  # second line of combined voltage-control label
            }
        ),
        additional_configurable_ids=frozenset({6781}),
        name_edits={
            6781: (
                "CPU Vcore / Dynamic Vcore (DVID) / VCORE SOC / Dynamic VCORE "
                "SOC (DVID) / CPU VDD18 / CPU VDDP / A_VDD18S5 / DRAM Voltage "
                "(CH A/B) / DDRVPP Voltage (CH A/B) / DRAM Termination (CH A/B)"
            ),
        },
    ),
    Manifest(
        manual_id=10,
        sha256="17b5d03f17f9280f024b9ac491c9043b1ed3faa0987ea25a3cbacbf8c631b907",
        drafts=186,
        label="GIGABYTE Intel 600",
        read_only_ids=frozenset(
            {
                6894,  # Temperature
                6895,  # Fan Speed
                6896,  # Flow Rate
                6917,  # Core Fused Max Core Ratio
                7002,  # Mass Storage Devices
                7033,  # Case Open
                7034,  # split voltage-monitor label
                7035,  # split voltage-monitor label and display prose
                7037,  # Access Level
            }
        ),
        fragment_ids=frozenset(
            {
                6965,  # first line of combined voltage-control label
                6966,  # second line of combined voltage-control label
                6967,  # third line of combined voltage-control label
            }
        ),
        additional_configurable_ids=frozenset(
            {
                6932,  # AVX Settings; screenshot shows an Auto value
                6938,  # CPU Cores Enabling Mode; screenshot shows Random Mode
                6950,  # Turbo Power Limits; source description specifies Auto
                6968,  # final line of reconstructed voltage-control label
            }
        ),
        name_edits={
            6932: "AVX Settings",
            6938: "CPU Cores Enabling Mode",
            6950: "Turbo Power Limits",
            6968: (
                "Vcore Voltage Mode / CPU Vcore / Dynamic Vcore (DVID) / BCLK "
                "Adaptive Voltage / CPU Graphics Voltage (VAXG) / L2Atom "
                "Override Mode / Internal L2Atom / Internal L2Atom Offset / "
                "Internal VCCSA / CPU VCCIN AUX / DRAM Vdd / VddQ / VCC1P05 / "
                "VDD2 CPU / V0P82 PCH / V1P8 CPU / VCC1V8P"
            ),
            6991: "Onboard LAN Controller",
        },
        missed=(
            MissedSpec(
                page=13,
                name="Power Limit TDP (Watts) / Power Limit Time",
                description=(
                    "Sets the CPU, platform, or memory Turbo-mode power limit and "
                    "the time at that limit. Auto uses the CPU specifications; "
                    "available when Turbo Power Limits is enabled."
                ),
            ),
        ),
    ),
)


def _clean_description(value: str, page_number: int) -> str:
    lines = [line.strip() for line in value.splitlines()]
    while lines and (
        not lines[-1] or lines[-1] == str(page_number) or lines[-1] == f"- {page_number} -"
    ):
        lines.pop()
    return "\n".join(lines).strip()


def _rejection_reason(manifest: Manifest, setting: Setting) -> str | None:
    if setting.id in manifest.read_only_ids:
        return "Read-only sensor, status, device-information, or access-level display."
    if setting.id in manifest.fragment_ids:
        return "Wrapped fragment of a combined voltage-control label."
    if setting.id in manifest.additional_configurable_ids:
        return None
    if setting.extracted_name.startswith("&"):
        return None
    return (
        "Document navigation, screenshot annotation, menu/submenu heading, "
        "explanatory text, utility launcher, or parser fragment."
    )


def _load_pinned(session, manifest: Manifest):
    manual = session.get(Manual, manifest.manual_id)
    if manual is None or manual.sha256 != manifest.sha256:
        raise RuntimeError(f"Manual {manifest.manual_id} is not the pinned {manifest.label} guide.")
    run = session.scalar(select(ExtractionRun).where(ExtractionRun.manual_id == manifest.manual_id))
    if (
        run is None
        or run.status != "succeeded"
        or run.parser_version != EXPECTED_RUN_PARSER
        or run.settings_found != manifest.drafts
    ):
        raise RuntimeError(f"The pinned {manifest.label} extraction run changed.")
    settings = list(
        session.scalars(
            select(Setting)
            .where(Setting.manual_id == manifest.manual_id)
            .order_by(Setting.page_number, Setting.id)
        )
    )
    drafts = [setting for setting in settings if setting.status == "draft"]
    if len(settings) != manifest.drafts or len(drafts) != manifest.drafts:
        raise RuntimeError(
            f"Expected {manifest.drafts} untouched {manifest.label} drafts; "
            f"found {len(settings)} settings and {len(drafts)} drafts."
        )
    referenced_ids = (
        manifest.read_only_ids
        | manifest.fragment_ids
        | manifest.additional_configurable_ids
        | set(manifest.name_edits)
    )
    if not referenced_ids.issubset({setting.id for setting in settings}):
        raise RuntimeError(f"The {manifest.label} manifest references unknown setting IDs.")
    return manual, settings


def _apply_manifest(session, manifest: Manifest, manual: Manual, settings: list[Setting]) -> None:
    for setting in settings:
        reason = _rejection_reason(manifest, setting)
        if reason:
            review.reject_setting(session, setting, reason)
            continue

        name = manifest.name_edits.get(
            setting.id,
            setting.extracted_name.removeprefix("&").strip(),
        )
        description = _clean_description(
            setting.extracted_description,
            setting.page_number,
        )
        if name != setting.extracted_name or description != setting.extracted_description:
            review.edit_draft_setting(
                session,
                setting,
                name=name,
                description=description,
            )
        review.approve_setting(
            session,
            setting,
            note=(
                f"Configurable or executable {manifest.label} BIOS item verified "
                "against the rendered source page."
            ),
        )
        acceptance.review_page_reference(
            session,
            manual,
            setting.id,
            page_number=setting.page_number,
        )

    for spec in manifest.missed:
        acceptance.add_missed_setting(
            session,
            manual,
            name=spec.name,
            page_number=spec.page,
            description=spec.description,
            note=(
                "Confirmed parser-1.3 omission during full rendered-page review; "
                "the slash-separated heading was not emitted as a candidate."
            ),
        )

    acceptance.certify_ground_truth(
        session,
        manual,
        notes=(
            f"All pages in the {manifest.label} BIOS guide were rendered and audited. "
            "Every parser-1.3 candidate was reviewed; document scaffolding, wrapped "
            "label fragments, submenu-only headings, utilities, and read-only fields "
            "were rejected. Long combined voltage labels were reconstructed from their "
            "source lines."
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit both pinned manifests")
    args = parser.parse_args()

    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified pre-ingestion backup is missing: {BACKUP_PATH}")

    with SessionLocal() as session:
        loaded = [(manifest, *_load_pinned(session, manifest)) for manifest in MANIFESTS]
        for manifest, _manual, settings in loaded:
            approved = [
                setting for setting in settings if _rejection_reason(manifest, setting) is None
            ]
            rejected = len(settings) - len(approved)
            approved_empty = [
                setting for setting in approved if not (setting.extracted_description or "").strip()
            ]
            if approved_empty:
                ids = ", ".join(str(setting.id) for setting in approved_empty)
                raise RuntimeError(f"Unreviewed empty-description approvals: {ids}")
            print(
                f"{manifest.label} manifest: {len(approved)} approve, {rejected} reject, "
                f"{len(manifest.name_edits)} name edits, {len(manifest.missed)} missed."
            )
        if not args.apply:
            print("Dry run only; pass --apply after verifying the production backup.")
            return

        for manifest, manual, settings in loaded:
            _apply_manifest(session, manifest, manual, settings)
            report = acceptance.build_report(session, manual)
            print(
                f"Applied manual={manual.id}: approved={report.approved_count}, "
                f"rejected={report.rejected_count}, missed={report.missed_count}, "
                f"precision={report.precision:.4f}, "
                f"recall={report.estimated_recall:.4f}, "
                f"page_accuracy={report.page_accuracy:.4f}, "
                f"certified={report.acceptance_complete}."
            )


if __name__ == "__main__":
    main()
