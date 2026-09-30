"""Conservative coding-question identification from explicit source wording."""

import re


_LEETCODE_NUMBER = re.compile(
    r"(?i)(?:\blc\s*#?\s*(\d{1,5})\b|\bleetcode\s*#?\s*(\d{1,5})\b|力扣\s*(?:第)?\s*(\d{1,5})\s*(?:题)?)"
)


def parse_algorithm_match(text: str) -> dict:
    match = _LEETCODE_NUMBER.search(text)
    if match:
        number = next(group for group in match.groups() if group)
        return {"coding_kind": "LEETCODE", "platform": "LEETCODE",
                "problem_id": number, "title": None, "match_status": "EXPLICIT",
                "match_basis": "SOURCE_EXPLICIT", "confidence": 1.0,
                "evidence": {"matched_text": match.group(0)}}
    coding_kind = "SQL" if re.search(r"(?i)\bSQL\b|数据库查询", text) else (
        "CODING_TASK" if any(word in text for word in ("手撕", "编程", "代码", "算法题")) else "UNKNOWN")
    return {"coding_kind": coding_kind, "platform": None,
            "problem_id": None, "title": None, "match_status": "UNMATCHED",
            "match_basis": "DESCRIPTION_ONLY", "confidence": 0.0, "evidence": {}}
