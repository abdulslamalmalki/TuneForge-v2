"""Transparent, fail-closed routing from simple questions to reviewed identities."""

import re
from dataclasses import dataclass

from app import config
from app.domain.am5_question_acceptance import QUESTION_SET

KIND_GUIDED_QUESTION = "guided_question"
KIND_SETTING_LOOKUP = "setting_lookup"
ROUTER_VERSION = "1.0"

_WRAPPERS = (
    (
        "what_is",
        re.compile(
            r"^(?:what\s+is|what['’]s|explain|define|tell\s+me\s+about)\s+"
            r"(?P<setting>.+?)(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "what_does",
        re.compile(
            r"^what\s+does\s+(?P<setting>.+?)\s+(?:do|mean|represent)"
            r"(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "how_does",
        re.compile(
            r"^how\s+does\s+(?P<setting>.+?)\s+work"
            r"(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "where_is",
        re.compile(
            r"^where\s+is\s+(?P<setting>.+?)(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "is_safe",
        re.compile(
            r"^is\s+(?P<setting>.+?)\s+safe(?:\s+to\s+(?:enable|disable|change))?"
            r"(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "available_values",
        re.compile(
            r"^(?:what\s+are\s+)?(?:the\s+)?(?:available\s+)?(?:modes|values|options)"
            r"\s+(?:for|of)\s+(?P<setting>.+?)(?:\s+in\s+(?:the\s+)?bios)?[?.!]*$",
            re.IGNORECASE,
        ),
    ),
)
_TRAILING_QUESTION_PUNCTUATION = re.compile(r"[?.!]+$")
_SETTING_EDGE_PUNCTUATION = re.compile(r"^[\"'“”‘’?.!\s]+|[\"'“”‘’?.!\s]+$")


class QuestionRoutingError(ValueError):
    pass


@dataclass(frozen=True)
class RouteDecision:
    version: str
    kind: str
    original_query: str
    routed_value: str
    rule: str
    question_id: str | None = None

    @property
    def transformed(self) -> bool:
        return self.original_query != self.routed_value


def route(value: str) -> RouteDecision:
    query = " ".join(value.split())
    if not query:
        raise QuestionRoutingError("Enter a BIOS setting or question.")
    if len(query) > config.MAX_PUBLIC_QUESTION_LENGTH:
        raise QuestionRoutingError(
            f"Question must be {config.MAX_PUBLIC_QUESTION_LENGTH} characters or fewer."
        )

    normalized = _normalized_question(query)
    question = next(
        (item for item in QUESTION_SET if _normalized_question(item.question) == normalized),
        None,
    )
    if question is not None:
        return RouteDecision(
            version=ROUTER_VERSION,
            kind=KIND_GUIDED_QUESTION,
            original_query=query,
            routed_value=question.question,
            question_id=question.question_id,
            rule="exact_reviewed_question",
        )

    for rule, pattern in _WRAPPERS:
        match = pattern.fullmatch(query)
        if match is None:
            continue
        setting = _clean_setting(match.group("setting"))
        if setting:
            return RouteDecision(
                version=ROUTER_VERSION,
                kind=KIND_SETTING_LOOKUP,
                original_query=query,
                routed_value=setting,
                rule=rule,
            )

    return RouteDecision(
        version=ROUTER_VERSION,
        kind=KIND_SETTING_LOOKUP,
        original_query=query,
        routed_value=query,
        rule="exact_setting_input",
    )


def _normalized_question(value: str) -> str:
    return " ".join(_TRAILING_QUESTION_PUNCTUATION.sub("", value.casefold()).split())


def _clean_setting(value: str) -> str:
    return " ".join(_SETTING_EDGE_PUNCTUATION.sub("", value).split())
