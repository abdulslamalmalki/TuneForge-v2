"""Single place for paths and constants (Implementation Plan section 8)."""

import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

COMPONENT_ROOT = Path(__file__).resolve().parent.parent

# Single source for the version shown in OpenAPI and /api/v1/health.
APP_VERSION = "1.33.0"

# Authentication is required by default. Tests explicitly opt into the legacy
# local-only bypass so existing domain and HTTP regression tests stay focused
# on their original contracts. Never deploy with authentication disabled.
ENVIRONMENT = os.environ.get("TUNEFORGE_ENVIRONMENT", "development").strip().lower()
AUTH_MODE = os.environ.get("TUNEFORGE_AUTH_MODE", "required").strip().lower()
AUTH_COOKIE_NAME = "tuneforge_session"
AUTH_COOKIE_SECURE = os.environ.get("TUNEFORGE_AUTH_COOKIE_SECURE", "0") == "1"
AUTH_SESSION_HOURS = int(os.environ.get("TUNEFORGE_AUTH_SESSION_HOURS", "12"))
TRUSTED_HOSTS = tuple(
    host.strip()
    for host in os.environ.get("TUNEFORGE_TRUSTED_HOSTS", "127.0.0.1,localhost,testserver").split(
        ","
    )
    if host.strip()
)
RATE_LIMIT_ENABLED = os.environ.get(
    "TUNEFORGE_RATE_LIMIT_ENABLED", "1" if ENVIRONMENT == "production" else "0"
) == "1"
RATE_LIMIT_LOGIN = os.environ.get("TUNEFORGE_RATE_LIMIT_LOGIN", "10/300")
RATE_LIMIT_ASK = os.environ.get("TUNEFORGE_RATE_LIMIT_ASK", "30/60")
RATE_LIMIT_RESEARCH = os.environ.get("TUNEFORGE_RATE_LIMIT_RESEARCH", "10/60")
RATE_LIMIT_PUBLIC_UPLOAD = os.environ.get("TUNEFORGE_RATE_LIMIT_PUBLIC_UPLOAD", "5/3600")
RATE_LIMIT_CURATOR_UPLOAD = os.environ.get("TUNEFORGE_RATE_LIMIT_CURATOR_UPLOAD", "60/3600")
RATE_LIMIT_SEARCH = os.environ.get("TUNEFORGE_RATE_LIMIT_SEARCH", "120/60")
RATE_LIMIT_ADMIN_MUTATION = os.environ.get("TUNEFORGE_RATE_LIMIT_ADMIN_MUTATION", "120/60")
EXPECTED_DATABASE_REVISION = "0027"

# Overridable so tests can run against a temporary data directory (NFR-6).
DATA_DIR = Path(os.environ.get("KNOWLEDGE_BUILDER_DATA_DIR", str(COMPONENT_ROOT / "data")))
BACKUPS_DIR = Path(os.environ.get("TUNEFORGE_BACKUPS_DIR", str(DATA_DIR / "backups")))
UPLOADS_DIR = DATA_DIR / "uploads"
EVIDENCE_SOURCES_DIR = DATA_DIR / "evidence_sources"
DATABASE_PATH = DATA_DIR / "knowledge_builder.sqlite3"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # FR-3
MAX_PDF_PAGES = int(os.environ.get("TUNEFORGE_MAX_PDF_PAGES", "5000"))
MAX_BATCH_FILES = 50
MAX_LABEL_LENGTH = 100  # FR-1: vendor / model_name limit
MAX_DOCUMENT_TITLE_LENGTH = 300
MAX_SOURCE_URL_LENGTH = 2048
MAX_LANGUAGE_LENGTH = 35
MAX_MISSED_SETTING_NAME_LENGTH = 300
MAX_ACCEPTANCE_NOTE_LENGTH = 5000
MAX_CANONICAL_SETTING_NAME_LENGTH = 300
MAX_CANONICAL_SETTING_DESCRIPTION_LENGTH = 2000
MAX_CANONICAL_MAPPING_NOTE_LENGTH = 2000
MAX_CANONICAL_RISK_RATIONALE_LENGTH = 2000
MAX_EVIDENCE_REVIEW_NOTE_LENGTH = 5000
MAX_EVIDENCE_PASSAGE_CHARS = 1800
MAX_GUIDANCE_CLAIM_CHARS = 1000
MAX_GUIDANCE_REVIEW_NOTE_LENGTH = 5000
MAX_PUBLIC_QUESTION_LENGTH = 300
PROVENANCE_SEARCH_ENDPOINT = os.environ.get(
    "TUNEFORGE_PROVENANCE_SEARCH_ENDPOINT", "https://lite.duckduckgo.com/lite/"
)
PROVENANCE_SEARCH_TIMEOUT_SECONDS = float(
    os.environ.get("TUNEFORGE_PROVENANCE_SEARCH_TIMEOUT_SECONDS", "8")
)
PROVENANCE_MAX_SEARCH_BYTES = 1_000_000
PROVENANCE_MAX_CANDIDATES = 5
TRUSTED_WEB_SEARCH_ENDPOINT = os.environ.get(
    "TUNEFORGE_TRUSTED_WEB_SEARCH_ENDPOINT", PROVENANCE_SEARCH_ENDPOINT
)
TRUSTED_WEB_TIMEOUT_SECONDS = float(
    os.environ.get("TUNEFORGE_TRUSTED_WEB_TIMEOUT_SECONDS", "8")
)
TRUSTED_WEB_MAX_SEARCH_BYTES = 1_000_000
TRUSTED_WEB_MAX_PAGE_BYTES = 2_000_000
TRUSTED_WEB_MAX_RESULTS = 5

# Optional presentation-model connection. TuneForge remains fully functional when
# this is empty: reviewed retrieval and deterministic rendering are the product
# authority, and a configured model is only an untrusted presentation component.
LLM_PROVIDER = os.environ.get("TUNEFORGE_LLM_PROVIDER", "").strip().lower()
LLM_MODEL = os.environ.get("TUNEFORGE_LLM_MODEL", "").strip()
LLM_API_KEY = os.environ.get("TUNEFORGE_LLM_API_KEY", "").strip()
LLM_BASE_URL = os.environ.get("TUNEFORGE_LLM_BASE_URL", "").strip()
LLM_TIMEOUT_SECONDS = float(os.environ.get("TUNEFORGE_LLM_TIMEOUT_SECONDS", "20"))
LLM_MAX_RESPONSE_BYTES = int(
    os.environ.get("TUNEFORGE_LLM_MAX_RESPONSE_BYTES", str(256 * 1024))
)
LLM_PROVIDER_IDS = frozenset({"openai", "anthropic", "gemini", "openai_compatible"})
# FR-10 / plan section 10: cumulative extracted text below this means the PDF
# has no usable text layer (scanned/image-only) and the run fails.
MIN_EXTRACTABLE_TEXT_CHARS = 200

LOG_LEVEL = "INFO"


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_SOURCES_DIR.mkdir(parents=True, exist_ok=True)


def validate_runtime_config() -> None:
    """Fail startup before serving with an unsafe or malformed configuration."""
    if ENVIRONMENT not in {"development", "production", "test"}:
        raise RuntimeError("TUNEFORGE_ENVIRONMENT must be development, production, or test.")
    if AUTH_MODE not in {"required", "disabled"}:
        raise RuntimeError("TUNEFORGE_AUTH_MODE must be required or disabled.")
    if not 1 <= AUTH_SESSION_HOURS <= 168:
        raise RuntimeError("TUNEFORGE_AUTH_SESSION_HOURS must be between 1 and 168.")
    if not 1 <= MAX_PDF_PAGES <= 20_000:
        raise RuntimeError("TUNEFORGE_MAX_PDF_PAGES must be between 1 and 20000.")
    if not TRUSTED_HOSTS:
        raise RuntimeError("TUNEFORGE_TRUSTED_HOSTS must contain at least one hostname.")
    for name in (
        "RATE_LIMIT_LOGIN",
        "RATE_LIMIT_ASK",
        "RATE_LIMIT_RESEARCH",
        "RATE_LIMIT_PUBLIC_UPLOAD",
        "RATE_LIMIT_CURATOR_UPLOAD",
        "RATE_LIMIT_SEARCH",
        "RATE_LIMIT_ADMIN_MUTATION",
    ):
        parse_rate_limit_policy(globals()[name], f"TUNEFORGE_{name}")
    _validate_https_service_url("TUNEFORGE_PROVENANCE_SEARCH_ENDPOINT", PROVENANCE_SEARCH_ENDPOINT)
    _validate_https_service_url(
        "TUNEFORGE_TRUSTED_WEB_SEARCH_ENDPOINT", TRUSTED_WEB_SEARCH_ENDPOINT
    )
    if LLM_PROVIDER and LLM_PROVIDER not in LLM_PROVIDER_IDS:
        raise RuntimeError(
            "TUNEFORGE_LLM_PROVIDER must be openai, anthropic, gemini, "
            "openai_compatible, or empty."
        )
    if LLM_PROVIDER and not LLM_MODEL:
        raise RuntimeError("TUNEFORGE_LLM_MODEL is required when a model provider is selected.")
    if LLM_PROVIDER in {"openai", "anthropic", "gemini"} and not LLM_API_KEY:
        raise RuntimeError(
            "TUNEFORGE_LLM_API_KEY is required for the selected official model provider."
        )
    if LLM_PROVIDER != "openai_compatible" and LLM_BASE_URL:
        raise RuntimeError(
            "TUNEFORGE_LLM_BASE_URL is supported only for openai_compatible providers."
        )
    if LLM_PROVIDER == "openai_compatible" and not LLM_BASE_URL:
        raise RuntimeError(
            "TUNEFORGE_LLM_BASE_URL is required for an openai_compatible provider."
        )
    if LLM_BASE_URL:
        _validate_llm_base_url(LLM_BASE_URL)
    if not 1 <= LLM_TIMEOUT_SECONDS <= 120:
        raise RuntimeError("TUNEFORGE_LLM_TIMEOUT_SECONDS must be between 1 and 120.")
    if not 16 * 1024 <= LLM_MAX_RESPONSE_BYTES <= 2 * 1024 * 1024:
        raise RuntimeError(
            "TUNEFORGE_LLM_MAX_RESPONSE_BYTES must be between 16384 and 2097152."
        )
    if ENVIRONMENT == "production":
        if AUTH_MODE != "required":
            raise RuntimeError("Production requires TUNEFORGE_AUTH_MODE=required.")
        if not AUTH_COOKIE_SECURE:
            raise RuntimeError("Production requires TUNEFORGE_AUTH_COOKIE_SECURE=1.")
        if any(host == "*" for host in TRUSTED_HOSTS):
            raise RuntimeError("Production does not allow a wildcard trusted host.")
        if not RATE_LIMIT_ENABLED:
            raise RuntimeError("Production requires TUNEFORGE_RATE_LIMIT_ENABLED=1.")


def _validate_llm_base_url(base_url: str) -> None:
    value = urlsplit(base_url)
    if value.scheme not in {"http", "https"} or not value.hostname:
        raise RuntimeError("TUNEFORGE_LLM_BASE_URL must be an absolute HTTP(S) URL.")
    if value.username or value.password or value.query or value.fragment:
        raise RuntimeError(
            "TUNEFORGE_LLM_BASE_URL cannot contain credentials, query parameters, or fragments."
        )
    if value.scheme == "http" and value.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(
            "Plain HTTP model endpoints are permitted only on localhost or a loopback address."
        )


def _validate_https_service_url(name: str, value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise RuntimeError(f"{name} must be an absolute public HTTPS URL.") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise RuntimeError(f"{name} must be an absolute public HTTPS URL.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise RuntimeError(f"{name} must not target a private or special-use address.")


def parse_rate_limit_policy(value: str, name: str) -> tuple[int, int]:
    try:
        limit_text, window_text = value.split("/", 1)
        limit, window = int(limit_text), int(window_text)
    except (AttributeError, TypeError, ValueError):
        raise RuntimeError(f"{name} must use the format requests/window_seconds.") from None
    if not 1 <= limit <= 100_000 or not 1 <= window <= 86_400:
        raise RuntimeError(f"{name} values are outside the supported range.")
    return limit, window
