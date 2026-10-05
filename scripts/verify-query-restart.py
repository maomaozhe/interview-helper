"""Replay an earlier native write AFTER recreation of verification-api."""
import json
from pathlib import Path
import httpx
from sqlalchemy import select,func
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database,AgentTurn,ModelCall,ReviewEvent

s=load_settings()
assert s.local_user_id=="query-spec-verification-20261005"
db=create_database(s.database_url,create_tables=False)
path=Path("data/reports/query-native-write-20261005.json")
report=json.loads(path.read_text(encoding="utf-8"))
with db.session() as session:
    turn=session.get(AgentTurn,report["run_id"])
    body={k:v for k,v in turn.request_payload.items() if k in {
        "message","request_id","conversation_id","expected_version"}}
    result=turn.response
    model_before=session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.query_run_id==turn.id))
    reviews_before=session.scalar(select(func.count()).select_from(ReviewEvent).where(ReviewEvent.user_id==s.local_user_id))
with httpx.Client(trust_env=False,timeout=10) as client:
    response=client.post("http://127.0.0.1:8000/api/agent/chat",json=body)
    response.raise_for_status()
    assert response.json()["data"]==result
with db.session() as session:
    assert session.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.query_run_id==turn.id))==model_before
    assert session.scalar(select(func.count()).select_from(ReviewEvent).where(ReviewEvent.user_id==s.local_user_id))==reviews_before
report["checks"]["recreated_api_replay_no_model_or_duplicate_write"]=True
path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report["checks"],ensure_ascii=False))
