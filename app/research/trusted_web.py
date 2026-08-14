"""Opt-in, BIOS-scoped research over a strict official-domain allowlist.

Results are append-audited source snapshots. They are deliberately not evidence
sources, guidance claims, or reviewed TuneForge knowledge.
"""

import hashlib
import html
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser

from sqlalchemy.orm import Session

from app import config
from app.auth.context import current_actor
from app.db.models import (
    TrustedWebResearchEvent,
    TrustedWebResearchResult,
    TrustedWebResearchRun,
)
from app.domain.extraction_runs import utc_now_iso
from app.normalization.canonical_settings import normalize_alias
from app.observability import log_event, metrics
from app.provenance.search import SearchUnavailableError, search_raw_results

POLICY_VERSION = "official-bios-web-1.0"
STATUS_SUCCEEDED = "succeeded"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"

PUBLISHERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "amd": ("AMD", ("amd.com",)),
    "intel": ("Intel", ("intel.com",)),
    "asus": ("ASUS", ("asus.com",)),
    "msi": ("MSI", ("msi.com",)),
    "gigabyte": ("GIGABYTE", ("gigabyte.com",)),
    "asrock": ("ASRock", ("asrock.com",)),
    "ami": ("American Megatrends", ("ami.com",)),
    "uefi": ("UEFI Forum", ("uefi.org",)),
    "pcisig": ("PCI-SIG", ("pcisig.com",)),
    "jedec": ("JEDEC", ("jedec.org",)),
    "dmtf": ("DMTF", ("dmtf.org",)),
    "insyde": ("Insyde Software", ("insyde.com",)),
    "phoenix": ("Phoenix Technologies", ("phoenix.com",)),
}

_BIOS_SCOPE_TERMS = frozenset(
    {
        "acpi", "agesa", "bios", "boot", "chipset", "cpu", "csm", "dram", "expo",
        "fan", "firmware", "fclk", "iommu", "memory", "motherboard", "overclock",
        "pbo", "pcie", "raid", "rebar", "secure boot", "smt", "soc", "tpm", "uefi",
        "undervolt", "virtualization", "voltage", "xmp",
    }
)
_QUERY_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+.-]*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_STOP_WORDS = frozenset(
    {"a", "an", "and", "are", "best", "can", "does", "for", "how", "i", "in", "is",
     "it", "my", "of", "on", "or", "setting", "settings", "should", "the", "this",
     "to", "what", "which", "with"}
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "nav", "header", "footer"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


class TrustedWebResearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedOfficialPage:
    final_url: str
    title: str
    text: str
    content_sha256: str


@dataclass(frozen=True)
class TrustedWebSourceView:
    publisher_id: str
    publisher_name: str
    title: str
    source_url: str
    evidence_kind: str
    excerpt_text: str | None
    content_sha256: str | None
    retrieved_at: str


@dataclass(frozen=True)
class TrustedWebResearchView:
    run_id: int
    query: str
    status: str
    notice: str
    sources: tuple[TrustedWebSourceView, ...]


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in _SKIP_TAGS:
            self._hidden_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in _SKIP_TAGS and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        value = " ".join(html.unescape(data).split())
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        if not self._hidden_depth and not self._in_title:
            self.text_parts.append(value)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a prevalidated address while retaining TLS hostname checks."""

    def __init__(self, host: str, address: str) -> None:
        super().__init__(
            host,
            port=443,
            timeout=config.TRUSTED_WEB_TIMEOUT_SECONDS,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def research(
    session: Session,
    question: str,
    *,
    search_fetch: Callable[[str], str] | None = None,
    page_fetch: Callable[[str], FetchedOfficialPage] | None = None,
) -> TrustedWebResearchView:
    """Run one explicit official-web lookup and persist its complete safe snapshot."""

    query = " ".join(question.split())
    if not query or len(query) > config.MAX_PUBLIC_QUESTION_LENGTH:
        raise TrustedWebResearchError("Enter a question of 300 characters or fewer.")
    run = _start_run(session, query)
    if not _is_bios_scoped(query):
        return _finish(
            session,
            run,
            status=STATUS_REFUSED,
            reason="Live research is limited to BIOS, UEFI, firmware, and motherboard topics.",
            sources=(),
        )

    search_query = f"{query} official BIOS documentation"
    encoded_query = urllib.parse.urlencode({"q": search_query})
    search_url = f"{config.TRUSTED_WEB_SEARCH_ENDPOINT}?{encoded_query}"
    try:
        raw_results = search_raw_results(
            search_url,
            fetch=search_fetch or _fetch_pinned_search_html,
        )
    except SearchUnavailableError:
        return _finish(
            session,
            run,
            status=STATUS_FAILED,
            reason="The trusted-web search service is temporarily unavailable.",
            sources=(),
        )

    fetch_page = page_fetch or _fetch_official_page
    views: list[TrustedWebSourceView] = []
    seen_urls: set[str] = set()
    for result in raw_results:
        publisher = _publisher_for_url(result.url)
        if publisher is None:
            continue
        normalized_url = _normalize_url(result.url)
        if normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        publisher_id, publisher_name = publisher
        retrieved_at = utc_now_iso()
        title = _clean(result.title)[: config.MAX_DOCUMENT_TITLE_LENGTH] or normalized_url
        evidence_kind = "official_link_only"
        excerpt = None
        content_sha256 = None
        source_url = normalized_url
        try:
            page = fetch_page(normalized_url)
            final_publisher = _publisher_for_url(page.final_url)
            if final_publisher is None:
                raise TrustedWebResearchError("Official page redirected outside the allowlist.")
            publisher_id, publisher_name = final_publisher
            source_url = _normalize_url(page.final_url)
            title = _clean(page.title)[: config.MAX_DOCUMENT_TITLE_LENGTH] or title
            excerpt = _select_excerpt(page.text, query)
            content_sha256 = page.content_sha256
            if excerpt:
                evidence_kind = "official_page_excerpt"
        except Exception:
            # The already validated official link remains useful and is clearly
            # distinguished from content retrieved directly from that page.
            metrics.increment("trusted_web_page_fetch_failures_total")
            pass
        views.append(
            TrustedWebSourceView(
                publisher_id=publisher_id,
                publisher_name=publisher_name,
                title=title,
                source_url=source_url,
                evidence_kind=evidence_kind,
                excerpt_text=excerpt,
                content_sha256=content_sha256,
                retrieved_at=retrieved_at,
            )
        )
        if len(views) >= config.TRUSTED_WEB_MAX_RESULTS:
            break

    if not views:
        return _finish(
            session,
            run,
            status=STATUS_REFUSED,
            reason="No allowlisted official source matched this question.",
            sources=(),
        )
    return _finish(
        session,
        run,
        status=STATUS_SUCCEEDED,
        reason="",
        sources=tuple(views),
    )


def _start_run(session: Session, query: str) -> TrustedWebResearchRun:
    now = utc_now_iso()
    run = TrustedWebResearchRun(
        query_text=query,
        normalized_query=normalize_alias(query) or query.casefold(),
        status="running",
        policy_version=POLICY_VERSION,
        result_count=0,
        reason=None,
        created_at=now,
        finished_at=None,
    )
    session.add(run)
    session.flush()
    session.add(
        TrustedWebResearchEvent(
            run_id=run.id,
            action="started",
            actor=current_actor(),
            detail=json.dumps({"policy_version": POLICY_VERSION}, sort_keys=True),
            created_at=now,
        )
    )
    session.commit()
    session.refresh(run)
    return run


def _finish(
    session: Session,
    run: TrustedWebResearchRun,
    *,
    status: str,
    reason: str,
    sources: tuple[TrustedWebSourceView, ...],
) -> TrustedWebResearchView:
    now = utc_now_iso()
    for ordinal, source in enumerate(sources, start=1):
        session.add(
            TrustedWebResearchResult(
                run_id=run.id,
                ordinal=ordinal,
                publisher_id=source.publisher_id,
                publisher_name=source.publisher_name,
                title=source.title,
                source_url=source.source_url,
                evidence_kind=source.evidence_kind,
                excerpt_text=source.excerpt_text,
                content_sha256=source.content_sha256,
                retrieved_at=source.retrieved_at,
            )
        )
    run.status = status
    run.result_count = len(sources)
    run.reason = reason or None
    run.finished_at = now
    session.add(
        TrustedWebResearchEvent(
            run_id=run.id,
            action=status,
            actor=current_actor(),
            detail=json.dumps(
                {"reason": reason or None, "result_count": len(sources)}, sort_keys=True
            ),
            created_at=now,
        )
    )
    session.commit()
    metrics.increment("trusted_web_runs_total", status=status)
    log_event(
        logging.getLogger(__name__),
        "trusted_web_run_finished",
        level=logging.WARNING if status == STATUS_FAILED else logging.INFO,
        status=status,
        result_count=len(sources),
    )
    notice = reason or (
        "Live official-source research is not reviewer-approved TuneForge knowledge. "
        "Treat excerpts as research leads and verify the linked source before changing "
        "BIOS settings."
    )
    return TrustedWebResearchView(
        run_id=run.id,
        query=run.query_text,
        status=status,
        notice=notice,
        sources=sources,
    )


def _is_bios_scoped(query: str) -> bool:
    folded = query.casefold()
    return any(term in folded for term in _BIOS_SCOPE_TERMS)


def _publisher_for_url(value: str) -> tuple[str, str] | None:
    if any(ord(character) < 32 for character in value):
        return None
    value = value.strip()
    if len(value) > config.MAX_SOURCE_URL_LENGTH or any(character.isspace() for character in value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.casefold() != "https" or parsed.username or parsed.password:
        return None
    if port not in (None, 443):
        return None
    for publisher_id, (publisher_name, domains) in PUBLISHERS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return publisher_id, publisher_name
    return None


def is_allowlisted_official_url(value: str) -> bool:
    """Public policy check used by tests and future connector adapters."""

    return _publisher_for_url(value) is not None


def _validate_official_url(value: str) -> tuple[str, str]:
    publisher = _publisher_for_url(value)
    if publisher is None:
        raise TrustedWebResearchError("URL is not on an allowlisted official HTTPS domain.")
    return publisher


def _normalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    return urllib.parse.urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))


def _resolve_public_addresses(value: str) -> tuple[str, ...]:
    host = urllib.parse.urlsplit(value).hostname or ""
    try:
        addresses = sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(
                    host,
                    443,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            }
        )
    except OSError as error:
        raise TrustedWebResearchError("Official source hostname could not be resolved.") from error
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise TrustedWebResearchError("Official source did not resolve exclusively to public IPs.")
    return tuple(addresses)


def _fetch_official_page(url: str) -> FetchedOfficialPage:
    final_url = _normalize_url(url)
    body = b""
    for redirect_count in range(_MAX_REDIRECTS + 1):
        _validate_official_url(final_url)
        status, location, content_type, body = _pinned_get(final_url)
        if status in _REDIRECT_STATUSES:
            if redirect_count == _MAX_REDIRECTS:
                raise TrustedWebResearchError("Official source exceeded the redirect limit.")
            if not location:
                raise TrustedWebResearchError("Official source returned an invalid redirect.")
            final_url = _normalize_url(urllib.parse.urljoin(final_url, location))
            continue
        if not 200 <= status < 300:
            raise TrustedWebResearchError("Official source could not be retrieved.")
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise TrustedWebResearchError("Official source is not an HTML page.")
        break
    if len(body) > config.TRUSTED_WEB_MAX_PAGE_BYTES:
        raise TrustedWebResearchError("Official source exceeded the safe page-size limit.")
    parser = _VisibleTextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    parser.close()
    return FetchedOfficialPage(
        final_url=final_url,
        title=" ".join(parser.title_parts),
        text="\n".join(parser.text_parts),
        content_sha256=hashlib.sha256(body).hexdigest(),
    )


def _fetch_pinned_search_html(url: str) -> str:
    """Fetch only the configured search host through the pinned HTTPS transport."""

    configured_host = urllib.parse.urlsplit(config.TRUSTED_WEB_SEARCH_ENDPOINT).hostname
    current_url = url
    body = b""
    for redirect_count in range(_MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != configured_host
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise TrustedWebResearchError("Search service redirected outside its trusted host.")
        status, location, content_type, body = _pinned_get(current_url)
        if status in _REDIRECT_STATUSES:
            if redirect_count == _MAX_REDIRECTS:
                raise TrustedWebResearchError("Search service exceeded the redirect limit.")
            if not location:
                raise TrustedWebResearchError("Search service returned an invalid redirect.")
            current_url = urllib.parse.urljoin(current_url, location)
            continue
        if not 200 <= status < 300:
            raise TrustedWebResearchError("Search service could not be retrieved.")
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise TrustedWebResearchError("Search service did not return HTML.")
        break
    if len(body) > config.TRUSTED_WEB_MAX_SEARCH_BYTES:
        raise TrustedWebResearchError("Search response exceeded the safe size limit.")
    return body.decode("utf-8", errors="replace")


def _pinned_get(url: str) -> tuple[int, str | None, str, bytes]:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    last_error: Exception | None = None
    for address in _resolve_public_addresses(url):
        connection = _PinnedHTTPSConnection(host, address)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "User-Agent": "TuneForge-Trusted-Web-Research/1.0",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    parsed_length = int(content_length)
                    if not 0 <= parsed_length <= config.TRUSTED_WEB_MAX_PAGE_BYTES:
                        raise TrustedWebResearchError(
                            "Official source exceeded the safe page-size limit."
                        )
                except ValueError:
                    raise TrustedWebResearchError(
                        "Official source returned an invalid content length."
                    ) from None
            body = response.read(config.TRUSTED_WEB_MAX_PAGE_BYTES + 1)
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].lower()
            return response.status, response.getheader("Location"), content_type, body
        except TrustedWebResearchError:
            raise
        except (OSError, http.client.HTTPException) as error:
            last_error = error
        finally:
            connection.close()
    raise TrustedWebResearchError("Official source could not be retrieved.") from last_error


def _select_excerpt(text: str, query: str) -> str | None:
    tokens = {
        token.casefold()
        for token in _QUERY_TOKEN.findall(query)
        if len(token) >= 3 and token.casefold() not in _STOP_WORDS
    }
    sentences = [" ".join(item.split()) for item in _SENTENCE.split(text) if item.strip()]
    ranked = sorted(
        (
            (sum(token in sentence.casefold() for token in tokens), index, sentence)
            for index, sentence in enumerate(sentences)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    matched_indices = [index for score, index, _ in ranked if score > 0][:3]
    if not matched_indices:
        return None
    context_indices: set[int] = set()
    for index in matched_indices:
        context_indices.update(
            item for item in (index - 1, index, index + 1) if 0 <= item < len(sentences)
        )
    selected = [sentences[index] for index in sorted(context_indices)[:5]]
    return " ".join(selected)[:1000]


def _clean(value: str) -> str:
    return " ".join(html.unescape(value).split())
