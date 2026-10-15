"""Source evidence isolation and observable multi-stage decisions; no APIs."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from interview_intelligence.dedup.context import VERSION, compile_source_evidence
from interview_intelligence.dedup.provider import AnswerBoundaryVerification, OpenAICompatibleJudge
from interview_intelligence.ingestion.snapshot import decode_source
from interview_intelligence.resources import resource_path


def source(text, target, *, encoded=None):
    raw = text.encode("utf-8") if encoded is None else encoded
    start = text.index(target)
    spans = [{"start_char": start, "end_char": start + len(target), "quote": target}]
    return raw, spans, hashlib.sha256(raw).hexdigest()


def evidence(text, target, path="sources/fixture.md"):
    raw, spans, sha = source(text, target)
    return compile_source_evidence(raw, spans, raw_question=target, source_hash=sha, source_path=path)


def client_for(*results):
    results = list(results)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(results.pop(0))))],
            usage=SimpleNamespace(prompt_tokens=23, completion_tokens=11), model="observed-r7")
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), requests


def decision(label="SAME"):
    return {"decision": label, "reason_code": "fixture", "confidence": .8}


def verification(**values):
    return {**{key: True for key in AnswerBoundaryVerification.model_fields if key != "reason_code"},
            "reason_code": "fixture_scope", **values}


def v3(client, **options):
    return OpenAICompatibleJudge(client=client, model="alias", prompt_path=resource_path("prompts/dedup_judge_v3.md"),
                                 max_attempts=1, **options)


def test_compiler_authenticates_original_bytes_and_normalized_span_offsets():
    text = "# 数据库甲\n手写：构造有界队列\n2.解释队列如何扩容\n"
    raw, spans, sha = source(text, "构造有界队列", encoded=b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    result = compile_source_evidence(raw, spans, raw_question="构造有界队列", source_hash=sha)
    assert result["raw_question"] == "构造有界队列"
    assert result["version"] == VERSION and result["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert any(s["role"] == "target_line_prefix" and s["quote"] == "手写：" for s in result["references"])
    assert any(s["role"] == "reference_after" and "扩容" in s["quote"] for s in result["references"])
    for item in result["source_spans"] + result["references"]:
        assert text[item["start_char"]:item["end_char"]] == item["quote"]
    assert all(s["use"] == "reference_only" for s in result["references"])
    assert "扩容" not in result["raw_question"]


def test_exact_source_whitespace_survives_strict_model_validation():
    target = " 解释队列 \n"
    result = evidence("# 系统\n9. " + target + "下一题", target)
    assert result["raw_question"] == target
    assert any(s["quote"] == "9. " for s in result["references"])


@pytest.mark.parametrize("tamper", ["bytes", "span", "raw_question", "overlap"])
def test_source_corruption_is_rejected_before_becoming_evidence(tamper):
    raw, spans, sha = source("# 原文\n1.比较队列实现\n", "比较队列实现")
    question = "比较队列实现"
    if tamper == "bytes": raw += b"changed"
    if tamper == "span": spans[0]["start_char"] += 1
    if tamper == "raw_question": question = "只解释队列实现"
    if tamper == "overlap": spans *= 2; question += "\n" + question
    with pytest.raises(ValueError):
        compile_source_evidence(raw, spans, raw_question=question, source_hash=sha)


def test_context_is_bounded_without_rewriting_multispan_target():
    text = "# 基础讨论\n" + ("前文" * 800 + "\n") * 30 + "问题主体\n给定条件\n" + ("后文" * 800 + "\n") * 5
    raw = text.encode("utf-8")
    spans = [{"start_char": text.index(q), "end_char": text.index(q) + len(q), "quote": q}
             for q in ("问题主体", "给定条件")]
    result = compile_source_evidence(raw, spans, raw_question="问题主体\n给定条件", source_hash=hashlib.sha256(raw).hexdigest())
    assert sum(len(s["quote"]) for s in result["references"]) <= 4000
    assert all(len(s["quote"]) <= 500 for s in result["references"])
    assert result["raw_question"] == "问题主体\n给定条件"


def test_source_evidence_cannot_be_rebound_to_a_different_question():
    client, requests = client_for(decision())
    with pytest.raises(ValueError, match="different question"):
        v3(client).judge("解释事件循环", "解释调度器", incoming_evidence=evidence("# 数据库甲\n事务特性", "事务特性"))
    assert requests == []


def test_same_raw_question_keeps_its_independent_source_on_each_side():
    target = "解释故障恢复过程"
    left = evidence("问数据库甲\n" + target + "\n指定单个副本掉线如何恢复", target, "sources/a.md")
    right = evidence("# 消息队列乙\n" + target + "\n如何确保消费者恰好一次", target, "sources/b.md")
    client, requests = client_for(decision("RELATED"))
    events = []
    judge = v3(client, on_call=events.append)
    judge.judge(target, target, incoming_evidence=left, candidate_evidence=right)
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["incoming"] == payload["candidate"] == target
    assert payload["incoming_evidence"] == left and payload["candidate_evidence"] == right
    assert "消息队列乙" not in json.dumps(payload["incoming_evidence"], ensure_ascii=False)
    assert "数据库甲" not in json.dumps(payload["candidate_evidence"], ensure_ascii=False)
    assert events[0]["parsed_result"] == decision("RELATED")
    assert events[0]["model_revision"] == "observed-r7"
    assert events[0]["comparison_context"]["incoming_evidence_sha256"] != events[0]["comparison_context"]["candidate_evidence_sha256"]


def test_v3_single_stage_and_missing_context_remain_supported():
    client, requests = client_for(decision())
    judge = v3(client)
    assert judge.judge("解释事件循环", "说明事件循环").decision == "SAME"
    assert requests[0]["messages"][1]["content"] == "新问题：解释事件循环\n候选标准题：说明事件循环"
    assert len(requests) == 1 and judge.collection_fingerprint()["verify_equivalence"] is False
    assert judge.version == "dedup_judge_v10_source_evidence"


def test_batch_source_binding_and_guard_booleans_are_preserved_without_shared_trace_state():
    incoming = evidence("# 调度器甲\n解释优先级调度", "解释优先级调度")
    first = evidence("# 调度器甲\n口述优先级调度", "口述优先级调度")
    second = evidence("# 调度器甲\n手写调度器", "手写调度器")
    client, requests = client_for({"decisions": [{"candidate_id": "c0", **decision()},
        {"candidate_id": "c1", **decision()}]}, verification(), verification(same_response_form=False))
    events = []
    judge = v3(client, verify_equivalence=True, on_call=events.append)
    result = judge.judge_many("解释优先级调度", [("oral", "口述优先级调度"), ("code", "手写调度器")],
        incoming_evidence=incoming, candidate_evidence={"oral": first, "code": second})
    payload = json.loads(requests[0]["messages"][1]["content"])
    assert payload["candidates"][0]["candidate_evidence"] == first
    assert payload["candidates"][1]["candidate_evidence"] == second
    assert events[0]["comparison_context"]["candidate_id_map"] == {"c0": "oral", "c1": "code"}
    assert events[0]["parsed_result"]["decisions"][1]["decision"] == "SAME"
    assert [e["comparison_context"].get("candidate_id") for e in events[1:]] == ["oral", "code"]
    assert events[1]["parsed_result"] == verification()
    assert events[2]["parsed_result"]["same_response_form"] is False
    assert result["oral"].decision == "SAME" and result["code"].decision == "RELATED"
    guard_payload = json.loads(requests[2]["messages"][1]["content"])
    assert guard_payload["candidate_evidence"] == second and guard_payload["incoming_evidence"] == incoming
    assert not hasattr(judge, "last_result")


def test_batch_rejects_unknown_evidence_candidate_before_request():
    client, requests = client_for()
    with pytest.raises(ValueError, match="unknown judge candidate"):
        v3(client).judge_many("解释队列", [("a", "解释队列")], candidate_evidence={"wrong-id": None})
    assert requests == []


def test_collector_persists_source_and_first_stage_even_when_optional_guard_rejects(monkeypatch, tmp_path):
    from eval.collect import ProviderCollector
    from interview_intelligence.config import Settings
    question = "说明调度流程"
    for path, header in (("a.md", "# 调度器甲"), ("b.md", "# 调度器乙")):
        (tmp_path / path).write_text(header + "\n" + question + "\n独立追加问题", encoding="utf-8")
    endpoints = []
    for path in ("a.md", "b.md"):
        raw = (tmp_path / path).read_bytes()
        text = decode_source(raw)
        start = text.index(question)
        endpoints.append({"text": question, "raw_question": question, "source_path": path,
            "source_hash": hashlib.sha256(raw).hexdigest(),
            "source_spans": [{"start_char": start, "end_char": start + len(question), "quote": question}]})
    client, requests = client_for(decision(), verification(same_subject=False))
    monkeypatch.setattr("eval.collect.capped_client", lambda settings: client)
    settings = Settings(model_api_key="test", model_base_url="http://fixture.invalid/v1", judge_model="alias")
    collector = ProviderCollector("dedup", tmp_path, settings, None, None, dedup_prompt_version="dedup_judge_v3",
                                  dedup_source_context=True, dedup_verify_equivalence=True)
    calls = []
    observed = collector({"left": endpoints[0], "right": endpoints[1]}, calls)
    assert observed["result"]["label"] == "RELATED"
    assert observed["dedup_trace"]["first_stage"]["decision"] == "SAME"
    assert observed["dedup_trace"]["guards"][0]["result"]["same_subject"] is False
    assert observed["dedup_trace"]["final"] == observed["result"]
    assert observed["source_evidence"]["left"]["source_hash"] != observed["source_evidence"]["right"]["source_hash"]
    assert observed["provider_fingerprint"]["source_context_version"] == VERSION
    assert len(requests) == len(calls) == 2


def test_collector_context_corruption_fails_without_spending_a_model_call(monkeypatch, tmp_path):
    from eval.collect import ProviderCollector
    from interview_intelligence.config import Settings
    client, requests = client_for()
    monkeypatch.setattr("eval.collect.capped_client", lambda settings: client)
    (tmp_path / "source.md").write_text("原文题干", encoding="utf-8")
    endpoint = {"text": "原文题干", "raw_question": "原文题干", "source_path": "source.md",
                "source_hash": "0" * 64, "source_spans": [{"start_char": 0, "end_char": 4, "quote": "原文题干"}]}
    collector = ProviderCollector("dedup", tmp_path, Settings(model_api_key="test", model_base_url="http://fixture.invalid/v1"),
                                  None, None, dedup_prompt_version="dedup_judge_v3", dedup_source_context=True)
    with pytest.raises(ValueError, match="hash mismatch"):
        collector({"left": endpoint, "right": endpoint}, [])
    assert requests == []
