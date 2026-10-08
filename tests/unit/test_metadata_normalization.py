import pytest

from interview_intelligence.ingestion.pipeline import _normalize_company, _normalize_round


@pytest.mark.parametrize("raw,expected", [("PDD", "拼多多"), ("boss", "BOSS直聘"),
                                          ("BOSS直聘", "BOSS直聘"), ("腾讯音乐", "腾讯音乐"),
                                          ("腾讯", "腾讯"), ("boss项目", "boss项目")])
def test_company_alias_is_exact_and_preserves_separate_business_identity(raw, expected):
    assert _normalize_company(raw) == expected


def test_round_does_not_assign_a_multi_round_title_to_the_first_round():
    assert _normalize_round("一面二面三面面经") is None
    assert _normalize_round("二面转HR面") is None
    assert _normalize_round("二面 80min") == "SECOND"
    assert _normalize_round("HR面") == "HR"
