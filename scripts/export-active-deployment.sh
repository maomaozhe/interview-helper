#!/usr/bin/env bash
# Export the running, installed application, not unreviewed working-tree changes.
set -euo pipefail
umask 077
OUTPUT_DIR="${1:?Provide a fresh output directory}"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"
[[ ! -e "$OUTPUT_DIR/app.tar.gz" ]] || { echo "Output already contains an app export" >&2; exit 1; }
api_id="$(docker compose ps -q api)"
pi_id="$(docker compose ps -q pi-agent)"
[[ -n "$api_id" && -n "$pi_id" ]]
docker exec "$api_id" tar --exclude='__pycache__' --exclude='*.pyc' -czf - -C /usr/local/lib/python3.12/site-packages interview_intelligence -C /app alembic.ini migrations config >"$OUTPUT_DIR/app.tar.gz"
docker exec "$pi_id" tar --exclude='__pycache__' -czf - -C /app . >"$OUTPUT_DIR/pi.tar.gz"
docker exec "$api_id" tar --exclude=interview_intelligence --exclude=__pycache__ --exclude='*.pyc' -czf - -C /usr/local/lib/python3.12/site-packages . >"$OUTPUT_DIR/python-deps.tar.gz"
docker exec "$api_id" python -m pip freeze | sed '/^interview-intelligence /d' >"$OUTPUT_DIR/requirements.active.txt"
docker exec "$api_id" python -c 'import json; from interview_intelligence.config import load_settings; print(json.dumps(load_settings().model_dump(mode="json")))' >"$OUTPUT_DIR/runtime.private.json"
docker compose exec -T postgres pg_dump -U interview -d interview_intelligence --format=custom --no-owner --no-acl >"$OUTPUT_DIR/database.dump"
python3 scripts/transfer-search-index.py export "$OUTPUT_DIR/search-index.jsonl.gz" --url http://127.0.0.1:9200
cp scripts/transfer-search-index.py "$OUTPUT_DIR/"
tar -czf "$OUTPUT_DIR/corpus-and-snapshots.tar.gz" md data/snapshots data/feedback
docker inspect --format '{{.Name}} {{.Image}}' "$api_id" "$pi_id" >"$OUTPUT_DIR/source-images.txt"
curl -fsS http://127.0.0.1:8000/api/health >"$OUTPUT_DIR/source-health.json"
(
    cd "$OUTPUT_DIR"
    sha256sum app.tar.gz pi.tar.gz python-deps.tar.gz requirements.active.txt corpus-and-snapshots.tar.gz database.dump search-index.jsonl.gz runtime.private.json transfer-search-index.py >SHA256SUMS
)
printf 'Active application, database, vectors, sources and feedback exported to %s\n' "$OUTPUT_DIR"
