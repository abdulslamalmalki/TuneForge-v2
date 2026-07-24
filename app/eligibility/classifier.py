"""Deterministic document-type classifier for the extraction eligibility gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

CLASSIFIER_VERSION = "1.0"

TYPE_BIOS_GUIDE = "bios_guide"
TYPE_FULL_MANUAL = "full_manual"
TYPE_SOFTWARE_GUIDE = "software_guide"
TYPE_FEATURE_GUIDE = "feature_guide"
TYPE_UNKNOWN = "unknown"
DOCUMENT_TYPES = (
    TYPE_BIOS_GUIDE,
    TYPE_FULL_MANUAL,
    TYPE_SOFTWARE_GUIDE,
    TYPE_FEATURE_GUIDE,
    TYPE_UNKNOWN,
)


@dataclass(frozen=True)
class ClassificationResult:
    document_type: str
    confidence: int
    eligible_recommendation: bool
    reasons: tuple[str, ...]
    scores: dict[str, int]
    classifier_version: str = CLASSIFIER_VERSION


def classify_document(
    *,
    filename: str,
    document_title: str,
    page_count: int,
    sample_text: str,
) -> ClassificationResult:
    """Classify one manual from immutable upload facts and a bounded text sample."""
    label = _normalize(f"{filename} {document_title}")
    text = _normalize(sample_text)
    scores = {
        TYPE_BIOS_GUIDE: 0,
        TYPE_FULL_MANUAL: 0,
        TYPE_SOFTWARE_GUIDE: 0,
        TYPE_FEATURE_GUIDE: 0,
    }
    reasons: dict[str, list[str]] = {key: [] for key in scores}

    _score_pattern(
        label,
        r"\b(?:uefi\s+)?bios\s+(?:setup\s+)?(?:user\s+)?(?:manual|guide)\b|"
        r"\bbios\s+setup\b|\buefi\s+setup\b|"
        r"\b(?:manual|guide)\b.*\b(?:bios|uefi)\b|"
        r"\b(?:bios|uefi)\b.*\b(?:manual|guide)\b",
        scores,
        reasons,
        TYPE_BIOS_GUIDE,
        10,
        "The filename or title explicitly identifies a BIOS/UEFI settings guide.",
    )
    _score_pattern(
        label,
        r"\bbios\b|\buefi\b",
        scores,
        reasons,
        TYPE_BIOS_GUIDE,
        3,
        "The filename or title contains BIOS/UEFI terminology.",
    )
    _score_pattern(
        label,
        r"[a-z0-9]bios(?:\W|$)",
        scores,
        reasons,
        TYPE_BIOS_GUIDE,
        3,
        "The vendor filename ends a product/platform identifier with BIOS.",
    )
    _score_pattern(
        label,
        r"\bmotherboard\s+user\s+guide\b|\bmotherboard\s+manual\b|"
        r"\bcomplete\s+user\s+manual\b",
        scores,
        reasons,
        TYPE_FULL_MANUAL,
        10,
        "The filename or title identifies a full motherboard user manual.",
    )
    _score_pattern(
        label,
        r"\bsoftware\s+(?:user\s+)?guide\b|\butility\s+guide\b",
        scores,
        reasons,
        TYPE_SOFTWARE_GUIDE,
        8,
        "The filename or title identifies a software or utility guide.",
    )
    _score_pattern(
        label,
        r"\bfeature\s+guide\b|\boverclocking\s+guide\b|\binstallation\s+guide\b",
        scores,
        reasons,
        TYPE_FEATURE_GUIDE,
        7,
        "The filename or title identifies a focused feature guide.",
    )

    bios_content_signals = (
        r"\boc menu\b",
        r"\badvanced (?:bios |uefi )?settings\b",
        r"\bsecure boot\b",
        r"\bdram (?:frequency|voltage|timing)\b",
        r"\bconfiguration options?\s*:",
    )
    bios_hits = sum(bool(re.search(pattern, text)) for pattern in bios_content_signals)
    if bios_hits:
        scores[TYPE_BIOS_GUIDE] += min(8, bios_hits * 2)
        reasons[TYPE_BIOS_GUIDE].append(
            f"The sampled pages contain {bios_hits} BIOS-setting catalog signal(s)."
        )

    full_manual_signals = (
        r"\bpackage contents\b",
        r"\bsafety information\b",
        r"\binstalling the (?:cpu|motherboard|memory)\b",
        r"\brear i/?o panel\b",
        r"\bmotherboard layout\b",
        r"\bhardware setup\b",
    )
    full_hits = sum(bool(re.search(pattern, text)) for pattern in full_manual_signals)
    if full_hits:
        scores[TYPE_FULL_MANUAL] += min(8, full_hits * 2)
        reasons[TYPE_FULL_MANUAL].append(
            f"The sampled pages contain {full_hits} full hardware-manual signal(s)."
        )

    if page_count >= 200:
        scores[TYPE_FULL_MANUAL] += 3
        reasons[TYPE_FULL_MANUAL].append(
            f"The document has {page_count} pages, which is typical of a full user manual."
        )

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_type, best_score = ranked[0]
    second_score = ranked[1][1]
    if best_score < 4 or best_score == second_score:
        return ClassificationResult(
            document_type=TYPE_UNKNOWN,
            confidence=35 if best_score < 4 else 50,
            eligible_recommendation=False,
            reasons=(
                "The available filename, title, and sampled pages are not decisive; reviewer "
                "classification is required.",
            ),
            scores=scores,
        )

    margin = best_score - second_score
    confidence = min(99, 55 + best_score + margin * 3)
    return ClassificationResult(
        document_type=best_type,
        confidence=confidence,
        eligible_recommendation=best_type == TYPE_BIOS_GUIDE,
        reasons=tuple(reasons[best_type]),
        scores=scores,
    )


def read_bounded_sample(path: Path, *, max_pages: int = 12) -> str:
    """Read at most the first ``max_pages`` pages for fast preflight analysis."""
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages[:max_pages])


def _score_pattern(
    value: str,
    pattern: str,
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    document_type: str,
    points: int,
    reason: str,
) -> None:
    if re.search(pattern, value):
        scores[document_type] += points
        reasons[document_type].append(reason)


def _normalize(value: str) -> str:
    return re.sub(r"[_\-/]+", " ", value).casefold()
