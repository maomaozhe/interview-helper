import pytest

from evals.project_quality.source_iteration import selected_samples


MANIFEST = {"extraction": [{"id": "a", "split": "test"}, {"id": "b", "split": "test"},
                           {"id": "dev", "split": "dev"}]}


def test_default_does_not_silently_drop_failed_or_unselected_test_rows():
    assert [r["id"] for r in selected_samples(MANIFEST, "extraction", {})] == ["a", "b"]


@pytest.mark.parametrize("ids", [[], ["a", "a"], ["dev"], ["missing"]])
def test_explicit_diagnostic_selection_cannot_hide_a_missing_or_duplicated_id(ids):
    with pytest.raises(ValueError):
        selected_samples(MANIFEST, "extraction", {"sample_ids": ids})


def test_failure_replay_is_an_explicit_protocol_subset_preserving_source_order():
    assert selected_samples(MANIFEST, "extraction", {"sample_ids": ["b"]}) == [{"id": "b", "split": "test"}]
