"""Alembic compatibility for installations that already contain manuals."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parents[2]


def _alembic(data_dir: Path, revision: str) -> None:
    env = os.environ.copy()
    env["KNOWLEDGE_BUILDER_DATA_DIR"] = str(data_dir)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=COMPONENT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_asus_model_migration_repairs_an_existing_0014_catalog(tmp_path):
    data_dir = tmp_path / "asus-model-migration-data"
    _alembic(data_dir, "0014")
    database = data_dir / "knowledge_builder.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM motherboard_models WHERE model_id = 'asus-tuf-gaming-x870-plus-wifi'"
        )

    _alembic(data_dir, "head")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT vendor_id, chipset_id, platform_id FROM motherboard_models "
            "WHERE model_id = 'asus-tuf-gaming-x870-plus-wifi'"
        ).fetchone() == ("asus", "amd-x870", "amd-am5")


def test_upgrade_backfills_existing_manual_provenance(tmp_path):
    data_dir = tmp_path / "migration-data"
    _alembic(data_dir, "0002")
    database = data_dir / "knowledge_builder.sqlite3"
    created_at = "2026-01-02T03:04:05+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO manuals (
                original_filename, stored_filename, sha256, file_size_bytes,
                page_count, vendor, model_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy.pdf",
                "manual_1.pdf",
                "a" * 64,
                123,
                5,
                "ASUS",
                "Legacy Board",
                created_at,
            ),
        )

    _alembic(data_dir, "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT document_title, source_url, retrieved_at, language,
                   eligibility_status, vendor, vendor_id
            FROM manuals WHERE original_filename = 'legacy.pdf'
            """
        ).fetchone()
        assert row == ("legacy.pdf", None, created_at, "en", "legacy", "ASUS", "asus")
        columns = {
            column[1]: column[3]
            for column in connection.execute("PRAGMA table_info(manuals)").fetchall()
        }
        assert columns["document_title"] == 1
        assert columns["retrieved_at"] == 1
        assert columns["language"] == 1
        assert columns["acceptance_reviewed_at"] == 0
        assert columns["eligibility_status"] == 1
        assert columns["document_type"] == 0
        setting_columns = {
            column[1]: column[3]
            for column in connection.execute("PRAGMA table_info(settings)").fetchall()
        }
        assert setting_columns["edited_page_number"] == 0
        assert setting_columns["page_review_status"] == 1
        event_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='manual_events'"
        ).fetchone()
        assert event_table == ("manual_events",)
        missed_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='missed_settings'"
        ).fetchone()
        assert missed_table == ("missed_settings",)
        batch_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN ('ingestion_batches', 'ingestion_batch_items')
                """
            )
        }
        assert batch_tables == {"ingestion_batches", "ingestion_batch_items"}
        vendors = connection.execute(
            "SELECT vendor_id, canonical_name FROM vendors ORDER BY vendor_id"
        ).fetchall()
        assert vendors == [
            ("asrock", "ASRock"),
            ("asus", "ASUS"),
            ("gigabyte", "GIGABYTE"),
            ("msi", "MSI"),
        ]
        aliases = connection.execute(
            "SELECT vendor_id FROM vendor_aliases WHERE normalized_alias = 'republicofgamers'"
        ).fetchone()
        assert aliases == ("asus",)
        foreign_keys = connection.execute("PRAGMA foreign_key_list(manuals)").fetchall()
        assert any(row[2:5] == ("vendors", "vendor_id", "vendor_id") for row in foreign_keys)
        applicability = connection.execute(
            "SELECT scope, model_status, warnings_json FROM manual_applicability"
        ).fetchone()
        assert applicability == (
            "unknown",
            "unknown",
            '["No normalized applicability target could be determined."]',
        )
        hardware_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'hardware_platforms', 'chipset_families', 'chipsets',
                    'motherboard_models', 'manual_applicability',
                    'manual_applicability_targets'
                )
                """
            )
        }
        assert len(hardware_tables) == 6
        assert connection.execute(
            "SELECT vendor_id, chipset_id, platform_id FROM motherboard_models "
            "WHERE model_id = 'asus-tuf-gaming-x870-plus-wifi'"
        ).fetchone() == ("asus", "amd-x870", "amd-am5")
        cpu_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'cpu_architectures', 'cpu_families',
                    'cpu_models', 'cpu_model_aliases'
                )
                """
            )
        }
        assert len(cpu_tables) == 4
        assert connection.execute(
            "SELECT cpu_model_id, platform_id, is_x3d FROM cpu_models"
        ).fetchone() == ("amd-ryzen-7-9800x3d", "amd-am5", 1)
        canonical_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'canonical_settings', 'canonical_setting_aliases',
                    'setting_canonical_mappings', 'canonical_mapping_events',
                    'canonical_setting_categories',
                    'canonical_setting_classifications',
                    'canonical_classification_events',
                    'canonical_mapping_suggestions',
                    'canonical_suggestion_events'
                )
                """
            )
        }
        assert len(canonical_tables) == 9
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_setting_categories"
        ).fetchone() == (10,)
        assert connection.execute("SELECT COUNT(*) FROM canonical_settings").fetchone() == (0,)
        evidence_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'evidence_sources', 'evidence_extraction_runs',
                    'evidence_passages', 'evidence_source_events'
                )
                """
            )
        }
        assert len(evidence_tables) == 4
        guidance_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'guidance_claims', 'guidance_claim_evidence',
                    'guidance_claim_settings', 'guidance_claim_events'
                )
                """
            )
        }
        assert len(guidance_tables) == 4
        refusal_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'unanswered_lookups', 'referral_pointers',
                    'referral_pointer_settings', 'referral_pointer_events'
                )
                """
            )
        }
        assert len(refusal_tables) == 4
        auth_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN (
                    'user_accounts', 'auth_sessions', 'auth_events'
                )
                """
            )
        }
        assert len(auth_tables) == 3
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0018",)


def test_hardware_migration_backfill_matches_phase1_scope_rules(tmp_path):
    data_dir = tmp_path / "hardware-migration-data"
    _alembic(data_dir, "0008")
    database = data_dir / "knowledge_builder.sqlite3"
    created_at = "2026-07-16T00:00:00+00:00"
    rows = [
        (
            "asus-amd800.pdf",
            "manual_1.pdf",
            "1" * 64,
            "ASUS",
            "asus",
            "PRIME / ProArt / TUF GAMING AMD 800 Series",
            "AMD 800 Series BIOS Manual",
            "AMD 800 Series",
            "bios_guide",
        ),
        (
            "msi-x670e.pdf",
            "manual_2.pdf",
            "2" * 64,
            "MSI",
            "msi",
            "MPG X670E CARBON WIFI",
            "MPG X670E CARBON WIFI Motherboard User Guide",
            "X670E",
            None,
        ),
        (
            "gigabyte-amd600.pdf",
            "manual_3.pdf",
            "3" * 64,
            "GIGABYTE",
            "gigabyte",
            "AMD X670/B650 Series",
            "AMD 600 Series BIOS Setup Guide",
            "X670; B650; AMD 600 Series",
            "bios_guide",
        ),
    ]
    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO manuals (
                original_filename, stored_filename, sha256, file_size_bytes,
                page_count, vendor, vendor_id, model_name, document_title,
                retrieved_at, language, chipset, document_type,
                eligibility_status, created_at
            ) VALUES (?, ?, ?, 123, 5, ?, ?, ?, ?, ?, 'en', ?, ?, 'legacy', ?)
            """,
            [
                (
                    filename,
                    stored,
                    sha256,
                    vendor,
                    vendor_id,
                    model,
                    title,
                    created_at,
                    chipset,
                    document_type,
                    created_at,
                )
                for (
                    filename,
                    stored,
                    sha256,
                    vendor,
                    vendor_id,
                    model,
                    title,
                    chipset,
                    document_type,
                ) in rows
            ],
        )

    _alembic(data_dir, "head")

    with sqlite3.connect(database) as connection:
        projections = connection.execute(
            """
            SELECT manual_id, scope, detected_model_id
            FROM manual_applicability ORDER BY manual_id
            """
        ).fetchall()
        assert projections == [
            (1, "chipset_family", None),
            (2, "motherboard_model", "msi-mpg-x670e-carbon-wifi"),
            (3, "chipset_set", None),
        ]
        targets = connection.execute(
            """
            SELECT manual_id,
                   COALESCE(platform_id, chipset_family_id, chipset_id,
                            motherboard_model_id)
            FROM manual_applicability_targets
            ORDER BY manual_id, ordinal
            """
        ).fetchall()
        assert targets == [
            (1, "amd-800-series"),
            (2, "msi-mpg-x670e-carbon-wifi"),
            (3, "amd-x670"),
            (3, "amd-b650"),
        ]
        events = connection.execute(
            """
            SELECT COUNT(*) FROM manual_events
            WHERE action = 'hardware_applicability_normalized'
              AND actor = 'system-migration'
            """
        ).fetchone()[0]
        assert events == 3
