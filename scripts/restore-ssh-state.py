"""Restore a fresh SSH deployment, verify corpus/vector integrity, then start it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen


def wait_http(url, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urlopen(url, timeout=3) as response:
                if response.status == 200:
                    return json.load(response)
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise TimeoutError(url)


def main(root):
    root = root.resolve()
    release = root / "release"
    source_health = json.loads((release / "source-health.json").read_text())["data"]
    os.chdir(root / "app")
    sys.path.insert(0, str(root / "app"))
    from interview_intelligence.config import load_settings
    import psycopg

    config = load_settings()
    pg_bin = root / "runtime/postgresql/bin"
    db_env = dict(os.environ, PGPASSFILE=str(root / "pgpass"))
    for _ in range(60):
        result = subprocess.run([str(pg_bin / "pg_isready"), "-h", "127.0.0.1", "-p", "15432"], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(2)
    else:
        raise TimeoutError("PostgreSQL readiness")
    wait_http("http://127.0.0.1:19200/_cluster/health")
    connection_args = ["-h", "127.0.0.1", "-p", "15432", "-U", "interview"]
    subprocess.run([str(pg_bin / "createdb"), *connection_args, "interview_intelligence"], env=db_env, check=True)
    subprocess.run([str(pg_bin / "pg_restore"), *connection_args, "--dbname=interview_intelligence",
                    "--no-owner", "--no-acl", "--single-transaction", "--exit-on-error",
                    str(release / "database.dump")], env=db_env, check=True)
    report = {"source_health": source_health}
    with psycopg.connect(config.database_url.replace("postgresql+psycopg:", "postgresql:")) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_revision, indexed_revision, task_annotation_revision FROM corpus_state WHERE id=1")
            revision, indexed, annotations = cursor.fetchone()
            assert revision == indexed == source_health["current_revision"]
            report.update(corpus_revision=revision, indexed_revision=indexed, task_annotation_revision=annotations)
            cursor.execute("SELECT count(*) FROM pipeline_run WHERE status IN ('QUEUED', 'RUNNING')")
            if cursor.fetchone()[0]:
                raise ValueError("Source backup contains unfinished ingestion; worker left stopped")
            cursor.execute("SELECT DISTINCT raw_file_hash FROM source_revision")
            hashes = [item[0] for item in cursor.fetchall()]
            for digest in hashes:
                path = root / "data/snapshots" / f"{digest}.md"
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("Missing or changed source snapshot")
            report["source_snapshots_verified"] = len(hashes)
            cursor.execute("""
                SELECT count(DISTINCT d.id), count(DISTINCT i.id), count(*), count(DISTINCT q.canonical_question_id)
                FROM question_occurrence q
                JOIN interview i ON i.id=q.interview_id
                JOIN document_build b ON b.id=i.build_id
                JOIN source_revision r ON r.id=b.source_revision_id
                JOIN source_document d ON d.id=r.source_document_id
                WHERE d.active_build_id=b.id AND b.decision='INCLUDED' AND i.analytics_eligible=true
            """)
            report["active_corpus"] = dict(zip(("documents", "interviews", "occurrences", "canonical_questions"), cursor.fetchone()))
    result = subprocess.run([sys.executable, str(release / "transfer-search-index.py"), "import",
                             str(release / "search-index.jsonl.gz"), "--url", "http://127.0.0.1:19200"],
                            check=True, text=True, capture_output=True)
    report["search_import"] = json.loads(result.stdout)
    assert report["search_import"]["imported"] == report["active_corpus"]["canonical_questions"]
    subprocess.run(["systemctl", "--user", "start", "interview-intelligence-api", "interview-intelligence-pi"], check=True)
    report["deployed_health"] = wait_http("http://127.0.0.1:18082/api/health")["data"]
    assert report["deployed_health"] == source_health
    report["pi_health"] = wait_http("http://127.0.0.1:18787/health")
    subprocess.run(["systemctl", "--user", "start", "interview-intelligence-worker", "interview-intelligence-web"], check=True)
    subprocess.run(["systemctl", "--user", "enable", *[f"interview-intelligence-{name}.service" for name in
                    ("postgres", "elasticsearch", "api", "pi", "worker", "web")]], check=True)
    (root / "deployment-receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-dir", type=Path, default=Path.home() / "services/interview-intelligence")
    main(parser.parse_args().service_dir)
