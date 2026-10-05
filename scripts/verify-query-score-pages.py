"""Read-only live PG keyset scores compared with the independent legacy formula."""
import json
from pathlib import Path
import httpx
from interview_intelligence.config import load_settings
from interview_intelligence.contracts import StatsRequest
from interview_intelligence.domain.models import create_database
from interview_intelligence.analytics.stats import query_question_stats

settings=load_settings();db=create_database(settings.database_url,create_tables=False)
checks={}
with httpx.Client(base_url="http://127.0.0.1:8000",trust_env=False,timeout=20) as client:
    for sort in ("importance","gap"):
        with db.session() as session:
            truth=query_question_stats(session,StatsRequest(sort=sort,limit=100),user_id=settings.local_user_id)["data"]
        params={"sort":sort,"top_n":100,"page_size":37}
        rows=[]
        while True:
            response=client.get("/api/questions/list",params=params);response.raise_for_status();p=response.json()
            rows.extend(p["data"])
            cursor=p["meta"]["pagination"]["next_cursor"]
            if not cursor:break
            params["cursor"]=cursor
        assert [r["key"] for r in rows]==[r["key"] for r in truth]
        assert len({r["key"] for r in rows})==100
        assert all(abs(a["importance_score"]-b["importance_score"])<1e-10 for a,b in zip(rows,truth))
        checks[sort+"_pg_keyset_exact_top100"]=True
Path("data/reports/query-score-pages-20261005.json").write_text(json.dumps(checks,indent=2),encoding="utf-8")
print(json.dumps(checks))
