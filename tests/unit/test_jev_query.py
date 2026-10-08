import time
import httpx
import pytest

from interview_intelligence.agent.jev import JevPlanner, parse_chinese_number, quantity_candidates
from interview_intelligence.agent.query_contract import QuerySpec, validate_model_plan
from interview_intelligence.agent.query_service import QueryRun
from interview_intelligence.config import Settings
from interview_intelligence.agent.query_contract import QueryRequest
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits
from interview_intelligence.domain.models import create_database


@pytest.mark.parametrize("text,value", [("40", 40), ("四十", 40), ("一百二十", 120), ("两百", 200)])
def test_literal_quantity_parsing(text, value):
    assert parse_chinese_number(text) == value


def test_jev_selects_count_span_not_year_or_leetcode_number(monkeypatch, tmp_path):
    message = "2026年面经里，除力扣40题外，前四十个算法题"
    settings = Settings(jev_api_key="test", jev_confidence_threshold=.85)
    planner = JevPlanner(settings, create_database("sqlite+pysqlite:///:memory:"),
                         ModelCallGate(tmp_path / "gate", minimum_interval_seconds=0))
    request = QueryRequest(message=message, request_id="test")
    run = QueryRun("run", request, "conversation", 0, {}, RequestLimits(time.monotonic() + 10), (0, 0, 0))
    context = {"message": message, "today": "2026-10-04", "explicit_filters": {}, "default_page_size": 20, "session": {}}
    payload, nums = planner.payload(context)
    assert [n["value"] for n in nums] == [2026, 40, 40]
    values = {k: "NONE" for k in payload["questions"]}
    values.update(action="LIST", sort="frequency", group_by="question", coding_focus="ALGORITHM", top_n="n2", time="NONE")
    response = {"model": "jev-1.13.0", "answers": {k: {"type": "choice", "choice": v, "confidence": .99}
                for k, v in values.items()}, "usage": {"input_tokens": 10, "output_tokens": 0}}
    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=response)), **kwargs))
    plan = planner.plan(run, context)
    assert plan.top_n == 40 and plan.page_size == 40
    assert plan.filters.coding_focus == "ALGORITHM" and plan.filters.question_type is None
    response["usage"]["options"] = {"top_n": {"distinct": 5, "total": 5}}
    assert planner.plan(run, context).top_n == 40
    response["usage"]["options"]["top_n"]["distinct"] = 4
    assert planner.plan(run, context) is None
    assert run.decision["fallback_reason"] == "input_truncated"
    response["usage"]["options"] = {}
    uncalibrated = JevPlanner(settings.model_copy(update={"jev_provider": "laya"}), planner.database, planner.gate)
    assert uncalibrated.plan(run, context) is None
    assert run.decision["fallback_reason"] == "domain_calibration_required"
    response["answers"]["top_n"]["confidence"] = .3
    assert planner.plan(run, context) is None


def test_semantic_candidates_cannot_claim_global_top_n():
    with pytest.raises(ValueError, match="no global top_n"):
        QuerySpec(action="SEARCH", search_query="OOM", top_n=40)


def test_fast_continuation_keeps_saved_page_instead_of_expanding_to_top_n(tmp_path):
    planner=JevPlanner(Settings(),None,None)
    values={k:"NONE" for k in ("company","topic_l1","round","response_form","coding_focus","time")}
    values.update(action="LIST",coding_focus="INHERIT",round="SECOND",sort="frequency",group_by="question",top_n="INHERIT")
    state={"filters":{"coding_focus":"ALGORITHM"},"sort":"frequency", "last_plan":{"top_n":37,"page_size":8}}
    plan=planner.decode(values,[],{"today":"2026-10-05","explicit_filters":{},"default_page_size":20},state)
    assert plan.top_n==37 and plan.page_size==8 and plan.filters.round=="SECOND"


def test_local_model_validation_failure_preserves_known_usage(monkeypatch,tmp_path):
    calls=[]
    planner=JevPlanner(Settings(jev_provider="local_qwen",jev_api_key="fixture"),
        create_database("sqlite+pysqlite:///:memory:"),ModelCallGate(tmp_path/"gate",minimum_interval_seconds=0),on_call=calls.append)
    run=QueryRun("run",QueryRequest(message="算法题有哪些",request_id="request"),"conversation",0,{},
        RequestLimits(time.monotonic()+10),(0,0,0))
    response={"error":"INVALID_ANSWER_FIELDS","protocol":"typed_json_v1","raw_answer":"{}",
        "usage":{"input_tokens":80,"output_tokens":50},"model_revision":"fixed-revision"}
    client_type=httpx.Client
    monkeypatch.setattr(httpx,"Client",lambda **kwargs:client_type(transport=httpx.MockTransport(
        lambda req:httpx.Response(400,json=response)),**kwargs))
    context={"message":"算法题有哪些","today":"2026-10-05","explicit_filters":{},"default_page_size":20,"session":{}}
    assert planner.plan(run,context) is None
    assert run.decision["provider_error_code"]=="INVALID_ANSWER_FIELDS"
    assert calls[0]["status"]=="FAILED" and calls[0]["input_tokens"]==80 and calls[0]["output_tokens"]==50
    assert calls[0]["token_budget_charge"]==130


def test_incomplete_model_scope_is_rejected_instead_of_silently_using_defaults():
    with pytest.raises(ValueError, match="QUERY_PLAN_INCOMPLETE"):
        validate_model_plan({"action": "LIST"})
    plan = QuerySpec(action="LIST", filters={"coding_focus":"ENGINEERING", "round":"SECOND"})
    assert validate_model_plan(plan.model_dump(mode="json")).filters.round == "SECOND"
    payload = plan.model_dump(mode="json")
    del payload["filters"]["round"]
    with pytest.raises(ValueError, match="filters.round"):
        validate_model_plan(payload)


def test_typed_probability_only_combines_equivalent_values(tmp_path):
    settings=Settings(jev_provider="local_qwen")
    planner=JevPlanner(settings,create_database("sqlite+pysqlite:///:memory:"),ModelCallGate(tmp_path/"gate",minimum_interval_seconds=0))
    payload={"questions":{"coding_focus":{"criteria":{"NONE":"none","INHERIT":"old","ALGORITHM":"algo"}}}}
    data={"answers":{"coding_focus":{"type":"choice","choice":"NONE","confidence":.4,
        "probabilities":{"NONE":.4,"INHERIT":.35,"ALGORITHM":.25}}}}
    values,confidences,raw=planner.decision_values(payload,data,{}, {},[])
    assert values["coding_focus"]=="NONE" and confidences["coding_focus"]==.75
    values,confidences,raw=planner.decision_values(payload,data,{}, {"filters":{"coding_focus":"ALGORITHM"}},[])
    assert values["coding_focus"]=="INHERIT" and confidences["coding_focus"]==.6
    data["answers"]["coding_focus"]["probabilities"]["NONE"]=.9
    with pytest.raises(ValueError,match="INVALID_DECISION_PROBABILITIES"):
        planner.decision_values(payload,data,{}, {},[])


def test_local_service_self_report_cannot_replace_host_calibration(tmp_path):
    import json
    import hashlib
    import inspect
    from pathlib import Path
    path=tmp_path/"calibration.json"
    planner=JevPlanner(Settings(jev_provider="local_qwen",jev_calibration_path=path),None,None)
    response={"calibrated":True,"model_revision":"revision-a","protocol":"typed_json_v1",
        "service_sha256":"a"*64,"grammar_sha256":"b"*64}
    assert planner._calibrated(response) is False
    artifact={"schema":"jev_domain_calibration_v1","status":"PASSED","provider":"local_qwen","model_revision":"revision-a",
        "decision_version":planner.version,"independent_calibration":True,"threshold":.85,
        "accepted_samples":20,"accepted_errors":0,"provider_protocol":"typed_json_v1",
        "service_sha256":"a"*64,"grammar_sha256":"b"*64,
        "decision_contract_sha256":hashlib.sha256(Path(inspect.getfile(JevPlanner)).read_bytes()).hexdigest()}
    path.write_text(json.dumps(artifact),encoding="utf-8")
    assert planner._calibrated(response) is True
    response["service_sha256"]="c"*64
    assert planner._calibrated(response) is False
    response["service_sha256"]="a"*64
    response["model_revision"]="revision-b"
    assert planner._calibrated(response) is False
