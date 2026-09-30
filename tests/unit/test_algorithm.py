from interview_intelligence.ingestion.algorithm import parse_algorithm_match


def test_explicit_leetcode_number_preserved():
    match = parse_algorithm_match("手撕 lc32 最长有效括号")
    assert match["problem_id"] == "32"
    assert match["platform"] == "LEETCODE"
    assert match["match_status"] == "EXPLICIT"


def test_generic_description_does_not_invent_problem_id():
    match = parse_algorithm_match("手撕一道括号匹配题")
    assert match["problem_id"] is None
    assert match["match_status"] == "UNMATCHED"
