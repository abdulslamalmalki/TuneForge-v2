from app.domain import am5_question_acceptance as acceptance


def _spec(question_id):
    return next(row for row in acceptance.QUESTION_SET if row.question_id == question_id)


def test_empty_report_passes_intentional_refusals_but_exposes_factual_gaps(db_session):
    report = acceptance.build_report(db_session)

    assert report.summary.total_question_count == 12
    assert report.summary.expected_supported_count == 9
    assert report.summary.expected_refusal_count == 3
    assert report.summary.actual_supported_count == 0
    assert report.summary.actual_refusal_count == 12
    assert report.summary.passing_question_count == 3
    assert report.summary.unexpected_gap_count == 9
    assert report.summary.acceptance_status == "hold"
    recommendation = next(
        row for row in report.questions if row.question_id == "pbo-scalar-recommendation"
    )
    assert recommendation.passed is True
    assert recommendation.actual_outcome == "refused"
    assert "will not invent values" in recommendation.reason


def test_factual_question_requires_am5_observation_and_every_required_claim_type():
    spec = _spec("pbo-behavior")

    unobserved = acceptance._evaluate_question(
        spec,
        observed_ids=set(),
        claim_types={spec.canonical_id: {"behavior"}},
        claim_counts={spec.canonical_id: 1},
        canonical_names={spec.canonical_id: "Precision Boost Overdrive"},
    )
    missing_type = acceptance._evaluate_question(
        spec,
        observed_ids={spec.canonical_id},
        claim_types={spec.canonical_id: {"definition"}},
        claim_counts={spec.canonical_id: 1},
        canonical_names={spec.canonical_id: "Precision Boost Overdrive"},
    )
    supported = acceptance._evaluate_question(
        spec,
        observed_ids={spec.canonical_id},
        claim_types={spec.canonical_id: {"behavior", "definition"}},
        claim_counts={spec.canonical_id: 2},
        canonical_names={spec.canonical_id: "Precision Boost Overdrive"},
    )

    assert unobserved.actual_outcome == "refused"
    assert "No approved AM5 setting" in unobserved.reason
    assert missing_type.actual_outcome == "refused"
    assert missing_type.reason == "Missing accepted claim type: behavior."
    assert supported.actual_outcome == "supported"
    assert supported.passed is True
    assert supported.approved_claim_count == 2


def test_product_boundaries_are_expected_refusals():
    for question_id in (
        "pbo-scalar-recommendation",
        "exact-system-safety",
        "bios-version-changes",
    ):
        spec = _spec(question_id)
        result = acceptance._evaluate_question(
            spec,
            observed_ids={spec.canonical_id} if spec.canonical_id else set(),
            claim_types={},
            claim_counts={},
            canonical_names={},
        )

        assert result.expected_outcome == "refused"
        assert result.actual_outcome == "refused"
        assert result.passed is True
