"""Single place for paths and constants (Implementation Plan section 8)."""

import os
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parent.parent

# Single source for the version shown in OpenAPI and /api/v1/health.
APP_VERSION = "1.3.0"

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
EXPECTED_DATABASE_REVISION = "0018"

# Overridable so tests can run against a temporary data directory (NFR-6).
DATA_DIR = Path(os.environ.get("KNOWLEDGE_BUILDER_DATA_DIR", str(COMPONENT_ROOT / "data")))
BACKUPS_DIR = Path(os.environ.get("TUNEFORGE_BACKUPS_DIR", str(DATA_DIR / "backups")))
UPLOADS_DIR = DATA_DIR / "uploads"
EVIDENCE_SOURCES_DIR = DATA_DIR / "evidence_sources"
DATABASE_PATH = DATA_DIR / "knowledge_builder.sqlite3"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # FR-3
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
    "TUNEFORGE_PROVENANCE_SEARCH_ENDPOINT", "https://html.duckduckgo.com/html/"
)
PROVENANCE_SEARCH_TIMEOUT_SECONDS = float(
    os.environ.get("TUNEFORGE_PROVENANCE_SEARCH_TIMEOUT_SECONDS", "8")
)
PROVENANCE_MAX_SEARCH_BYTES = 1_000_000
PROVENANCE_MAX_CANDIDATES = 5
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
    if not TRUSTED_HOSTS:
        raise RuntimeError("TUNEFORGE_TRUSTED_HOSTS must contain at least one hostname.")
    if ENVIRONMENT == "production":
        if AUTH_MODE != "required":
            raise RuntimeError("Production requires TUNEFORGE_AUTH_MODE=required.")
        if not AUTH_COOKIE_SECURE:
            raise RuntimeError("Production requires TUNEFORGE_AUTH_COOKIE_SECURE=1.")
        if any(host == "*" for host in TRUSTED_HOSTS):
            raise RuntimeError("Production does not allow a wildcard trusted host.")
