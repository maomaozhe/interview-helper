"""Opt-in prompt version and task-dimension transport, without remote inference.

Semantic improvement is deliberately left to the paired real-source evaluation;
mock outputs here test wiring, identity, and independent dimension preservation.
"""
import inspect
import json
from contextlib import nullcontext
from types import SimpleNamespace

import httpx
import pytest

from interview_intelligence.agent import task_annotation as annotation
from interview_intelligence.config import Settings
from interview_intelligence.providers.budget import CallBudget
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.resources import resource_path


class EmptySession:
    def scalars(self,statement):
        return []


def test_v8_is_opt_in_and_existing_default_is_unchanged():
    assert 'task_annotation_v8' in annotation.SUPPORTED_PROMPT_VERSIONS
    assert inspect.signature(annotation.annotate_tasks).parameters['prompt_version'].default == 'task_annotation_v4'


def test_v9_changes_focus_policy_without_changing_response_form_or_wire_policy():
    v8 = resource_path('prompts/task_annotation_v8.md').read_text(encoding='utf-8')
    v9 = resource_path('prompts/task_annotation_v9.md').read_text(encoding='utf-8')
    assert v8 != v9
    assert v8.split('再独立判断 f。',1)[1] == v9.split('再独立判断 f。',1)[1]
    assert 'task_annotation_v9' in annotation.SUPPORTED_PROMPT_VERSIONS


@pytest.mark.parametrize('version',['task_annotation_v4','task_annotation_v7','task_annotation_v8','task_annotation_v9'])
def test_known_prompt_dry_run_never_reads_prompt_or_calls_a_model(monkeypatch,version):
    def forbidden_resource(_):
        raise AssertionError('dry run must not access a model prompt')
    monkeypatch.setattr(annotation,'resource_path',forbidden_resource)
    database = SimpleNamespace(session=lambda:nullcontext(EmptySession()))
    result = annotation.annotate_tasks(database,None,prompt_version=version,dry_run=True)
    assert result == {'pending_in_batch':0,'batches':0,'model_calls':0,'changed':0}


def test_unknown_prompt_is_rejected_before_database_or_model_access():
    def forbidden_database():
        raise AssertionError('invalid prompt must fail before any database access')
    database = SimpleNamespace(session=forbidden_database)
    with pytest.raises(ValueError,match='ANNOTATION_PROMPT_VERSION_INVALID'):
        annotation.annotate_tasks(database,None,prompt_version='task_annotation_v999',dry_run=True)


@pytest.mark.parametrize('version',['task_annotation_v8','task_annotation_v9'])
def test_explicit_candidate_loads_resource_without_changing_default(monkeypatch,tmp_path,version):
    observed = []
    def resource(relative):
        observed.append(relative)
        return resource_path(relative)
    monkeypatch.setattr(annotation,'resource_path',resource)
    database = SimpleNamespace(session=lambda:nullcontext(EmptySession()))
    settings = Settings(model_lock_path=tmp_path/'gate',model_min_interval_seconds=0)
    result = annotation.annotate_tasks(database,settings,prompt_version=version)
    assert observed == [f'prompts/{version}.md']
    assert result['changed'] == result['model_calls'] == 0


def test_v8_wire_preserves_verbal_engineering_and_one_dimension_unknown(monkeypatch,tmp_path):
    # Synthetic fixtures, not any gold question or an executable classifier.
    texts = ['回顾现有项目机制','口头构造指定新功能','仅知求解任务，作答形式未记',
             '写组件代码','写查询','口述求解过程']
    expected = [('VERBAL','NONE'),('VERBAL','ENGINEERING'),('UNKNOWN','ALGORITHM'),
                ('CODE','ENGINEERING'),('SQL','ENGINEERING'),('VERBAL','ALGORITHM')]
    batch = [SimpleNamespace(id=f'fixture-{i}',raw_question=text,source_spans=[])
             for i,text in enumerate(texts)]
    payload = [{'occurrence_id':row.id,'raw_question':row.raw_question,
                'source_quotes':[row.raw_question],'context_before':'原始来源上下文',
                'context_after':'后续上下文'} for row in batch]
    requests,telemetry = [],[]
    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        result = [{'i':i,'f':form,'c':focus,'p':.96}
                  for i,(form,focus) in reversed(list(enumerate(expected)))]
        return httpx.Response(200,json={'model':'fixture','choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps({'items':result})}}],
            'usage':{'prompt_tokens':100,'completion_tokens':100}})
    original_client = httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:
        original_client(transport=httpx.MockTransport(provider),**kwargs))
    class Audit:
        def begin(self): return nullcontext()
        def add(self,entry): telemetry.append(entry)
    database = SimpleNamespace(session=lambda:nullcontext(Audit()))
    settings = Settings(model_api_key='fixture-only',model_base_url='http://fixture.invalid/v1',
        query_model='fixture',model_lock_path=tmp_path/'gate',model_min_interval_seconds=0)
    budget = CallBudget(max_calls=1,max_tokens=100_000)
    prompt = resource_path('prompts/task_annotation_v8.md').read_text(encoding='utf-8')
    result = annotation.model_labels(database,settings,
        ModelCallGate(settings.model_lock_path,minimum_interval_seconds=0),budget,
        prompt,payload,batch,'task_annotation_v8')
    assert len(requests) == budget.used_calls == 1
    assert requests[0]['messages'][0]['content'] == prompt
    compact_payload = json.loads(requests[0]['messages'][1]['content'])
    assert [item['i'] for item in compact_payload] == list(range(len(batch)))
    assert all('occurrence_id' not in item and item['source_quotes']==[texts[item['i']]]
               for item in compact_payload)
    by_id = {item.occurrence_id:item for item in result.items}
    assert set(by_id) == {row.id for row in batch}
    for i,row in enumerate(batch):
        assert (by_id[row.id].response_form,by_id[row.id].coding_focus) == expected[i]
        assert by_id[row.id].evidence_quote == row.raw_question
    assert telemetry[0].prompt_version == 'task_annotation_v8'
    assert telemetry[0].status == 'SUCCEEDED'
