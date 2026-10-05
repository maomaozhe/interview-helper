"""Native Pi write smoke on the explicitly isolated verification API actor.

Run INSIDE verification-api: requests share the real cross-container gate.
Refuses to run as the ordinary development user. Retains test actor audit data.
"""
import json,time
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select,func
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database,ReviewEvent,ModelCall,ToolInvocation,AgentTurn

settings=load_settings()
assert settings.local_user_id=="query-spec-verification-20261005","isolated actor required"
db=create_database(settings.database_url,create_tables=False)
c=httpx.Client(base_url="http://127.0.0.1:8000",trust_env=False,timeout=70)
def count(user):
    with db.session() as s:return s.scalar(select(func.count()).select_from(ReviewEvent).where(ReviewEvent.user_id==user))
before_local,before_test=count("local"),count(settings.local_user_id)
page=c.post("/api/questions/list",json={"request_id":str(uuid4()),"list_request":{
    "coding_focus":"ALGORITHM","page_size":3,"top_n":3}})
page.raise_for_status();p=page.json();ids=[r["canonical_question_id"] for r in p["data"]]
body={"request_id":str(uuid4()),"message":"把当前页这三道题全部标记为已掌握，不修改其他题目",
      "conversation_id":p["meta"]["conversation_id"],"expected_version":p["meta"]["conversation_version"]}
started=time.perf_counter()
r=c.post("/api/agent/chat",json=body);r.raise_for_status();result=r.json()
assert result["data"]["intent"]=="RECORD_REVIEW",result["data"]["intent"]
assert result["data"]["planning"]["provider"]=="pi"
assert sorted(x["canonical_question_id"] for x in result["data"]["facts"]["review"]["items"])==sorted(ids)
assert count(settings.local_user_id)-before_test==3 and count("local")==before_local
replay=c.post("/api/agent/chat",json=body);replay.raise_for_status();assert replay.json()==result
assert count(settings.local_user_id)-before_test==3
with db.session() as s:
    turn=s.scalar(select(AgentTurn).where(AgentTurn.request_id==body["request_id"],AgentTurn.user_id==settings.local_user_id))
    model_calls=list(s.scalars(select(ModelCall).where(ModelCall.query_run_id==turn.id)))
    actions=list(s.scalars(select(ToolInvocation).where(ToolInvocation.run_id==turn.id)))
report={"actor":settings.local_user_id,"checks":{"native_pi_write":True,"exact_current_page":True,
    "replay_no_duplicate_events":True,"ordinary_actor_unchanged":True},"elapsed_ms":round((time.perf_counter()-started)*1000,2),
    "request_id":body["request_id"],"run_id":turn.id,"action_ids":[a.id for a in actions],
    "written_ids":ids,"model_calls":[{"operation":m.operation_type,"provider_ms":m.provider_ms,
        "queue_ms":m.queue_ms,"interval_ms":m.interval_ms,"ttft_ms":m.ttft_ms,
        "token_budget_charge":m.token_budget_charge} for m in model_calls]}
Path("data/reports/query-native-write-20261005.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False,indent=2))
