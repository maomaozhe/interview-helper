"""Candidate provider control flow and experiment provenance, without model calls."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from interview_intelligence.dedup.provider import (
    AnswerBoundaryVerification, DEFAULT_JUDGE_PROMPT, OpenAICompatibleJudge,
)
from interview_intelligence.resources import resource_path


def fake_client(responses):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        payload = responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(prompt_tokens=19, completion_tokens=7), model="observed-model-revision",
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), requests


def relation(decision="SAME"):
    return {"decision": decision, "reason_code": "fixture_answer_boundary", "confidence": .93}


def verification(**differences):
    result = {name: True for name in AnswerBoundaryVerification.model_fields if name != "reason_code"}
    result.update(reason_code="fixture_scope_check", **differences)
    return result


def legacy_verification():
    return {"same_subject": True, "same_operation": True, "same_answer_scope": True,
            "compatible_constraints": True, "referents_resolved": True, "reason_code": "legacy_scope"}


def candidate(client, **kwargs):
    return OpenAICompatibleJudge(client=client, model="requested-model-alias",
        prompt_path=resource_path("prompts/dedup_judge_v2.md"), max_attempts=1, **kwargs)


def test_default_profile_keeps_legacy_prompt_and_disabled_guard():
    client, requests = fake_client([relation()])
    judge = OpenAICompatibleJudge(client=client, model="fixture")
    assert judge.judge("介绍事件循环", "解释事件循环").decision == "SAME"
    assert judge.version == "dedup_judge_v6_bounded"
    assert len(requests) == 1
    assert requests[0]["messages"][0]["content"] == DEFAULT_JUDGE_PROMPT.read_text(encoding="utf-8")
    assert judge.collection_fingerprint()["verify_equivalence"] is False


@pytest.mark.parametrize("difference", [
    "same_subject", "same_operation", "same_answer_scope", "same_response_form",
    "same_key_constraints", "same_failure_scope", "referents_resolved", "complete_conditions_available",
])
def test_candidate_guard_blocks_each_explicit_boundary_difference(difference):
    client, requests = fake_client([relation(), verification(**{difference: False})])
    events = []
    judge = candidate(client, verify_equivalence=True, on_call=events.append)
    result = judge.judge("解释任务调度器的异常处理", "给出任务调度器的完整代码")
    assert result.decision == "RELATED" and result.confidence <= .5
    assert result.reason_code == "equivalence_not_proven_fixture_scope_check"
    assert len(requests) == 2
    assert requests[-1]["response_format"]["json_schema"]["name"] == "dedup_answer_boundary_verification_v3"
    assert events[-1]["call_stage"] == "equivalence_verification"


def test_candidate_preserves_same_when_all_answer_boundaries_match():
    client, requests = fake_client([relation(), verification()])
    judge = candidate(client, verify_equivalence=True)
    result = judge.judge("解释事件队列的调度过程", "说明事件队列如何被调度")
    assert result.decision == "SAME" and result.confidence == .93
    assert len(requests) == 2
    assert judge.version == "dedup_judge_v9_verified_answer_boundary"


@pytest.mark.parametrize("label", ["RELATED", "DIFFERENT"])
def test_candidate_does_not_spend_a_guard_call_on_non_same(label):
    client, requests = fake_client([relation(label)])
    judge = candidate(client, verify_equivalence=True)
    assert judge.judge("事件队列如何调度", "进程启动如何分配虚拟内存").decision == label
    assert len(requests) == 1


def test_candidate_guard_can_be_disabled_for_prompt_only_ablation():
    client, requests = fake_client([relation()])
    judge = candidate(client, verify_equivalence=False)
    assert judge.judge("事件循环", "事件循环").decision == "SAME"
    assert len(requests) == 1
    assert judge.version == "dedup_judge_v9_answer_boundary"
    assert judge.collection_fingerprint()["guard_prompt_sha256"] is None


def test_batch_keeps_candidate_id_coverage_and_checks_only_same_items():
    decisions = [{"candidate_id": "c0", **relation()},
                 {"candidate_id": "c1", **relation("RELATED")},
                 {"candidate_id": "c2", **relation()}]
    client, requests = fake_client([{"decisions": decisions}, verification(same_response_form=False), verification()])
    events = []
    judge = candidate(client, verify_equivalence=True, on_call=events.append)
    result = judge.judge_many("解释任务调度机制", [
        ("implementation", "手写任务调度代码"), ("condition", "调度器宕机后恢复"),
        ("synonym", "说明任务如何调度"),
    ])
    assert set(result) == {"implementation", "condition", "synonym"}
    assert result["implementation"].decision == "RELATED"
    assert result["condition"].decision == "RELATED"
    assert result["synonym"].decision == "SAME"
    assert len(requests) == 3
    assert [event["call_stage"] for event in events] == [
        "relation_judgement", "equivalence_verification", "equivalence_verification",
    ]


def test_prompt_and_guard_fingerprints_record_actual_request_and_model_revision():
    client, requests = fake_client([relation(), verification()])
    events = []
    judge = candidate(client, verify_equivalence=True, on_call=events.append)
    judge.judge("事件队列", "事件队列")
    fingerprint = judge.collection_fingerprint()
    assert fingerprint["model"] == "requested-model-alias"
    assert fingerprint["prompt_version"] == "dedup_judge_v2"
    assert fingerprint["prompt_sha256"] != OpenAICompatibleJudge(client=client, model="fixture").prompt_hash
    assert fingerprint["verification_schema"] == "dedup_answer_boundary_verification_v3"
    for event, request in zip(events, requests):
        effective = hashlib.sha256(request["messages"][0]["content"].encode("utf-8")).hexdigest()
        assert event["effective_prompt_sha256"] == effective
        assert event["prompt_sha256"] == fingerprint["prompt_sha256"]
        assert event["model"] == "requested-model-alias"
        assert event["model_revision"] == "observed-model-revision"
    assert events[-1]["effective_prompt_sha256"] == fingerprint["guard_prompt_sha256"]


def test_guard_requires_new_axes_instead_of_accepting_old_five_boolean_response():
    old = {"same_subject": True, "same_operation": True, "same_answer_scope": True,
           "compatible_constraints": True, "referents_resolved": True, "reason_code": "old_schema"}
    client, _ = fake_client([relation(), old])
    events = []
    judge = candidate(client, verify_equivalence=True, on_call=events.append)
    with pytest.raises(ValidationError):
        judge.judge("事件队列", "事件队列")
    assert events[-1]["status"] == "FAILED"
    assert events[-1]["schema_name"] == "dedup_answer_boundary_verification_v3"


@pytest.mark.parametrize("prompt,override,configured,guarded", [
    (None, None, False, False),
    (None, None, True, True),
    ("dedup_judge_v1", False, True, False),
    ("dedup_judge_v1", True, False, True),
    ("dedup_judge_v2", False, True, False),
    ("dedup_judge_v2", True, False, True),
])
def test_provider_collector_routes_explicit_dedup_experiment_options(monkeypatch, tmp_path, prompt, override, configured, guarded):
    from eval.collect import ProviderCollector
    from interview_intelligence.config import Settings
    check = verification() if prompt == "dedup_judge_v2" else legacy_verification()
    payloads = [relation()] + ([check] if guarded else [])
    client, requests = fake_client(payloads)
    monkeypatch.setattr("eval.collect.capped_client", lambda settings: client)
    settings = Settings(model_api_key="test", model_base_url="http://fixture.invalid/v1",
                        judge_model="requested-model-alias", dedup_verify_equivalence=configured)
    collector = ProviderCollector("dedup", tmp_path, settings, None, None,
                                  dedup_prompt_version=prompt, dedup_verify_equivalence=override)
    calls = []
    observation = collector({"left": {"text": "事件队列如何调度"},
                             "right": {"text": "解释事件队列调度机制"}}, calls)
    assert observation["result"]["label"] == "SAME"
    assert observation["provider_fingerprint"]["verify_equivalence"] is guarded
    assert observation["provider_fingerprint"]["prompt_version"] == (prompt or "dedup_judge_v1")
    assert len(requests) == (2 if guarded else 1)
    assert calls[0]["model_revision"] == "observed-model-revision"


def test_collect_cli_rejects_dedup_override_for_another_section_before_reading_data(monkeypatch, tmp_path):
    from eval.collect import main
    from eval.validate_gold import GoldValidationError
    monkeypatch.setattr("sys.argv", ["eval.collect", "--dataset", str(tmp_path / "missing"),
        "--section", "sql", "--adapter", "sql", "--output", str(tmp_path / "unused.jsonl"),
        "--dedup-prompt-version", "dedup_judge_v2"])
    with pytest.raises(GoldValidationError, match="dedup override requires provider dedup"):
        main()


def test_collect_cli_exposes_both_guard_ablation_flags(monkeypatch, capsys):
    from eval.collect import main
    monkeypatch.setattr("sys.argv", ["eval.collect", "--help"])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "--dedup-prompt-version" in output
    assert "--dedup-verify-equivalence" in output
    assert "--no-dedup-verify-equivalence" in output


def test_actual_resolver_keeps_zero_confidence_after_candidate_batch_and_guard():
    from interview_intelligence.dedup.service import DedupService
    from interview_intelligence.domain.models import CanonicalQuestion, create_database
    database = create_database("sqlite:///:memory:")
    with database.session() as session:
        session.add(CanonicalQuestion(id="existing", canonical_text="说明事件队列如何调度",
                    primary_topic_id="other.open_question", taxonomy_version="v1", question_type="KNOWLEDGE"))
        session.commit()
    first = {"candidate_id": "c0", **relation(), "confidence": 0.0}
    client, requests = fake_client([{"decisions": [first]}, verification()])
    judge = candidate(client, verify_equivalence=True)
    encoder = SimpleNamespace(version="fixture-vector", embed=lambda text: [1.0, 0.0])
    resolver = DedupService(encoder=encoder, judge=judge, candidate_limit=5, candidate_backend="exact")
    with database.session() as session:
        result = resolver.resolve(session, "解释事件队列的调度过程", "other.open_question", "KNOWLEDGE")
        assert result.decision == "SAME" and result.canonical.id == "existing"
        assert result.confidence == 0.0
        assert result.evidence["judgements"][0]["confidence"] == 0.0
        assert result.candidate_ids == ["existing"]
        session.rollback()
    assert len(requests) == 2
    assert requests[0]["response_format"]["json_schema"]["name"] == "dedup_batch_v3"


def test_actual_resolver_retains_default_for_a_judge_with_no_confidence():
    from interview_intelligence.dedup.service import DedupService
    from interview_intelligence.domain.models import CanonicalQuestion, create_database
    database = create_database("sqlite:///:memory:")
    with database.session() as session:
        session.add(CanonicalQuestion(id="existing", canonical_text="事件队列调度",
                    primary_topic_id="other.open_question", taxonomy_version="v1", question_type="KNOWLEDGE"))
        session.commit()
    encoder = SimpleNamespace(version="fixture-vector", embed=lambda text: [1.0, 0.0])
    judge = SimpleNamespace(version="fixture-string-judge", judge=lambda incoming, candidate: "SAME")
    resolver = DedupService(encoder=encoder, judge=judge, candidate_limit=5, candidate_backend="exact")
    with database.session() as session:
        result = resolver.resolve(session, "解释事件队列调度", "other.open_question", "KNOWLEDGE")
        assert result.decision == "SAME" and result.confidence == 1.0
        assert result.evidence["judgements"][0]["confidence"] is None
        session.rollback()
