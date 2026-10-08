"""Read-only verification, executed in each production container via stdin."""
import hashlib
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select

import interview_intelligence
from interview_intelligence.agent.task_annotation import annotate_tasks
from interview_intelligence.config import load_settings
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.domain.models import AgentConversation, CorpusState, ReviewEvent, UserQuestionState, create_database


root = Path("/app/data/reports/quality-refinement-20261006")
expected = json.loads((root / "deployment.expected.json").read_text(encoding="utf-8"))
role = sys.argv[1]
package = Path(interview_intelligence.__file__).parent
assert "site-packages" in str(package), "INSTALLED_PACKAGE_REQUIRED"
hashes = {name: hashlib.sha256((package / relative).read_bytes()).hexdigest()
          for name, relative in expected["package_paths"].items()}
assert hashes == expected["sha256"], "DEPLOYED_CODE_HASH_MISMATCH"
settings = load_settings()
defaults = {"query": settings.query_prompt_version, "extraction": settings.extraction_prompt_version,
            "thinking": settings.extraction_thinking_mode, "task_labels": inspect.signature(annotate_tasks).parameters["prompt_version"].default,
            "task_source_context": inspect.signature(annotate_tasks).parameters["source_context"].default,
            "fast_decision": settings.jev_decision_enabled}
assert defaults == expected["production_defaults"], "EXPERIMENTAL_DEFAULT_ENABLED"
database = create_database(settings.database_url, create_tables=False)
actors = []
with database.session() as session:
    state = session.get(CorpusState, 1)
    snapshot = {"corpus_revision": state.current_revision, "indexed_revision": state.indexed_revision,
                "task_annotation_revision": state.task_annotation_revision}
    assert snapshot == {k: expected["snapshot"][k] for k in snapshot}, "PUBLISHED_SNAPSHOT_CHANGED"
    for user in ("eval-query-refinement-20261006-v7", "eval-query-reserved-first-20261006-v7"):
        row = {"user_id": user}
        for key, table in (("state_count", UserQuestionState), ("event_count", ReviewEvent), ("conversation_count", AgentConversation)):
            row[key] = session.scalar(select(func.count()).select_from(table).where(table.user_id == user))
        assert row["state_count"] == row["event_count"] == 0 and row["conversation_count"] == 50, "ACTOR_STATE_CHANGED"
        actors.append(row)
assert FilterSpec(language="Java").language == "JAVA"
health, language_sql = None, None
if role == "api":
    with httpx.Client(trust_env=False, timeout=20) as client:
        response = client.get("http://127.0.0.1:8000/api/health")
        response.raise_for_status()
        health = response.json()["data"]
        assert health["index"] == "ready" and not health["jev_decision_enabled"]
        lower = client.get("http://127.0.0.1:8000/api/questions/list", params={"language": "Java", "page_size": 5, "top_n": 5})
        upper = client.get("http://127.0.0.1:8000/api/questions/list", params={"language": "JAVA", "page_size": 5, "top_n": 5})
        lower.raise_for_status()
        upper.raise_for_status()
        ids = lambda response: [(r["canonical_question_id"], r["occurrence_count"]) for r in response.json()["data"]]
        assert ids(lower) == ids(upper), "LANGUAGE_SCOPE_MISMATCH"
        language_sql = {"status": "PASSED", "rows": ids(lower), "scope": "Read-only Java/JAVA normalization; no model calls."}
result = {"role": role, "status": "PASSED", "verified_at": datetime.now(timezone.utc).isoformat(),
          "package_path": str(package), "production_defaults": defaults, "snapshot": snapshot, "actors": actors,
          "deployed_file_sha256": hashes, "health": health, "language_sql_smoke": language_sql,
          "scope": "Common support deployed with existing production prompt defaults. Candidate evaluation outputs were not published."}
(root / f"deployment.{role}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"role": role, "status": result["status"], "defaults": defaults, "snapshot": snapshot}, ensure_ascii=False))
