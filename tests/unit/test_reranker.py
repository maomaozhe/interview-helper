import json
from types import SimpleNamespace
import pytest
from interview_intelligence.search.reranker import LLMReranker

class Client:

    def __init__(self, ids, grades=None, evidence=None, scopes=None, query_evidence=None):
        self.ids = ids
        self.grades = grades or {}
        self.evidence = evidence or {}
        self.scopes = scopes or {}
        self.query_evidence = query_evidence or {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        request = json.loads(kwargs['messages'][-1]['content'])
        texts = {row['id']: row['question'] for row in request['candidates']}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'query_object': '测试对象', 'query_focus': '测试目标', 'rankings': [{'candidate_id': item, 'object_relation': 'EXPLICIT' if self.grades.get(item, 3) >= 2 else 'NONE', 'focus_relation': {3: 'DIRECT', 2: 'SUBTASK', 1: 'NEIGHBOR', 0: 'NONE'}[self.grades.get(item, 3)], 'object_evidence': self.evidence.get(item, {}).get('object', texts.get(item, '')), 'focus_evidence': self.evidence.get(item, {}).get('focus', texts.get(item, ''))} for item in self.ids]})))])

def test_reranker_must_return_exact_candidate_set():
    candidates = [{'canonical_question_id': 'a', 'canonical_text': 'Redis 过期'}, {'canonical_question_id': 'b', 'canonical_text': '缓存击穿'}]
    reranker = LLMReranker(client=Client(['c1', 'c0']), model='test')
    assert [item['canonical_question_id'] for item in reranker.rerank('击穿', candidates)] == ['b', 'a']
    with pytest.raises(ValueError, match='candidate set'):
        LLMReranker(client=Client(['c1']), model='test').rerank('击穿', candidates)

def test_reranker_does_not_present_unrelated_or_only_broadly_related_questions():
    candidates = [{'canonical_question_id': 'a', 'canonical_text': 'OOM 排查'}, {'canonical_question_id': 'b', 'canonical_text': 'JVM 类加载'}, {'canonical_question_id': 'c', 'canonical_text': 'Redis 数据类型'}]
    reranker = LLMReranker(client=Client(['c0', 'c1', 'c2'], {'c0': 3, 'c1': 1, 'c2': 0}), model='test')
    rows = reranker.rerank('OOM 问法', candidates)
    assert [row['canonical_question_id'] for row in rows] == ['a']
    assert rows[0]['relevance_grade'] == 3
    assert LLMReranker(client=Client(['c0'], {'c0': 0}), model='test').rerank('OOM', candidates[:1]) == []

def test_ark_rerank_disables_unbounded_reasoning_and_caps_output():
    captured = []
    underlying = Client(['c0'])

    def create(**kwargs):
        captured.append(kwargs)
        return underlying.create(**kwargs)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    candidates = [{'canonical_question_id': 'a', 'canonical_text': 'OOM排查'}]
    LLMReranker(client=client, model='test', base_url='https://ark.cn-beijing.volces.com/api/coding/v3').rerank('OOM', candidates)
    assert captured[0]['extra_body'] == {'thinking': {'type': 'disabled'}}
    assert captured[0]['max_tokens'] == 6144
    LLMReranker(client=client, model='test', base_url='https://another.example/v1').rerank('OOM', candidates)
    assert 'extra_body' not in captured[1]

@pytest.mark.parametrize('ids', [['c0', 'c0'], ['c0', 'unknown']])
def test_reranker_rejects_duplicate_or_invented_ids(ids):
    candidates = [{'canonical_question_id': 'a'}, {'canonical_question_id': 'b'}]
    with pytest.raises(ValueError, match='candidate set'):
        LLMReranker(client=Client(ids), model='test').rerank('OOM', candidates)

@pytest.mark.parametrize('finish_reason', ['stop', None, 'length'])
def test_streamed_reranker_requires_complete_output_and_keeps_usage(finish_reason):
    payload = json.dumps({'query_object': '内存', 'query_focus': 'OOM排查', 'rankings': [{'candidate_id': 'c0', 'object_relation': 'EXPLICIT', 'focus_relation': 'DIRECT', 'object_evidence': 'OOM', 'focus_evidence': 'OOM排查'}]})
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part), finish_reason=None)], usage=None, model='resolved-test') for part in [payload[:20], payload[20:]]]
    if finish_reason:
        chunks.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason=finish_reason)], usage=None, model='resolved-test'))
    chunks.append(SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20), model='resolved-test'))

    class Stream:
        closed = False

        def __iter__(self):
            return iter(chunks)

        def close(self):
            self.closed = True
    response = Stream()
    requests, logs = ([], [])

    def create(**kwargs):
        requests.append(kwargs)
        return response
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reranker = LLMReranker(client=client, model='test', stream=True, on_call=logs.append)
    if finish_reason == 'stop':
        assert reranker.rerank('OOM', [{'canonical_question_id': 'a', 'canonical_text': 'OOM排查'}])[0]['canonical_question_id'] == 'a'
        assert logs[0]['input_tokens'] == 50
        assert logs[0]['model_revision'] == 'resolved-test'
    else:
        with pytest.raises(ValueError, match='STREAM_INCOMPLETE|MODEL_OUTPUT_TRUNCATED'):
            reranker.rerank('OOM', [{'canonical_question_id': 'a'}])
        assert logs[0]['status'] == 'FAILED'
    assert requests[0]['stream'] is True
    assert requests[0]['stream_options'] == {'include_usage': True}
    assert response.closed

@pytest.mark.parametrize('evidence', [{'object': '不存在的Agent', 'focus': '内存泄漏排查'}, {'object': '内存', 'focus': '内存...排查'}, {'object': '', 'focus': '内存泄漏排查'}, {'object': '内存', 'focus': '   '}])
def test_high_score_cannot_replace_grounded_object_and_focus_evidence(evidence):
    candidates = [{'canonical_question_id': 'a', 'canonical_text': '线上内存泄漏排查'}, {'canonical_question_id': 'b', 'canonical_text': 'OOM排查'}]
    rows = LLMReranker(client=Client(['c0', 'c1'], evidence={'c0': evidence}), model='test').rerank('内存泄漏', candidates)
    assert [row['canonical_question_id'] for row in rows] == ['b']
    assert rows.audit['candidates'][0]['decision'] == 'INVALID_EVIDENCE'
    assert rows.audit['candidates'][0]['invalid_quotes'] == {key: value.strip() for key, value in evidence.items()}
    assert rows.audit['candidates'][1]['decision'] == 'ACCEPTED'
    assert rows[0]['relevance_evidence'] == {'object': 'OOM排查', 'focus': 'OOM排查'}

def test_evidence_and_diagnostics_are_bound_to_each_rerank_call():
    reranker = LLMReranker(client=Client(['c0']), model='test')
    first = reranker.rerank('Agent记忆', [{'canonical_question_id': 'a', 'canonical_text': 'Agent记忆设计'}])
    second = reranker.rerank('秒杀', [{'canonical_question_id': 'b', 'canonical_text': '秒杀库存设计'}])
    assert first.audit['candidates'][0]['canonical_question_id'] == 'a'
    assert second.audit['candidates'][0]['canonical_question_id'] == 'b'
    assert first[0]['relevance_evidence']['object'] == 'Agent记忆设计'
