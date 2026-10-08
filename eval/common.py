"""Credential-free primitives shared by collection and scoring."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SECTIONS = ("extraction", "task_labels", "dedup", "retrieval", "routing", "sql")
PIPELINES = ("BM25", "DENSE", "HYBRID", "HYBRID_RERANK")
MISSING = object()


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path):
    def invalid(value):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            if not isinstance(row, dict):
                raise ValueError("JSONL rows must be objects")
            rows.append(row)
    return rows


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")


def inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("dataset path escapes its directory")
    return path


def provenance() -> dict:
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                                         stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    paths = [p for folder in ("eval", "src", "config", "prompts", "services/pi-agent", "services/jev-gateway", "evals/project_quality", "evals/agent_gold")
             for p in (root / folder).rglob("*") if p.is_file()
             and p.suffix in {".py", ".yaml", ".md", ".json", ".js", ".mjs"}
             and not {"__pycache__", "results", "reports"}.intersection(p.relative_to(root).parts)]
    hashes = {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(paths)}
    return {"git_commit": commit, "source_sha256": digest(hashes), "file_hashes": hashes,
            "annotation_guide_sha256": digest((root / "docs/annotation-guide.md").read_bytes()) if (root / "docs/annotation-guide.md").is_file() else None,
            "created_at": datetime.now(timezone.utc).isoformat()}


def lookup(value, path: str):
    parts = path.split(".") if path else []
    def visit(current, remaining):
        if not remaining:
            return current
        head, *tail = remaining
        if head == "*":
            if not isinstance(current, list):
                return MISSING
            values = [visit(item, tail) for item in current]
            return MISSING if any(item is MISSING for item in values) else values
        if isinstance(current, dict) and head in current:
            return visit(current[head], tail)
        if isinstance(current, list) and head.isdigit() and int(head) < len(current):
            return visit(current[int(head)], tail)
        return MISSING
    return visit(value, parts)


def finite_number(value):
    return type(value) in {int, float} and math.isfinite(value)
