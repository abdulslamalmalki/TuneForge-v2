"""Apply the pinned v1.5.1 official-manual trust corrections.

The command is intentionally data-specific. It verifies the immutable backup,
database revision, live counts, official manual provenance, and exact source
records before invoking TuneForge's normal reopen/edit/approve/reject APIs.
Run without ``--apply`` for a read-only preflight.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, text

from app import config
from app.db.models import Manual, Setting, SettingCanonicalMapping
from app.db.session import SessionLocal
from app.domain import acceptance, review
from app.operations.backup import inspect_backup
from tools.audit_public_bios_knowledge import build_report

BACKUP_PATH = config.BACKUPS_DIR / "tuneforge-v1.5.1-pre-trust-audit.zip"
BACKUP_SHA256 = "be7e60c26e73e5a5e70e3e94e79af8851ee7227d559668880be6a4c57e6707b2"
BASELINE_COUNTS = {
    "settings": 8719,
    "approved": 4021,
    "rejected": 4698,
    "draft": 0,
    "mappings": 243,
}
EXPECTED_FINAL_COUNTS = {
    "settings": 8719,
    "approved": 4018,
    "rejected": 4701,
    "draft": 0,
    "mappings": 243,
}


@dataclass(frozen=True)
class ManualPin:
    manual_id: int
    sha256: str
    source_url: str


MANUALS = (
    ManualPin(
        2,
        "ec2ca097b878859f5de664449f5ca8d0d51eb41509290588da0fc2dacf711315",
        "https://www.asrock.com/mb/AMD/X870E%20Taichi%20White/index.asp#Manual",
    ),
    ManualPin(
        3,
        "586c9e7fc4a78e22afc5234b3d5f13511b9e923131a76f4af963bd4a50293054",
        "https://www.asus.com/us/supportonly/prime%20b860m-ks/helpdesk_manual/",
    ),
    ManualPin(
        5,
        "c59d17516e594f187bcb8d1a60b9e746f0a1849435f76ae93e726040f731af8e",
        "https://www.msi.com/support/download-manual/MPG-Z790-EDGE-TI-MAX-WIFI",
    ),
)


@dataclass(frozen=True)
class DescriptionCorrection:
    setting_id: int
    manual_id: int
    page_number: int
    name: str
    description: str | None
    approval_note: str


DESCRIPTION_CORRECTIONS = (
    DescriptionCorrection(
        462,
        2,
        63,
        "Read Drift Adjustment",
        "Auto corresponds to Read Drift Adjustment 0.",
        "Official ASRock manual page 63 visually reviewed; the adjacent Auto value was "
        "restored as the setting description.",
    ),
    DescriptionCorrection(
        464,
        2,
        63,
        "Write Drift Adjustment",
        "Auto corresponds to Write Drift Adjustment 0.",
        "Official ASRock manual page 63 visually reviewed; the adjacent Auto value was "
        "restored as the setting description.",
    ),
    DescriptionCorrection(
        720,
        3,
        24,
        "DIMM Fit",
        None,
        "Official ASUS manual page 24 visually reviewed; the extracted description is "
        "the documented DIMM Fit behavior and the empty reviewer override was removed.",
    ),
)


@dataclass(frozen=True)
class RejectionCorrection:
    setting_id: int
    manual_id: int
    page_number: int
    name: str
    reason: str


REJECTIONS = (
    RejectionCorrection(
        823,
        3,
        31,
        "Duty Cycle Correction Downstream Training",
        "Official ASUS manual page 31 shows this as a group heading, not a configurable "
        "setting; the configurable PI Serializer/LUT child remains approved as setting 824.",
    ),
    RejectionCorrection(
        1077,
        3,
        57,
        "Processor Disable",
        "Official ASUS manual page 57 shows this as a section heading, not a configurable "
        "setting; the configurable E-core control remains approved as setting 1078.",
    ),
    RejectionCorrection(
        4901,
        5,
        50,
        "tXPDLL",
        "Official MSI manual page 50 repeats the same adjacent tXPDLL label and description; "
        "setting 4900 is retained as the single public source record.",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(session) -> dict[str, int]:
    return {
        "settings": int(session.scalar(select(func.count()).select_from(Setting)) or 0),
        "approved": int(
            session.scalar(
                select(func.count()).select_from(Setting).where(Setting.status == "approved")
            )
            or 0
        ),
        "rejected": int(
            session.scalar(
                select(func.count()).select_from(Setting).where(Setting.status == "rejected")
            )
            or 0
        ),
        "draft": int(
            session.scalar(
                select(func.count()).select_from(Setting).where(Setting.status == "draft")
            )
            or 0
        ),
        "mappings": int(
            session.scalar(select(func.count()).select_from(SettingCanonicalMapping)) or 0
        ),
    }


def _verify_backup() -> None:
    if not BACKUP_PATH.is_file():
        raise RuntimeError(f"Verified backup is missing: {BACKUP_PATH}")
    if _sha256(BACKUP_PATH) != BACKUP_SHA256:
        raise RuntimeError("Verified backup SHA-256 changed.")
    inspection = inspect_backup(BACKUP_PATH)
    if inspection.database_revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError("Verified backup has an unexpected database revision.")


def _verify_setting(session, spec) -> Setting:
    setting = session.get(Setting, spec.setting_id)
    if setting is None:
        raise RuntimeError(f"Pinned setting {spec.setting_id} is missing.")
    actual = (
        setting.manual_id,
        setting.page_number,
        setting.status,
        review.effective_name(setting),
        setting.page_review_status,
    )
    expected = (spec.manual_id, spec.page_number, "approved", spec.name, "correct")
    if actual != expected:
        raise RuntimeError(
            f"Pinned setting {spec.setting_id} changed: expected {expected!r}, found {actual!r}."
        )
    if session.get(SettingCanonicalMapping, setting.id) is not None:
        raise RuntimeError(f"Pinned setting {setting.id} unexpectedly has a canonical mapping.")
    return setting


def _preflight(session) -> None:
    _verify_backup()
    revision = session.scalar(text("SELECT version_num FROM alembic_version"))
    if revision != config.EXPECTED_DATABASE_REVISION:
        raise RuntimeError(
            f"Expected revision {config.EXPECTED_DATABASE_REVISION}, found {revision}."
        )
    if session.scalar(text("PRAGMA integrity_check")) != "ok":
        raise RuntimeError("SQLite integrity check failed.")
    if list(session.execute(text("PRAGMA foreign_key_check"))):
        raise RuntimeError("SQLite foreign-key check failed.")
    counts = _counts(session)
    if counts != BASELINE_COUNTS:
        raise RuntimeError(f"Live baseline changed: expected {BASELINE_COUNTS}, found {counts}.")
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        if manual is None:
            raise RuntimeError(f"Pinned manual {pin.manual_id} is missing.")
        if (manual.sha256, manual.source_url) != (pin.sha256, pin.source_url):
            raise RuntimeError(f"Pinned manual {pin.manual_id} provenance changed.")
        if manual.acceptance_reviewed_at is None:
            raise RuntimeError(f"Pinned manual {pin.manual_id} is not acceptance-certified.")
    for spec in (*DESCRIPTION_CORRECTIONS, *REJECTIONS):
        _verify_setting(session, spec)
    if review.effective_description(session.get(Setting, 462)).strip():
        raise RuntimeError("Setting 462 no longer has the expected empty description.")
    if review.effective_description(session.get(Setting, 464)).strip():
        raise RuntimeError("Setting 464 no longer has the expected empty description.")
    dimm_fit = session.get(Setting, 720)
    if dimm_fit.edited_description != "" or not dimm_fit.extracted_description.strip():
        raise RuntimeError("Setting 720 no longer has the pinned empty override state.")


def _apply(session) -> None:
    for spec in DESCRIPTION_CORRECTIONS:
        setting = _verify_setting(session, spec)
        review.reopen_setting(session, setting)
        description = (
            setting.extracted_description.strip()
            if spec.description is None
            else spec.description
        )
        review.edit_draft_setting(
            session,
            setting,
            name=review.effective_name(setting),
            description=description,
        )
        review.approve_setting(session, setting, note=spec.approval_note)
    for spec in REJECTIONS:
        setting = _verify_setting(session, spec)
        review.reopen_setting(session, setting)
        review.reject_setting(session, setting, spec.reason)
    for pin in MANUALS:
        manual = session.get(Manual, pin.manual_id)
        acceptance.certify_ground_truth(
            session,
            manual,
            notes=(
                "v1.5.1 trust audit completed against the cited official PDF pages; "
                "confirmed descriptions were restored and non-setting headings or exact "
                "duplicate documentation were rejected through the audited review workflow."
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply after the guarded preflight.")
    parser.add_argument(
        "--report",
        type=Path,
        default=config.DATA_DIR / "reports" / "v1.5.1-trust-corrections.json",
    )
    args = parser.parse_args()

    with SessionLocal() as session:
        _preflight(session)
        if not args.apply:
            print("Preflight passed. No changes made; rerun with --apply.")
            return 0
        _apply(session)
        final_counts = _counts(session)
        if final_counts != EXPECTED_FINAL_COUNTS:
            raise RuntimeError(
                f"Final counts differ: expected {EXPECTED_FINAL_COUNTS}, found {final_counts}."
            )
        trust_report = build_report(session)
        if trust_report["status"] != "pass":
            raise RuntimeError("Post-correction public knowledge audit failed.")
        payload = {
            "backup": {"path": str(BACKUP_PATH), "sha256": BACKUP_SHA256},
            "counts_before": BASELINE_COUNTS,
            "counts_after": final_counts,
            "description_corrections": [spec.setting_id for spec in DESCRIPTION_CORRECTIONS],
            "rejected_non_settings": [spec.setting_id for spec in REJECTIONS],
            "public_knowledge_audit": trust_report,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Correction report: {args.report.resolve()}")
        print(json.dumps(trust_report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
