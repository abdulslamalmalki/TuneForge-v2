"""Approved-settings keyword search (FR-24, FR-25; plan section 12 item 6).

Query functions only — index maintenance lives in app.search.index. The
index holds nothing but approved settings' effective values, so matching
it satisfies FR-24 by construction; rows are joined back to settings and
manuals for the FR-25 display fields.

Hostile-query safety: every whitespace-separated term is stripped of
double quotes and requoted, turning FTS5 operators (OR, NEAR, column:
filters, *, unbalanced quotes) into literal keywords — a MATCH syntax
error is unreachable. Terms combine with FTS5's implicit AND.
"""

from dataclasses import dataclass

from markupsafe import Markup, escape
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models import CanonicalSetting
from app.domain import canonical_settings
from app.domain import system_context as system_context_domain
from app.domain.canonical_settings import VendorEquivalentName
from app.domain.system_context import ContextApplicabilityMatch, SystemContext
from app.normalization.canonical_settings import normalize_lookup_variants

# Sentinels FTS5 wraps around matches; swapped for <mark> only after the
# snippet text itself is HTML-escaped.
_MARK_OPEN = "\x02"
_MARK_CLOSE = "\x03"

_SEARCH_SQL = text(
    "SELECT settings.id AS setting_id, settings_fts.name AS name,"
    " snippet(settings_fts, 1, :open, :close, '…', 12) AS raw_snippet,"
    " manuals.vendor AS vendor, manuals.vendor_id AS vendor_id,"
    " manuals.model_name AS model_name,"
    " manuals.original_filename AS original_filename,"
    " manuals.id AS manual_id,"
    " manual_applicability.scope AS applicability_scope,"
    " (SELECT group_concat(COALESCE(target.platform_id, target.chipset_family_id,"
    " target.chipset_id, target.motherboard_model_id), ',')"
    " FROM manual_applicability_targets AS target"
    " WHERE target.manual_id = manuals.id) AS applicability_target_ids,"
    " canonical_settings.canonical_id AS canonical_id,"
    " canonical_settings.canonical_name AS canonical_name,"
    " canonical_setting_classifications.category_id AS category_id,"
    " canonical_setting_categories.canonical_name AS category_name,"
    " canonical_setting_classifications.risk_level AS risk_level,"
    " COALESCE(settings.edited_page_number, settings.page_number) AS page_number"
    " FROM settings_fts"
    " JOIN settings ON settings.id = settings_fts.rowid"
    " JOIN manuals ON manuals.id = settings.manual_id"
    " LEFT JOIN manual_applicability ON manual_applicability.manual_id = manuals.id"
    " LEFT JOIN setting_canonical_mappings"
    " ON setting_canonical_mappings.setting_id = settings.id"
    " LEFT JOIN canonical_settings"
    " ON canonical_settings.canonical_id = setting_canonical_mappings.canonical_id"
    " AND canonical_settings.status = 'active'"
    " LEFT JOIN canonical_setting_classifications"
    " ON canonical_setting_classifications.canonical_id = canonical_settings.canonical_id"
    " LEFT JOIN canonical_setting_categories"
    " ON canonical_setting_categories.category_id ="
    " canonical_setting_classifications.category_id"
    " WHERE settings_fts MATCH :match"
    " AND (:vendor_id = '' OR manuals.vendor_id = :vendor_id)"
    " AND (:canonical_id = '' OR canonical_settings.canonical_id = :canonical_id)"
    " AND (:category_id = '' OR canonical_setting_classifications.category_id = :category_id)"
    " AND (:risk_level = '' OR canonical_setting_classifications.risk_level = :risk_level)"
    " AND (:platform_id = '' OR EXISTS ("
    " SELECT 1 FROM manual_applicability_targets AS target"
    " LEFT JOIN chipset_families AS family"
    " ON family.family_id = target.chipset_family_id"
    " LEFT JOIN chipsets AS chipset ON chipset.chipset_id = target.chipset_id"
    " LEFT JOIN motherboard_models AS model"
    " ON model.model_id = target.motherboard_model_id"
    " WHERE target.manual_id = manuals.id AND ("
    " target.platform_id = :platform_id OR family.platform_id = :platform_id"
    " OR chipset.platform_id = :platform_id OR model.platform_id = :platform_id)))"
    " AND (:chipset_family_id = '' OR EXISTS ("
    " SELECT 1 FROM manual_applicability_targets AS target"
    " LEFT JOIN chipsets AS chipset ON chipset.chipset_id = target.chipset_id"
    " LEFT JOIN motherboard_models AS model"
    " ON model.model_id = target.motherboard_model_id"
    " LEFT JOIN chipsets AS model_chipset ON model_chipset.chipset_id = model.chipset_id"
    " WHERE target.manual_id = manuals.id AND ("
    " target.chipset_family_id = :chipset_family_id"
    " OR chipset.family_id = :chipset_family_id"
    " OR model_chipset.family_id = :chipset_family_id"
    " OR target.platform_id = (SELECT platform_id FROM chipset_families"
    " WHERE family_id = :chipset_family_id))))"
    " AND (:chipset_id = '' OR EXISTS ("
    " SELECT 1 FROM manual_applicability_targets AS target"
    " LEFT JOIN motherboard_models AS model"
    " ON model.model_id = target.motherboard_model_id"
    " WHERE target.manual_id = manuals.id AND ("
    " target.chipset_id = :chipset_id OR model.chipset_id = :chipset_id"
    " OR target.chipset_family_id = (SELECT family_id FROM chipsets"
    " WHERE chipset_id = :chipset_id)"
    " OR target.platform_id = (SELECT platform_id FROM chipsets"
    " WHERE chipset_id = :chipset_id))))"
    " AND (:model_id = '' OR EXISTS ("
    " SELECT 1 FROM manual_applicability_targets AS target"
    " WHERE target.manual_id = manuals.id AND ("
    " target.motherboard_model_id = :model_id"
    " OR target.chipset_id = (SELECT chipset_id FROM motherboard_models"
    " WHERE model_id = :model_id)"
    " OR target.chipset_family_id = (SELECT family_id FROM chipsets WHERE chipset_id ="
    " (SELECT chipset_id FROM motherboard_models WHERE model_id = :model_id))"
    " OR target.platform_id = (SELECT platform_id FROM motherboard_models"
    " WHERE model_id = :model_id))))"
    " ORDER BY rank"
)


@dataclass(frozen=True)
class SearchResult:
    """One search hit with every FR-25 display field resolved."""

    setting_id: int
    effective_name: str
    snippet: Markup  # HTML-escaped description snippet with <mark> highlights
    manual_label: str  # vendor/model when present, else the original filename
    vendor_id: str | None
    manual_id: int
    applicability_scope: str | None
    applicability_target_ids: tuple[str, ...]
    context_match: ContextApplicabilityMatch | None
    canonical_id: str | None
    canonical_name: str | None
    equivalent_vendor_names: tuple[VendorEquivalentName, ...]
    category_id: str | None
    category_name: str | None
    risk_level: str | None
    page_number: int


def search_approved(
    session: Session,
    query: str,
    vendor_id: str = "",
    *,
    platform_id: str = "",
    chipset_family_id: str = "",
    chipset_id: str = "",
    model_id: str = "",
    canonical_id: str = "",
    category_id: str = "",
    risk_level: str = "",
    system_context: SystemContext | None = None,
) -> list[SearchResult]:
    """Keyword-match approved settings; a blank or all-noise query is empty."""
    match = _sanitize(query)
    if not match:
        return []
    parameters = {
        "match": match,
        "vendor_id": (
            system_context.vendor_id if system_context is not None else vendor_id.strip().casefold()
        ),
        "platform_id": platform_id.strip().casefold(),
        "chipset_family_id": chipset_family_id.strip().casefold(),
        "chipset_id": chipset_id.strip().casefold(),
        "model_id": model_id.strip().casefold(),
        "canonical_id": canonical_id.strip().casefold(),
        "category_id": category_id.strip().casefold(),
        "risk_level": risk_level.strip().casefold(),
        "open": _MARK_OPEN,
        "close": _MARK_CLOSE,
    }
    resolved_id = _resolve_exact_canonical_id(session, query)
    requested_id = parameters["canonical_id"]
    if resolved_id and requested_id and requested_id != resolved_id:
        return []
    if resolved_id:
        # Bind an exact reviewed name to its canonical identity before FTS.
        # FTS intentionally ignores punctuation, so this prevents opposite
        # labels such as Override(+) and Override(-) from crossing identities
        # while preserving the normal one-hit behavior for a vendor label.
        parameters["canonical_id"] = resolved_id
    rows = session.execute(_SEARCH_SQL, parameters).all()
    if not rows and resolved_id and parameters["canonical_id"] == resolved_id:
        projected = canonical_settings.list_vendor_equivalences(session, (resolved_id,))[
            resolved_id
        ]
        alias_match = _mapped_names_match(projected)
        if alias_match:
            # A canonical name or reviewer alias may not occur in any source
            # record. In that case expand to the mapped vendor names, keeping
            # the same canonical filter and cited equivalence projection.
            parameters["match"] = alias_match
            rows = session.execute(_SEARCH_SQL, parameters).all()
    equivalences = canonical_settings.list_vendor_equivalences(
        session,
        {row.canonical_id for row in rows if row.canonical_id},
    )
    return [
        _result_from_row(row, equivalences.get(row.canonical_id, ()), system_context)
        for row in rows
    ]


def _sanitize(query: str) -> str:
    terms = (term.replace('"', "") for term in query.split())
    return " ".join(f'"{term}"' for term in terms if term)


def _resolve_exact_canonical_id(session: Session, query: str) -> str | None:
    value = " ".join(query.split())
    identity = session.get(CanonicalSetting, value)
    candidate_ids = (
        {identity.canonical_id}
        if identity is not None and identity.status == canonical_settings.IDENTITY_ACTIVE
        else set()
    )
    normalized = normalize_lookup_variants(value)
    if normalized:
        candidate_ids.update(
            identity.canonical_id
            for _, identity in canonical_settings.find_alias_identity_matches(session, value)
        )
        candidate_ids.update(
            match.canonical_id
            for match in canonical_settings.find_mapped_name_matches(session, value)
        )
    return candidate_ids.pop() if len(candidate_ids) == 1 else None


def _mapped_names_match(equivalents: tuple[VendorEquivalentName, ...]) -> str:
    names = {item.setting_name for item in equivalents}
    alternatives = []
    for name in sorted(names):
        match = _sanitize(name)
        if match:
            alternatives.append(f"({match})")
    return " OR ".join(alternatives)


def _result_from_row(
    row,
    equivalents: tuple[VendorEquivalentName, ...],
    context: SystemContext | None,
) -> SearchResult:
    target_ids = tuple(item for item in (row.applicability_target_ids or "").split(",") if item)
    return SearchResult(
        setting_id=row.setting_id,
        effective_name=row.name,
        snippet=_render_snippet(row.raw_snippet),
        manual_label=(f"{row.vendor or ''} {row.model_name or ''}").strip()
        or row.original_filename,
        vendor_id=row.vendor_id,
        manual_id=row.manual_id,
        applicability_scope=row.applicability_scope,
        applicability_target_ids=target_ids,
        context_match=(
            system_context_domain.classify_manual_targets(
                context,
                manual_vendor_id=row.vendor_id,
                applicability_scope=row.applicability_scope,
                target_ids=target_ids,
            )
            if context is not None
            else None
        ),
        canonical_id=row.canonical_id,
        canonical_name=row.canonical_name,
        equivalent_vendor_names=equivalents,
        category_id=row.category_id,
        category_name=row.category_name,
        risk_level=row.risk_level,
        page_number=row.page_number,
    )


def _render_snippet(raw: str) -> Markup:
    escaped = str(escape(raw))
    return Markup(escaped.replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>"))
