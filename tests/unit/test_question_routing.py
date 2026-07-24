import pytest

from app.domain import question_routing


def test_exact_reviewed_question_routes_to_stable_question_id():
    decision = question_routing.route("  WHAT does PBO Scalar do?!  ")

    assert decision.kind == "guided_question"
    assert decision.question_id == "pbo-scalar-behavior"
    assert decision.routed_value == "What does PBO Scalar do?"
    assert decision.rule == "exact_reviewed_question"


@pytest.mark.parametrize(
    ("query", "setting", "rule"),
    [
        ("What is Curve Optimizer?", "Curve Optimizer", "what_is"),
        ("Explain PBO Scalar in the BIOS.", "PBO Scalar", "what_is"),
        ("What does CPU Boost Scalar mean?", "CPU Boost Scalar", "what_does"),
        ("How does Precision Boost Overdrive work?", "Precision Boost Overdrive", "how_does"),
    ],
)
def test_transparent_wrappers_extract_only_the_setting(query, setting, rule):
    decision = question_routing.route(query)

    assert decision.kind == "setting_lookup"
    assert decision.routed_value == setting
    assert decision.rule == rule
    assert decision.transformed is True


def test_unsupported_advice_question_is_not_rewritten_or_guessed():
    query = "Should I enable PBO Scalar for gaming?"
    decision = question_routing.route(query)

    assert decision.kind == "setting_lookup"
    assert decision.routed_value == query
    assert decision.rule == "exact_setting_input"
    assert decision.transformed is False


def test_question_validation_is_bounded():
    with pytest.raises(question_routing.QuestionRoutingError, match="Enter"):
        question_routing.route("   ")
    with pytest.raises(question_routing.QuestionRoutingError, match="300"):
        question_routing.route("x" * 301)
