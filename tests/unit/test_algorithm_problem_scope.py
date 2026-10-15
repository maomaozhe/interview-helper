"""Algorithm source task proof is required even when similarity scores are high."""
import pytest
import json

from interview_intelligence.search.reranker import LLMReranker, CandidateRank, RerankResult
from test_reranker import Client as BaseClient


class Client(BaseClient):
    """The same task-proof cases run against frozen production and workspace schemas."""
    def create(self, **kwargs):
        response = super().create(**kwargs)
        value = json.loads(response.choices[0].message.content)
        for item in value["rankings"]:
            item.update(object_relation="EXPLICIT", focus_relation="DIRECT")
        value["rankings"] = [{key: item[key] for key in CandidateRank.model_fields}
                             for item in value["rankings"]]
        value = {key: value[key] for key in RerankResult.model_fields}
        response.choices[0].message.content = json.dumps(value)
        return response


@pytest.mark.parametrize("task", ["DESIGN_TASK", "PROJECT_REPORT", "CODE_TASK", "LOCAL_DETAIL", "KNOWLEDGE", "UNKNOWN"])
def test_algorithm_problem_rejects_other_tasks_despite_direct_score(task):
    candidate = {"canonical_question_id": "bad", "canonical_text": "布隆过滤器的原理和特点是什么？"}
    client = Client(["c0"], scopes={"c0": {"candidate_task": task}},
                    query_evidence={"task": "ALGORITHM_PROBLEM"})
    rows = LLMReranker(client=client, model="fixture")._rank_candidates("字节算法题", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["decision"] == "TASK_REJECTED"


def test_algorithm_problem_cannot_borrow_task_proof_from_neighbor_or_unverified_source():
    candidate = {"canonical_question_id": "bad", "canonical_text": "布隆过滤器的容量？",
        "source_context": [
            {"source_context_status": "VERIFIED", "original_question": "布隆过滤器的容量？",
             "context_before": "算法：岛屿问题 (dfs)"},
            {"source_context_status": "UNAVAILABLE", "original_question": "算法：岛屿问题 (dfs)"}]}
    client = Client(["c0"], scopes={"c0": {"candidate_task": "ALGORITHM_TASK",
                                          "task_evidence": "岛屿问题 (dfs)"}},
                    query_evidence={"task": "ALGORITHM_PROBLEM"})
    rows = LLMReranker(client=client, model="fixture")._rank_candidates("字节算法题", [candidate])
    assert rows == []
    assert rows.audit["candidates"][0]["task_grounded"] is False


def test_algorithm_original_proof_does_not_require_a_known_annotation_or_code_prefix():
    candidate = {"canonical_question_id": "island", "canonical_text": "求岛屿数量。",
        "annotation_status": "UNKNOWN", "source_context": [
            {"source_context_status": "VERIFIED", "original_question": "岛屿问题 (dfs)"}]}
    client = Client(["c0"], scopes={"c0": {"candidate_task": "ALGORITHM_TASK",
                                          "task_evidence": "岛屿问题 (dfs)"}},
                    query_evidence={"task": "ALGORITHM_PROBLEM"})
    rows = LLMReranker(client=client, model="fixture")._rank_candidates("字节算法题", [candidate])
    assert [row["canonical_question_id"] for row in rows] == ["island"]


def test_engineering_request_still_accepts_code_tasks():
    candidate = {"canonical_question_id": "component", "canonical_text": "实现并发任务处理器。"}
    client = Client(["c0"], scopes={"c0": {"candidate_task": "CODE_TASK"}})
    assert LLMReranker(client=client, model="fixture")._rank_candidates("手写工程组件", [candidate])
