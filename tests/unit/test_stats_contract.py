import pytest

from interview_intelligence.contracts import StatsRequest


def test_gap_sort_is_only_valid_for_question_groups():
    with pytest.raises(ValueError):
        StatsRequest(group_by="company", sort="gap")
