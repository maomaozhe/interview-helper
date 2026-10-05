"""Export the production decision schema with manually labelled Chinese cases."""
import json
from pathlib import Path
import sys
from interview_intelligence.agent.jev import JevPlanner
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import create_database

if __name__ == "__main__":
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", jev_provider="laya", jev_model="convaiinnovations/laya-multilingual")
    planner = JevPlanner(settings, create_database(settings.database_url), None)
    cases = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else "evals/query-routing/cases.json").read_text(encoding="utf-8"))
    for case in cases:
        context = {"message": case["message"], "today": "2026-10-04", "explicit_filters": {},
                   "default_page_size": 20, "session": case.get("session", {})}
        payload, _ = planner.payload(context)
        payload["model"] = "convaiinnovations/laya-multilingual"
        case["payload"] = payload
    Path(sys.argv[2] if len(sys.argv) > 2 else "data/decision-eval-payloads.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"cases": len(cases), "exported": True}))
