"""Draft Setting creation: persist parser candidates for review (FR-15).

The extracted name and description written here are immutable afterwards
(FR-16): nothing in this module updates them, and no other v0.1 code path
writes to them after insert. Reviewer edits belong in the separate
edited_name/edited_description fields (M4).
"""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.db.models import Setting
from app.domain.extraction_runs import utc_now_iso
from app.extraction.settings_parser import SettingCandidate

STATUS_DRAFT = "draft"


def create_draft_settings(
    session: Session,
    *,
    manual_id: int,
    extraction_run_id: int,
    candidates: Iterable[SettingCandidate],
) -> list[Setting]:
    """Add one Draft settings row per candidate to the session.

    Rows carry the extracted name, extracted description (may be empty,
    FR-14), source page number, and owning manual/run (FR-15). Does not
    commit: the caller owns the transaction so drafts, run counters, and
    the run's success status land atomically (plan section 10, step 6).
    """
    now = utc_now_iso()
    drafts = [
        Setting(
            manual_id=manual_id,
            extraction_run_id=extraction_run_id,
            extracted_name=candidate.extracted_name,
            extracted_description=candidate.extracted_description,
            page_number=candidate.page_number,
            status=STATUS_DRAFT,
            created_at=now,
            updated_at=now,
        )
        for candidate in candidates
    ]
    session.add_all(drafts)
    return drafts
