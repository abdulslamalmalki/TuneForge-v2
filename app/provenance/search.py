"""Official support-page discovery through a constrained web search."""

import html
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from html.parser import HTMLParser

from app import config
from app.provenance.metadata import MetadataSuggestion
from app.provenance.official_sources import (
    is_official_support_page,
    normalize_support_url,
    vendor_policy,
)

SEARCH_VERSION = "1.0"
_TOKEN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class RawSearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class SupportPageCandidate:
    title: str
    url: str
    snippet: str
    confidence: int
    evidence: str

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


class SearchUnavailableError(Exception):
    pass


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[RawSearchResult] = []
        self._current_url: str | None = None
        self._current_title: list[str] = []
        self._current_snippet: list[str] = []
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and classes.intersection({"result__a", "result-link"}):
            self._flush()
            self._current_url = _unwrap_result_url(values.get("href") or "")
            self._in_title = True
        elif tag in {"a", "div", "td"} and classes.intersection(
            {"result__snippet", "result-snippet"}
        ):
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        if tag in {"a", "div", "td"} and self._in_snippet:
            self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._current_title.append(data)
        elif self._in_snippet:
            self._current_snippet.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._current_url:
            self.results.append(
                RawSearchResult(
                    title=_clean_text(" ".join(self._current_title)),
                    url=self._current_url,
                    snippet=_clean_text(" ".join(self._current_snippet)),
                )
            )
        self._current_url = None
        self._current_title = []
        self._current_snippet = []
        self._in_title = False
        self._in_snippet = False


def discover_support_pages(
    metadata: MetadataSuggestion,
    *,
    fetch: Callable[[str], str] | None = None,
) -> tuple[str, list[SupportPageCandidate]]:
    """Search, discard non-official URLs, and rank up to the configured limit."""
    policy = vendor_policy(metadata.vendor)
    if policy is None:
        return "", []
    query = build_query(metadata)
    search_url = f"{config.PROVENANCE_SEARCH_ENDPOINT}?{urllib.parse.urlencode({'q': query})}"
    results = search_raw_results(search_url, fetch=fetch)
    candidates: dict[str, SupportPageCandidate] = {}
    for result in results:
        if not is_official_support_page(metadata.vendor, result.url):
            continue
        normalized_url = normalize_support_url(result.url)
        candidate = _score_result(metadata, result, normalized_url)
        previous = candidates.get(normalized_url)
        if previous is None or candidate.confidence > previous.confidence:
            candidates[normalized_url] = candidate
    ranked = sorted(candidates.values(), key=lambda item: (-item.confidence, item.url))
    return query, ranked[: config.PROVENANCE_MAX_CANDIDATES]


def search_raw_results(
    search_url: str,
    *,
    fetch: Callable[[str], str] | None = None,
) -> tuple[RawSearchResult, ...]:
    """Return parsed search results without assigning trust to any result URL."""

    try:
        body = (fetch or _fetch_search_html)(search_url)
    except Exception as error:
        raise SearchUnavailableError("The support-page search service was unavailable.") from error
    parser = _DuckDuckGoParser()
    parser.feed(body)
    parser.close()
    if not parser.results and 'data-testid="anomaly-modal"' in body:
        raise SearchUnavailableError("The search service requested an interactive challenge.")
    return tuple(parser.results)


def build_query(metadata: MetadataSuggestion) -> str:
    policy = vendor_policy(metadata.vendor)
    if policy is None:
        return ""
    identity = metadata.model_name or metadata.document_title
    chipset = f" {metadata.chipset}" if metadata.chipset else ""
    return f'site:{policy.domains[0]} "{identity}"{chipset} support manual'


def _fetch_search_html(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TuneForge-Provenance-Discovery/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=config.PROVENANCE_SEARCH_TIMEOUT_SECONDS) as reply:
        content_type = reply.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise SearchUnavailableError("The search service returned an unexpected response.")
        body = reply.read(config.PROVENANCE_MAX_SEARCH_BYTES + 1)
    if len(body) > config.PROVENANCE_MAX_SEARCH_BYTES:
        raise SearchUnavailableError("The search response exceeded the safe size limit.")
    return body.decode("utf-8", errors="replace")


def _score_result(
    metadata: MetadataSuggestion, result: RawSearchResult, normalized_url: str
) -> SupportPageCandidate:
    searchable = f"{result.title} {result.snippet} {normalized_url}".casefold()
    model_tokens = _meaningful_tokens(metadata.model_name)
    title_tokens = _meaningful_tokens(metadata.document_title)
    matched_model = sum(token in searchable for token in model_tokens)
    matched_title = sum(token in searchable for token in title_tokens)
    score = 45 + min(35, matched_model * 8) + min(15, matched_title * 2)
    if metadata.chipset and metadata.chipset.casefold() in searchable:
        score += 5
    score = min(score, 99)
    evidence_parts = ["official vendor domain", "recognized support-page path"]
    if matched_model:
        evidence_parts.append(f"{matched_model} model token(s) matched")
    if matched_title:
        evidence_parts.append(f"{matched_title} document token(s) matched")
    return SupportPageCandidate(
        title=result.title or normalized_url,
        url=normalized_url,
        snippet=result.snippet,
        confidence=score,
        evidence="; ".join(evidence_parts),
    )


def _meaningful_tokens(value: str) -> list[str]:
    ignored = {"bios", "manual", "guide", "series", "setup", "motherboard", "english"}
    return [
        token.casefold()
        for token in _TOKEN.findall(value)
        if len(token) >= 3 and token.casefold() not in ignored
    ]


def _unwrap_result_url(value: str) -> str:
    value = html.unescape(value)
    if value.startswith("//"):
        value = f"https:{value}"
    parsed = urllib.parse.urlsplit(value)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query:
        return query["uddg"][0]
    return value


def _clean_text(value: str) -> str:
    return " ".join(html.unescape(value).split())
