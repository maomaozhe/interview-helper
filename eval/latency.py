"""Offline decomposition of persisted query timings; no inferred provider time."""
from collections import Counter
import math
from pathlib import Path

from eval.common import digest, read_jsonl


FIELDS = ('latency_ms', 'provider_ms', 'queue_ms', 'interval_ms', 'input_tokens', 'output_tokens')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sum_known(calls, field):
    values = [call.get(field) for call in calls]
    # The embedding API may omit output usage because it generates no text.
    if field == 'output_tokens':
        values = [0 if value is None and call.get('operation_type') == 'EMBEDDING' else value
                  for call, value in zip(calls, values, strict=True)]
    valid = [value for value in values if _number(value)]
    return {'known_sum': sum(valid), 'unknown_count': len(values) - len(valid)}


def query_latency(rows):
    """Count row-level calls once; tool_ms and decision_ms are nested timings."""
    seen_cases, seen_calls, cases = set(), set(), []
    for row in rows:
        if not isinstance(row.get('id'), str) or row['id'] in seen_cases:
            raise ValueError('PROFILE_DUPLICATE_OR_MISSING_SAMPLE_ID')
        seen_cases.add(row['id'])
        calls = row.get('model_calls')
        telemetry_available = isinstance(calls, list) and not row.get('telemetry_unavailable', False)
        calls = calls if isinstance(calls, list) else []
        for call in calls:
            if not isinstance(call.get('id'), str) or call['id'] in seen_calls:
                raise ValueError('PROFILE_DUPLICATE_OR_MISSING_CALL_ID')
            seen_calls.add(call['id'])
        turns = row.get('turns', [])
        timings = [turn.get('result', {}).get('meta', {}).get('timings', {}) for turn in turns]
        totals = [item.get('total_ms') for item in timings]
        server_ms = sum(totals) if totals and all(_number(value) for value in totals) else None
        stages = {}
        for operation in sorted({call.get('operation_type', 'UNKNOWN') for call in calls}):
            selected = [call for call in calls if call.get('operation_type', 'UNKNOWN') == operation]
            stages[operation] = {'calls': len(selected), **{field: _sum_known(selected, field) for field in FIELDS}}
        sums = {field: _sum_known(calls, field) for field in FIELDS}
        wall = sums['latency_ms']
        remainder = (server_ms - wall['known_sum'] if server_ms is not None
                     and telemetry_available and not wall['unknown_count'] else None)
        phases = [sums[field] for field in ('provider_ms', 'queue_ms', 'interval_ms')]
        accounted = (sum(value['known_sum'] for value in phases)
                     if telemetry_available and all(value['unknown_count'] == 0 for value in phases) else None)
        cases.append({'id': row['id'], 'snapshot': row.get('snapshot'), 'server_total_ms': server_ms,
            'turn_count': len(turns), 'telemetry_available': telemetry_available,
            'calls': len(calls), 'stages': stages, 'totals': sums,
            'server_minus_call_wall_ms': remainder,
            'overlapping_or_inconsistent_wall': remainder is not None and remainder < 0,
            'server_minus_known_phases_ms': server_ms - accounted if server_ms is not None and accounted is not None else None,
            'prompt_versions': dict(Counter(call.get('prompt_version', 'UNKNOWN') for call in calls)),
            'transport_retry_count': sum(call.get('retry_count', 0) > 0 for call in calls)})
    return cases


def compare_latency(before, after):
    """Compare persisted observations, without predicting counterfactual speed."""
    paths = [Path(before), Path(after)]
    profiles = [query_latency(read_jsonl(path)) for path in paths]
    left, right = ({case['id']: case for case in profile} for profile in profiles)
    if set(left) != set(right):
        raise ValueError('PROFILE_SAMPLE_SET_CHANGED')
    cases = []
    for identifier in left:
        old, new = left[identifier], right[identifier]
        if old['snapshot'] != new['snapshot']:
            raise ValueError('PROFILE_SNAPSHOT_CHANGED')
        stage_delta = {}
        for operation in sorted(old['stages'].keys() | new['stages'].keys()):
            stages = [side['stages'].get(operation, {'calls': 0, **{
                field: {'known_sum': 0, 'unknown_count': 0} for field in FIELDS}}) for side in (old, new)]
            stage_delta[operation] = {'calls': stages[1]['calls'] - stages[0]['calls'], **{
                field: (stages[1][field]['known_sum'] - stages[0][field]['known_sum']
                        if not stages[0][field]['unknown_count'] and not stages[1][field]['unknown_count'] else None)
                for field in FIELDS}}
        cases.append({'id': identifier, 'before': old, 'after': new,
            'server_delta_ms': (new['server_total_ms'] - old['server_total_ms']
                                if old['server_total_ms'] is not None and new['server_total_ms'] is not None else None),
            'stage_delta': stage_delta})
    return {'schema': 'persisted_query_latency_comparison_v1',
        'inputs': {key: {'path': str(path), 'sha256': digest(path.read_bytes())}
                   for key, path in zip(('before', 'after'), paths, strict=True)},
        'cases': cases,
        'scope': 'Single recorded runs and their persisted telemetry. Stage sums overlap tool/decision timing; no SLO, throughput or counterfactual speed claim.',
        'unknown_policy': 'Missing/invalid phase timing and usage remain unknown, never inferred from total.',
        'network_requests': 0, 'model_calls': 0}
