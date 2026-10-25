import pytest

from eval.latency import compare_latency, query_latency
from eval.common import write_jsonl


def sample(*, total=110, calls=None):
    return {'id': 'q1', 'snapshot': {'corpus_revision': 1}, 'turns': [
        {'result': {'meta': {'timings': {'total_ms': total, 'tool_ms': 100, 'decision_ms': 10}}}}],
        'model_calls': calls if calls is not None else [
            {'id': 'call-1', 'operation_type': 'RERANK', 'latency_ms': 90, 'provider_ms': 80,
             'queue_ms': 0, 'interval_ms': 8, 'input_tokens': 10, 'output_tokens': 2}]}


def test_profile_counts_outer_calls_once_and_does_not_add_nested_tool_time():
    row = sample()
    row['turns'][0]['model_calls'] = row['model_calls']
    case = query_latency([row])[0]
    assert case['calls'] == 1
    assert case['server_minus_call_wall_ms'] == 20
    assert case['server_minus_known_phases_ms'] == 22
    assert case['stages']['RERANK']['provider_ms']['known_sum'] == 80


@pytest.mark.parametrize('bad', [None, -1, True, '10', float('nan')])
def test_missing_or_invalid_provider_phase_is_unknown_not_inferred(bad):
    row = sample()
    row['model_calls'][0]['provider_ms'] = bad
    case = query_latency([row])[0]
    assert case['totals']['provider_ms'] == {'known_sum': 0, 'unknown_count': 1}
    assert case['server_minus_known_phases_ms'] is None


def test_missing_whole_telemetry_does_not_make_total_unallocated_time():
    row = sample()
    del row['model_calls']
    case = query_latency([row])[0]
    assert case['server_minus_call_wall_ms'] is None
    assert not case['telemetry_available']


def test_overlap_is_flagged_without_clamping_or_labeling_it_as_negative_overhead():
    case = query_latency([sample(total=50)])[0]
    assert case['server_minus_call_wall_ms'] == -40
    assert case['overlapping_or_inconsistent_wall']


def test_repeated_call_id_blocks_double_counting():
    row = sample()
    row['model_calls'] *= 2
    with pytest.raises(ValueError, match='DUPLICATE_OR_MISSING_CALL_ID'):
        query_latency([row])


def test_comparison_reports_new_stage_and_unknown_delta(tmp_path):
    before, after = sample(), sample(total=200)
    after['model_calls'][0]['provider_ms'] = None
    after['model_calls'].append({'id': 'repair', 'operation_type': 'RERANK_EVIDENCE_REPAIR',
        'latency_ms': 60, 'provider_ms': 50, 'queue_ms': 0, 'interval_ms': 10,
        'input_tokens': 6, 'output_tokens': 2})
    paths = [tmp_path/'before.jsonl', tmp_path/'after.jsonl']
    for path, value in zip(paths, (before, after), strict=True):
        write_jsonl(path, [value])
    comparison = compare_latency(*paths)['cases'][0]
    assert comparison['server_delta_ms'] == 90
    assert comparison['stage_delta']['RERANK']['provider_ms'] is None
    assert comparison['stage_delta']['RERANK_EVIDENCE_REPAIR']['provider_ms'] == 50


def test_comparison_rejects_snapshot_change(tmp_path):
    paths = [tmp_path/'a.jsonl', tmp_path/'b.jsonl']
    rows = [sample(), sample()]
    rows[1]['snapshot']['corpus_revision'] = 2
    for path, row in zip(paths, rows, strict=True):
        write_jsonl(path, [row])
    with pytest.raises(ValueError, match='PROFILE_SNAPSHOT_CHANGED'):
        compare_latency(*paths)
