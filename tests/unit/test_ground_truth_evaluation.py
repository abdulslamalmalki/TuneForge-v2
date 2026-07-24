from tools.evaluate_ground_truth import normalize_name


def test_ground_truth_name_normalization_is_stable():
    assert normalize_name("  CPU   Load-Line Calibration  ") == "cpu load-line calibration"
