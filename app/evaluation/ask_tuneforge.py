"""Deterministic evaluation of the public Ask TuneForge retrieval contract."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    EvidencePassage,
    EvidenceSource,
    GuidanceClaim,
    GuidanceClaimEvidence,
    GuidanceClaimSetting,
    GuidanceClaimSourceSetting,
    Manual,
    Setting,
)
from app.domain import (
    assistant_retrieval,
    guidance_read,
    guided_answers,
    interface_presentation,
    question_routing,
    refusal_gaps,
    system_context,
)
from app.domain.errors import GuidanceReadError
from app.domain.interface_presentation import PresentationEnvelope, PresentationFact
from app.domain.review import effective_description

DATASET_VERSION = "1.0"
REPORT_VERSION = "1.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvaluationSystemContext:
    motherboard_model_id: str
    cpu_model_id: str
    bios_version: str = ""


@dataclass(frozen=True)
class AskEvaluationCase:
    case_id: str
    category: str
    query: str
    expected_outcome: str
    expected_resolution_status: str
    expected_answer_states: tuple[str, ...]
    expected_canonical_id: str | None = None
    required_claim_types: tuple[str, ...] = ()
    minimum_statements: int = 0
    require_citations: bool = False
    must_refuse: bool = False
    expected_library_names: tuple[str, ...] = ()
    expected_suggestion_canonical_ids: tuple[str, ...] = ()
    minimum_vendor_equivalents: int = 0
    expected_hardware_statuses: tuple[str, ...] = ()
    expected_bios_statuses: tuple[str, ...] = ()
    system_context: EvaluationSystemContext | None = None


@dataclass(frozen=True)
class AskEvaluationDataset:
    dataset_version: str
    title: str
    cases: tuple[AskEvaluationCase, ...]


@dataclass(frozen=True)
class AskEvaluationResult:
    case_id: str
    category: str
    query: str
    route_kind: str
    outcome: str
    resolution_status: str
    answer_state: str
    canonical_id: str | None
    statement_count: int
    unsupported_statement_count: int
    citation_count: int
    library_names: tuple[str, ...]
    suggestion_canonical_ids: tuple[str, ...]
    checks: dict[str, bool | None]
    failures: tuple[str, ...]


@dataclass(frozen=True)
class AskEvaluationReport:
    report_version: str
    dataset_version: str
    dataset_title: str
    case_count: int
    status: str
    metrics: dict[str, dict[str, int | float | None]]
    results: tuple[AskEvaluationResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_dataset(path: Path) -> AskEvaluationDataset:
    """Load and strictly validate a checked-in Ask TuneForge dataset."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset_version") != DATASET_VERSION:
        version = payload.get("dataset_version")
        raise ValueError(f"Unsupported Ask TuneForge dataset version: {version}")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Ask TuneForge evaluation dataset must contain cases.")
    cases = tuple(_load_case(raw) for raw in raw_cases)
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Ask TuneForge evaluation case IDs must be unique.")
    return AskEvaluationDataset(
        dataset_version=payload["dataset_version"],
        title=str(payload.get("title") or "Ask TuneForge evaluation"),
        cases=cases,
    )


def evaluate_dataset(session: Session, dataset: AskEvaluationDataset) -> AskEvaluationReport:
    """Evaluate without committing refusal events or changing reviewed knowledge."""

    results = tuple(_evaluate_case(session, case) for case in dataset.cases)
    metrics = _metrics(results)
    return AskEvaluationReport(
        report_version=REPORT_VERSION,
        dataset_version=dataset.dataset_version,
        dataset_title=dataset.title,
        case_count=len(results),
        status="pass" if all(not result.failures for result in results) else "fail",
        metrics=metrics,
        results=results,
    )


def _load_case(raw: object) -> AskEvaluationCase:
    if not isinstance(raw, dict):
        raise ValueError("Each evaluation case must be an object.")
    context_raw = raw.get("system_context")
    context = None
    if context_raw is not None:
        if not isinstance(context_raw, dict):
            raise ValueError("system_context must be an object.")
        context = EvaluationSystemContext(
            motherboard_model_id=str(context_raw["motherboard_model_id"]),
            cpu_model_id=str(context_raw["cpu_model_id"]),
            bios_version=str(context_raw.get("bios_version") or ""),
        )
    expected_outcome = str(raw["expected_outcome"])
    if expected_outcome not in {"supported", "refused"}:
        raise ValueError("expected_outcome must be supported or refused.")
    return AskEvaluationCase(
        case_id=str(raw["case_id"]),
        category=str(raw["category"]),
        query=str(raw["query"]),
        expected_outcome=expected_outcome,
        expected_resolution_status=str(raw["expected_resolution_status"]),
        expected_answer_states=tuple(raw["expected_answer_states"]),
        expected_canonical_id=raw.get("expected_canonical_id"),
        required_claim_types=tuple(raw.get("required_claim_types", ())),
        minimum_statements=int(raw.get("minimum_statements", 0)),
        require_citations=bool(raw.get("require_citations", False)),
        must_refuse=bool(raw.get("must_refuse", False)),
        expected_library_names=tuple(raw.get("expected_library_names", ())),
        expected_suggestion_canonical_ids=tuple(
            raw.get("expected_suggestion_canonical_ids", ())
        ),
        minimum_vendor_equivalents=int(raw.get("minimum_vendor_equivalents", 0)),
        expected_hardware_statuses=tuple(raw.get("expected_hardware_statuses", ())),
        expected_bios_statuses=tuple(raw.get("expected_bios_statuses", ())),
        system_context=context,
    )


def _evaluate_case(session: Session, case: AskEvaluationCase) -> AskEvaluationResult:
    route = question_routing.route(case.query)
    context = (
        system_context.resolve(
            session,
            motherboard_model_id=case.system_context.motherboard_model_id,
            cpu_model_id=case.system_context.cpu_model_id,
            bios_version=case.system_context.bios_version,
        )
        if case.system_context
        else None
    )
    if route.kind == question_routing.KIND_GUIDED_QUESTION:
        presentation = guided_answers.build_answer(
            session, route.question_id or "", system_context=context
        ).presentation
    else:
        presentation = _read_only_lookup(
            session,
            route.routed_value,
            original_query=route.original_query,
            context=context,
        )
    library = (
        assistant_retrieval.find_approved_matches(
            session,
            route.original_query,
            setting_hint=(
                route.routed_value
                if route.kind == question_routing.KIND_SETTING_LOOKUP
                else ""
            ),
        )
        if presentation.outcome != "supported"
        else assistant_retrieval.ApprovedLibraryMatches((), ())
    )
    library_names = tuple(result.effective_name for result in library.results)
    suggestion_ids = tuple(item.canonical_id for item in presentation.suggestions)
    grounded = tuple(
        _statement_is_grounded(session, presentation, item)
        for item in presentation.statements
    )
    checks: dict[str, bool | None] = {
        "retrieval_accuracy": (
            presentation.outcome == case.expected_outcome
            and presentation.resolution_status == case.expected_resolution_status
            and presentation.answer_state in case.expected_answer_states
            and _contains_all(library_names, case.expected_library_names)
            and set(case.expected_suggestion_canonical_ids).issubset(suggestion_ids)
        ),
        "canonical_resolution_accuracy": (
            presentation.canonical_id == case.expected_canonical_id
            if case.expected_canonical_id is not None
            else None
        ),
        "citation_accuracy": (
            all(grounded) and all(item.citations for item in presentation.statements)
            if presentation.statements or case.require_citations
            else None
        ),
        "answer_completeness": (
            len(presentation.statements) >= case.minimum_statements
            and set(case.required_claim_types).issubset(
                item.claim_type for item in presentation.statements
            )
            if case.expected_outcome == "supported"
            else None
        ),
        "safe_refusal_accuracy": (
            presentation.outcome == "refused" and not presentation.statements
            if case.must_refuse
            else None
        ),
        "hardware_scope_correctness": (
            set(item.hardware_match_status for item in presentation.statements).issubset(
                case.expected_hardware_statuses
            )
            and set(item.bios_match_status for item in presentation.statements).issubset(
                case.expected_bios_statuses
            )
            and all(
                item.hardware_match_status != "not_applicable"
                and item.bios_match_status != "not_applicable"
                for item in presentation.statements
            )
            if case.system_context is not None
            else None
        ),
        "vendor_equivalence_accuracy": (
            len({item.vendor_id for item in presentation.equivalent_vendor_names})
            >= case.minimum_vendor_equivalents
            if case.minimum_vendor_equivalents
            else None
        ),
    }
    failures = tuple(name for name, passed in checks.items() if passed is False)
    return AskEvaluationResult(
        case_id=case.case_id,
        category=case.category,
        query=case.query,
        route_kind=route.kind,
        outcome=presentation.outcome,
        resolution_status=presentation.resolution_status,
        answer_state=presentation.answer_state,
        canonical_id=presentation.canonical_id,
        statement_count=len(presentation.statements),
        unsupported_statement_count=sum(item is False for item in grounded),
        citation_count=sum(len(item.citations) for item in presentation.statements),
        library_names=library_names,
        suggestion_canonical_ids=suggestion_ids,
        checks=checks,
        failures=failures,
    )


def _read_only_lookup(
    session: Session,
    setting: str,
    *,
    original_query: str,
    context: system_context.SystemContext | None,
) -> PresentationEnvelope:
    """Mirror public retrieval while intentionally omitting append-only demand logging."""

    try:
        explanation = guidance_read.explain_setting(session, setting, system_context=context)
    except GuidanceReadError as error:
        if not error.message.startswith("No exact"):
            raise
        matches = tuple(refusal_gaps.nearest_canonical_matches(session, setting))
        return interface_presentation.build_unresolved_envelope(
            query=original_query,
            refusal_message=error.message,
            suggestions=matches,
        )
    return interface_presentation.build_resolved_envelope(explanation)


def _statement_is_grounded(
    session: Session,
    presentation: PresentationEnvelope,
    statement: PresentationFact,
) -> bool:
    claim = session.get(GuidanceClaim, statement.claim_id)
    if (
        claim is None
        or claim.status != "approved"
        or claim.claim_text != statement.text
        or claim.claim_type != statement.claim_type
        or presentation.canonical_id is None
        or session.get(
            GuidanceClaimSetting,
            {"claim_id": claim.id, "canonical_id": presentation.canonical_id},
        )
        is None
        or not statement.citations
    ):
        return False
    return all(
        _citation_is_grounded(session, claim.id, citation)
        for citation in statement.citations
    )


def _citation_is_grounded(session: Session, claim_id: int, citation) -> bool:
    if not _SHA256.fullmatch(citation.text_sha256) or citation.page_number < 1:
        return False
    if citation.source_kind == "evidence_passage":
        passage = session.get(EvidencePassage, citation.passage_id)
        source = session.get(EvidenceSource, citation.source_id)
        relation = session.get(
            GuidanceClaimEvidence,
            {"claim_id": claim_id, "passage_id": citation.passage_id},
        )
        return bool(
            passage
            and source
            and relation
            and source.status == "approved"
            and passage.source_id == source.id
            and passage.page_number == citation.page_number
            and passage.text_sha256 == citation.text_sha256
            and source.source_url == citation.source_url
        )
    if citation.source_kind == "approved_source_setting":
        setting = session.get(Setting, citation.setting_id)
        manual = session.get(Manual, citation.manual_id)
        relation = session.get(
            GuidanceClaimSourceSetting,
            {"claim_id": claim_id, "setting_id": citation.setting_id},
        )
        return bool(
            setting
            and manual
            and relation
            and setting.status == "approved"
            and setting.manual_id == manual.id == citation.source_id
            and (setting.edited_page_number or setting.page_number) == citation.page_number
            and hashlib.sha256(effective_description(setting).encode("utf-8")).hexdigest()
            == citation.text_sha256
            and (manual.source_url or "") == citation.source_url
        )
    return False


def _contains_all(actual: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    haystack = tuple(value.casefold() for value in actual)
    return all(any(term.casefold() in value for value in haystack) for term in expected)


def _metrics(results: tuple[AskEvaluationResult, ...]) -> dict[str, dict[str, int | float | None]]:
    metrics = {
        name: _ratio(result.checks[name] for result in results)
        for name in (
            "retrieval_accuracy",
            "canonical_resolution_accuracy",
            "citation_accuracy",
            "answer_completeness",
            "safe_refusal_accuracy",
            "hardware_scope_correctness",
            "vendor_equivalence_accuracy",
        )
    }
    statement_total = sum(result.statement_count for result in results)
    unsupported = sum(result.unsupported_statement_count for result in results)
    metrics["unsupported_statement_rate"] = {
        "passed": statement_total - unsupported,
        "evaluated": statement_total,
        "failed": unsupported,
        "percent": round((unsupported / statement_total) * 100, 2) if statement_total else 0.0,
    }
    return metrics


def _ratio(values) -> dict[str, int | float | None]:
    evaluated = [value for value in values if value is not None]
    passed = sum(value is True for value in evaluated)
    return {
        "passed": passed,
        "evaluated": len(evaluated),
        "failed": len(evaluated) - passed,
        "percent": round((passed / len(evaluated)) * 100, 2) if evaluated else None,
    }
