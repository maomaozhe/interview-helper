"""Compare installed package files with a saved workspace hash manifest."""
import argparse
import hashlib
import json
from pathlib import Path

FILES = ["agent/query_contract.py", "agent/query_service.py", "agent/harness.py", "agent/feedback_memory.py",
         "agent/history.py", "api.py", "config.py", "web.py", "search/elasticsearch.py", "search/reranker.py",
         "search/service.py", "web/assets/app.js", "web/assets/core.js", "web/assets/workspace.css", "web/index.html",
         "resources/prompts/query_agent_v10.md"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.workspace:
        root = args.workspace / "src/interview_intelligence"
        hashes = {name: hashlib.sha256((args.workspace / name.replace("resources/", "")
            if name.startswith("resources/") else root / name).read_bytes()).hexdigest() for name in FILES}
        args.manifest.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    else:
        import interview_intelligence
        from interview_intelligence.config import Settings
        from interview_intelligence.search.reranker import LLMReranker
        root = Path(interview_intelligence.__file__).parent
        expected = json.loads(args.manifest.read_text(encoding="utf-8"))
        actual = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in expected}
        report = {"matched": actual == expected, "mismatches": [name for name in actual if actual[name] != expected[name]],
                  "query_version": Settings().query_prompt_version, "reranker_version": LLMReranker.version,
                  "query_deadline_seconds": Settings().query_deadline_seconds,
                  "installed_sha256": actual}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "installed_sha256"}))
        assert report["matched"]


if __name__ == "__main__":
    main()
