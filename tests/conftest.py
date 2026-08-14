"""Shared test setup: isolated data directory and database (NFR-6)."""

import os
import tempfile

import pytest
from sqlalchemy import text

# Must run before any `app` module is imported so config picks up the override.
os.environ.setdefault(
    "KNOWLEDGE_BUILDER_DATA_DIR", tempfile.mkdtemp(prefix="knowledge-builder-test-")
)
os.environ.setdefault("TUNEFORGE_AUTH_MODE", "disabled")


def make_pdf_bytes(page_count: int = 1, texts: list[str] | None = None) -> bytes:
    """Build a minimal but structurally valid PDF.

    With `texts`, the PDF has one page per entry, each containing that text
    in a Helvetica content stream; otherwise `page_count` blank pages.
    Newlines inside an entry become separate text lines on that page.
    """
    if texts is not None:
        page_count = len(texts)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    font_number = 3 + page_count
    for index in range(page_count):
        if texts is None:
            objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
        else:
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_number} 0 R >> >> "
                f"/Contents {font_number + 1 + index} 0 R >>".encode()
            )
    if texts is not None:
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        for text in texts:
            parts = ["BT /F1 12 Tf 72 720 Td"]
            for index, line in enumerate(text.split("\n")):
                escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
                if index:
                    parts.append("0 -20 Td")
                parts.append(f"({escaped}) Tj")
            parts.append("ET")
            stream = " ".join(parts).encode()
            objects.append(
                b"<< /Length "
                + str(len(stream)).encode()
                + b" >>\nstream\n"
                + stream
                + b"\nendstream"
            )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_position = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture
def pdf_bytes_factory():
    return make_pdf_bytes


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    from app import config
    from app.db.models import (
        Base,
        CanonicalSettingCategory,
        Chipset,
        ChipsetFamily,
        CpuArchitecture,
        CpuFamily,
        CpuModel,
        CpuModelAlias,
        CpuVendor,
        HardwarePlatform,
        MotherboardFamily,
        MotherboardModel,
        Vendor,
        VendorAlias,
    )
    from app.db.session import engine
    from app.normalization.canonical_settings import CATEGORIES
    from app.normalization.cpu import CPU_ARCHITECTURES, CPU_FAMILIES, CPU_MODELS, CPU_VENDORS
    from app.normalization.hardware import (
        CHIPSET_FAMILIES,
        CHIPSETS,
        MOTHERBOARD_FAMILIES,
        MOTHERBOARD_MODELS,
        PLATFORMS,
        motherboard_family_id,
    )
    from app.normalization.vendors import VENDORS, normalized_key
    from app.search import index as search_index

    config.ensure_data_dirs()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)")
        )
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": config.EXPECTED_DATABASE_REVISION},
        )
    # The FTS5 virtual table is not ORM-mapped (it is a disposable index);
    # real databases get it from the initial migration.
    with engine.begin() as connection:
        search_index.ensure_settings_fts(connection)
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        for definition in CATEGORIES:
            session.merge(
                CanonicalSettingCategory(
                    category_id=definition.category_id,
                    canonical_name=definition.canonical_name,
                    description=definition.description,
                    ordinal=definition.ordinal,
                )
            )
        aliases_by_key = {}
        for definition in VENDORS:
            session.merge(
                Vendor(
                    vendor_id=definition.vendor_id,
                    canonical_name=definition.canonical_name,
                )
            )
            for alias in (
                definition.vendor_id,
                definition.canonical_name,
                *definition.aliases,
            ):
                aliases_by_key[normalized_key(alias)] = VendorAlias(
                    normalized_alias=normalized_key(alias),
                    vendor_id=definition.vendor_id,
                    display_alias=alias,
                )
        session.add_all(aliases_by_key.values())
        session.flush()
        for definition in CPU_VENDORS:
            session.merge(
                CpuVendor(
                    vendor_id=definition.vendor_id,
                    canonical_name=definition.canonical_name,
                )
            )
        for definition in MOTHERBOARD_FAMILIES:
            session.merge(
                MotherboardFamily(
                    family_id=definition.family_id,
                    vendor_id=definition.vendor_id,
                    canonical_name=definition.canonical_name,
                )
            )
        session.flush()
        for definition in PLATFORMS:
            session.merge(
                HardwarePlatform(
                    platform_id=definition.platform_id,
                    canonical_name=definition.canonical_name,
                    cpu_vendor=definition.cpu_vendor,
                    socket_name=definition.socket_name,
                )
            )
        session.flush()
        for definition in CPU_ARCHITECTURES:
            session.merge(
                CpuArchitecture(
                    architecture_id=definition.architecture_id,
                    vendor_name=definition.vendor_name,
                    canonical_name=definition.canonical_name,
                )
            )
        session.flush()
        for definition in CPU_FAMILIES:
            session.merge(
                CpuFamily(
                    family_id=definition.family_id,
                    architecture_id=definition.architecture_id,
                    platform_id=definition.platform_id,
                    canonical_name=definition.canonical_name,
                )
            )
        session.flush()
        for definition in CPU_MODELS:
            session.merge(
                CpuModel(
                    cpu_model_id=definition.cpu_model_id,
                    family_id=definition.family_id,
                    architecture_id=definition.architecture_id,
                    platform_id=definition.platform_id,
                    canonical_name=definition.canonical_name,
                    is_x3d=int(definition.is_x3d),
                )
            )
            for alias in dict.fromkeys((definition.canonical_name, *definition.aliases)):
                session.merge(
                    CpuModelAlias(
                        normalized_alias=normalized_key(alias),
                        cpu_model_id=definition.cpu_model_id,
                        display_alias=alias,
                    )
                )
        session.flush()
        for definition in CHIPSET_FAMILIES:
            session.merge(
                ChipsetFamily(
                    family_id=definition.family_id,
                    canonical_name=definition.canonical_name,
                    platform_id=definition.platform_id,
                )
            )
        session.flush()
        for definition in CHIPSETS:
            session.merge(
                Chipset(
                    chipset_id=definition.chipset_id,
                    canonical_name=definition.canonical_name,
                    family_id=definition.family_id,
                    platform_id=definition.platform_id,
                )
            )
        session.flush()
        for definition in MOTHERBOARD_MODELS:
            session.merge(
                MotherboardModel(
                    model_id=definition.model_id,
                    vendor_id=definition.vendor_id,
                    canonical_name=definition.canonical_name,
                    family_id=motherboard_family_id(definition),
                    chipset_id=definition.chipset_id,
                    platform_id=definition.platform_id,
                )
            )
        session.commit()
    yield


@pytest.fixture(autouse=True)
def _clean_state(_create_schema):
    yield
    from sqlalchemy import text

    from app import config
    from app.db.models import (
        AuthEvent,
        AuthSession,
        CanonicalBacklogAssessment,
        CanonicalBacklogAssessmentEvent,
        CanonicalClassificationEvent,
        CanonicalIdentityEvent,
        CanonicalIdentityProposal,
        CanonicalIdentityProposalEvent,
        CanonicalIdentityProposalSetting,
        CanonicalMappingSuggestion,
        CanonicalSetting,
        CanonicalSettingAlias,
        CanonicalSettingClassification,
        CanonicalSuggestionEvent,
        EvidenceExtractionRun,
        EvidencePassage,
        EvidenceSource,
        EvidenceSourceEvent,
        GuidanceCandidate,
        GuidanceCandidateEvent,
        GuidanceClaim,
        GuidanceClaimApplicabilityTarget,
        GuidanceClaimEvent,
        GuidanceClaimEvidence,
        GuidanceClaimSetting,
        GuidanceClaimSourceSetting,
        IngestionBatch,
        IngestionBatchItem,
        Manual,
        ReferralPointer,
        ReferralPointerEvent,
        ReferralPointerSetting,
        ResearchEvidenceIntake,
        ResearchEvidenceIntakeEvent,
        SettingMenuContext,
        SettingMenuContextCitation,
        SettingMenuContextEvent,
        TrustedWebResearchEvent,
        TrustedWebResearchResult,
        TrustedWebResearchRun,
        UnansweredLookup,
        UserAccount,
    )
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        session.query(AuthEvent).delete()
        session.query(AuthSession).delete()
        session.query(UserAccount).delete()
        session.query(ReferralPointerEvent).delete()
        session.query(ReferralPointerSetting).delete()
        session.query(ReferralPointer).delete()
        session.query(UnansweredLookup).delete()
        session.query(ResearchEvidenceIntakeEvent).delete()
        session.query(ResearchEvidenceIntake).delete()
        session.query(TrustedWebResearchEvent).delete()
        session.query(TrustedWebResearchResult).delete()
        session.query(TrustedWebResearchRun).delete()
        session.query(GuidanceCandidateEvent).delete()
        session.query(GuidanceCandidate).delete()
        session.query(GuidanceClaimEvent).delete()
        session.query(GuidanceClaimApplicabilityTarget).delete()
        session.query(GuidanceClaimEvidence).delete()
        session.query(GuidanceClaimSourceSetting).delete()
        session.query(GuidanceClaimSetting).delete()
        session.query(GuidanceClaim).delete()
        session.query(EvidencePassage).delete()
        session.query(EvidenceSourceEvent).delete()
        session.query(EvidenceExtractionRun).delete()
        session.query(EvidenceSource).delete()
        session.query(CanonicalSuggestionEvent).delete()
        session.query(CanonicalMappingSuggestion).delete()
        session.query(CanonicalBacklogAssessmentEvent).delete()
        session.query(CanonicalBacklogAssessment).delete()
        session.query(CanonicalIdentityProposalEvent).delete()
        session.query(CanonicalIdentityProposalSetting).delete()
        session.query(CanonicalIdentityProposal).delete()
        session.query(SettingMenuContextCitation).delete()
        session.query(SettingMenuContext).delete()
        session.query(SettingMenuContextEvent).delete()
        session.query(CanonicalClassificationEvent).delete()
        session.query(CanonicalSettingClassification).delete()
        session.query(CanonicalIdentityEvent).delete()
        session.query(IngestionBatchItem).delete()
        session.query(IngestionBatch).delete()
        session.query(Manual).delete()
        session.query(CanonicalSettingAlias).delete()
        session.query(CanonicalSetting).delete()
        session.execute(text("DELETE FROM settings_fts"))
        session.commit()
    for path in config.UPLOADS_DIR.glob("*"):
        path.unlink()
    for path in config.EVIDENCE_SOURCES_DIR.glob("*"):
        path.unlink()


@pytest.fixture
def db_session():
    from app.db.session import SessionLocal

    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app())
