"""Read-only parser evaluation utility."""

from pathlib import Path

from tools.evaluate_parser import evaluate_paths, evaluate_pdf

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pdfs"


def test_evaluation_summarizes_parser_output():
    evaluation = evaluate_pdf(FIXTURES / "basic_manual.pdf")
    assert evaluation.page_count == 3
    assert evaluation.candidate_count == 5
    assert evaluation.candidate_pages == 3
    assert evaluation.parser_version == "1.3"
    assert evaluation.sample_candidate_names[0] == "Ai Overclock Tuner"


def test_evaluate_paths_preserves_input_order():
    paths = [FIXTURES / "no_descriptions.pdf", FIXTURES / "basic_manual.pdf"]
    evaluations = evaluate_paths(paths)
    assert [Path(item.path).name for item in evaluations] == [
        "no_descriptions.pdf",
        "basic_manual.pdf",
    ]
    assert evaluations[0].candidates_with_empty_description > 0
