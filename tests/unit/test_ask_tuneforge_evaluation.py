import json
from pathlib import Path

import pytest

from app.evaluation.ask_tuneforge import DATASET_VERSION, load_dataset

DATASET = Path(__file__).resolve().parents[2] / "evaluations" / "ask_tuneforge_v1.json"


def test_checked_in_ask_dataset_is_versioned_and_covers_required_risks():
    dataset = load_dataset(DATASET)

    assert dataset.dataset_version == DATASET_VERSION
    assert len(dataset.cases) >= 50
    categories = {case.category for case in dataset.cases}
    assert {
        "supported",
        "incorrect-terminology",
        "ambiguous",
        "unsupported-hardware",
        "vendor-specific",
        "hardware-scope",
        "bios-version-conflict",
        "recommendation",
        "risky-request",
        "insufficient-evidence",
    }.issubset(categories)
    joined = " ".join(case.query.casefold() for case in dataset.cases)
    for topic in (
        "load-line calibration",
        "svid behavior",
        "current capability",
        "cep",
        "tvb",
        "multicore enhancement",
        "cpu lite load",
        "pbo",
        "curve optimizer",
        "expo",
        "xmp",
        "gear 1",
        "above 4g",
        "resizable bar",
        "c-states",
        "speedstep",
        "speed shift",
        "pl1",
        "tau",
        "soc voltage",
        "vddio",
        "vddq",
        "memory context restore",
        "power down enable",
        "precision boost",
        "cppc",
        "cppc preferred cores",
    ):
        assert topic in joined


def test_dataset_loader_rejects_duplicate_case_ids(tmp_path):
    path = tmp_path / "duplicate.json"
    case = {
        "case_id": "same",
        "category": "refusal",
        "query": "Unknown",
        "expected_outcome": "refused",
        "expected_resolution_status": "unresolved",
        "expected_answer_states": ["No Verified Answer"],
    }
    path.write_text(
        json.dumps(
            {
                "dataset_version": DATASET_VERSION,
                "title": "duplicate",
                "cases": [case, case],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_dataset(path)


def test_dataset_loader_rejects_unknown_outcome(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(
            {
                "dataset_version": DATASET_VERSION,
                "title": "invalid",
                "cases": [
                    {
                        "case_id": "bad",
                        "category": "invalid",
                        "query": "Unknown",
                        "expected_outcome": "invented",
                        "expected_resolution_status": "unresolved",
                        "expected_answer_states": ["No Verified Answer"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_outcome"):
        load_dataset(path)
