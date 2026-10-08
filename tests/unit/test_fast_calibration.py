from evals.project_quality.calibrate_fast import fit,operating_point


def test_high_confidence_error_prevents_false_calibration_pass():
    rows=[{"candidate_plan":{"action":"LIST"},"minimum_confidence":.99,"typed_correct":True} for _ in range(25)]
    rows.append({"candidate_plan":{"action":"LIST"},"minimum_confidence":.999,"typed_correct":False})
    best,curve=fit(rows)
    assert best["threshold"] is None and best["accepted_precision"] is None
    assert operating_point(rows,.99)["accepted_errors"]==1


def test_calibration_excludes_fallbacks_from_effective_fast_coverage():
    rows=[{"candidate_plan":{"action":"LIST"},"minimum_confidence":.95,"typed_correct":True} for _ in range(25)]
    rows += [{"candidate_plan":None,"minimum_confidence":1,"typed_correct":True} for _ in range(10)]
    rows.append({"candidate_plan":{"action":"LIST"},"minimum_confidence":.94,"typed_correct":False})
    best,curve=fit(rows)
    assert best["threshold"]==.95 and best["accepted_samples"]==25 and best["accepted_errors"]==0
    assert best["accepted_coverage"]==25/36
