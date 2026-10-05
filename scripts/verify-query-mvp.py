"""Read-only live query smoke; stores no credentials and never records review state.

Run in the API container so model calls use the shared provider gate:
  docker compose exec -T api python - < scripts/verify-query-mvp.py
"""
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database, ModelCall


database = create_database(load_settings().database_url, create_tables=False)
client = httpx.Client(base_url="http://127.0.0.1:8000", trust_env=False, timeout=70)
report = {"checks": {}, "requests": []}


def call(path, body=None, params=None):
    started = time.perf_counter()
    response = client.post(path, json=body) if body else client.get(path, params=params)
    response.raise_for_status()
    result = response.json()
    meta = result.get("meta", {})
    report["requests"].append({"path": path, "message": body.get("message") if body else None,
        "elapsed_ms": round((time.perf_counter()-started)*1000, 2),
        "request_id": meta.get("request_id"), "timings": meta.get("timings"),
        "plan": meta.get("planning", {}).get("spec"), "pagination": {
            k:v for k,v in meta.get("pagination", {}).items() if k != "next_cursor"}})
    return result


def query(message, previous=None, *, chat=False, pipeline="HYBRID"):
    body = {"message": message, "request_id": str(uuid4())}
    if not chat:
        body.update(page_size=40, pipeline=pipeline)
    if previous:
        body.update(conversation_id=previous["meta"]["conversation_id"],
                    expected_version=previous["meta"]["conversation_version"])
    result = call("/api/agent/chat" if chat else "/api/questions/query", body)
    if chat:
        facts = result["data"]["facts"]
        facts["meta"].update(conversation_id=result["data"]["conversation_id"],
            conversation_version=result["data"]["conversation_version"], planning=result["data"]["planning"])
        result = facts
    return result, body


def ids_counts(result):
    return [(r["canonical_question_id"], r["occurrence_count"]) for r in result["data"]]


with database.session() as session:
    # Independent SQL statement, including the same active-build eligibility.
    truth = list(session.execute(text("""
        SELECT q.canonical_question_id, count(*)
        FROM question_occurrence q
        JOIN occurrence_task_annotation a ON a.occurrence_id=q.id
        JOIN interview i ON i.id=q.interview_id
        JOIN document_build b ON b.id=i.build_id
        JOIN source_revision r ON r.id=b.source_revision_id
        JOIN source_document d ON d.id=r.source_document_id
        WHERE d.active_build_id=b.id AND b.decision='INCLUDED'
          AND i.analytics_eligible=true AND a.coding_focus IN ('ALGORITHM','MIXED')
          AND a.response_form<>'UNKNOWN' AND a.coding_focus<>'UNKNOWN'
        GROUP BY q.canonical_question_id ORDER BY count(*) DESC, q.canonical_question_id
    """)))
truth = [(r[0], r[1]) for r in truth]

direct = call("/api/questions/list", params={"coding_focus":"ALGORITHM", "top_n":40, "page_size":40})
assert ids_counts(direct) == truth[:40] and len(direct["data"]) == 40
assert direct["meta"]["task_coverage"]["pending"] == 0
report["checks"]["sql_top40_matches_independent_sql"] = True
report["task_coverage"] = direct["meta"]["task_coverage"]

natural, natural_body = query("前40个频率最高的算法题")
assert ids_counts(natural) == truth[:40] and natural["meta"]["planning"]["provider"] == "pi"
with database.session() as session:
    before = session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == natural_body["request_id"]))
assert call("/api/questions/query", natural_body) == natural
with database.session() as session:
    assert session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id == natural_body["request_id"])) == before
report["checks"]["natural_top40_and_idempotency"] = True

handcode, _ = query("手撕代码有哪些题目", natural)
filters = handcode["meta"]["applied_filters"]
assert filters["coding_focus"] == "ENGINEERING" and filters["response_form"] == "CODE"
assert handcode["meta"]["planning"]["spec"]["top_n"] is None
engineering = call("/api/questions/list", params={"coding_focus":"ENGINEERING", "response_form":"CODE", "page_size":40})
assert ids_counts(handcode) == ids_counts(engineering)
report["engineering_count"] = engineering["meta"]["pagination"]["total"]
report["engineering_titles"] = [r["canonical_text"] for r in engineering["data"]]
second_round, _ = query("只看二面", handcode)
assert second_round["meta"]["applied_filters"]["round"] == "SECOND"
assert second_round["meta"]["applied_filters"]["coding_focus"] == "ENGINEERING"
assert second_round["meta"]["applied_filters"]["response_form"] == "CODE"
report["checks"]["handcode_and_followup_scope"] = True

first_body = {"list_request":{"coding_focus":"ALGORITHM", "page_size":20}, "request_id":str(uuid4())}
first = call("/api/questions/list", first_body)
next_body = {"list_request":{**first_body["list_request"], "cursor":first["meta"]["pagination"]["next_cursor"]},
    "conversation_id":first["meta"]["conversation_id"], "expected_version":first["meta"]["conversation_version"], "request_id":str(uuid4())}
second = call("/api/questions/list", next_body)
assert ids_counts(first) + ids_counts(second) == truth[:40]
with database.session() as session:
    assert session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.request_id.in_([first_body["request_id"],next_body["request_id"]]))) == 0
details, _ = query("查看第一题来源", second, chat=True)
assert details["data"][0]["canonical_question_id"] == second["data"][0]["canonical_question_id"]
third, _ = query("下一页", details, chat=True)
assert ids_counts(third) == truth[40:60]
report["checks"]["sql_pages_and_current_page_reference"] = True

semantic, _ = query("找与并发请求重复回源要合并处理语义相似的面试题", pipeline="HYBRID")
assert semantic["meta"]["route"] == "SEARCH"
report["semantic"] = {k:semantic["meta"].get(k) for k in ("requested_pipeline", "executed_pipeline", "degraded")}
report["checks"]["hybrid_semantic_search"] = True
report["algorithm_count"] = len(truth)
Path("data/reports").mkdir(parents=True, exist_ok=True)
Path("data/reports/query-mvp-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
