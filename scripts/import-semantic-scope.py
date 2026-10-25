"""Validate a complete reviewed semantic scope; publish only with explicit --apply."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview_intelligence.analytics.semantic_scope import SemanticScopeIncomplete
from interview_intelligence.analytics.semantic_scope_import import import_semantic_scope
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database


def save_immutable(path, value):
    """An existing audit file is reusable only when it has identical content."""
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError("SEMANTIC_SCOPE_IMPORT_AUDIT_FILE_CONFLICT") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="Source-reviewed semantic_scope_import_v1 JSON")
    parser.add_argument("--apply", action="store_true", help="Atomically publish after complete validation")
    parser.add_argument("--output-dir", type=Path, default=Path("data/reports/semantic-scope-import"))
    args = parser.parse_args(argv)
    settings = load_settings()
    artifact = json.loads(args.artifact.read_text(encoding="utf-8-sig"))
    digest = artifact.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("SEMANTIC_SCOPE_IMPORT_DIGEST_MISMATCH")
    output = args.output_dir.resolve() / digest
    snapshots = settings.snapshot_root.resolve()
    def source_reader(revision):
        if not re.fullmatch(r"[0-9a-f]{64}", revision.raw_file_hash):
            raise ValueError("SEMANTIC_SCOPE_SOURCE_HASH_INVALID")
        path = (snapshots / f"{revision.raw_file_hash}.md").resolve()
        if not path.is_relative_to(snapshots):
            raise ValueError("SEMANTIC_SCOPE_SOURCE_OUTSIDE_ROOT")
        return path.read_bytes()
    # Neither dry-run nor apply silently creates or upgrades a database schema.
    database = create_database(settings.database_url, create_tables=False)
    try:
        def prepared(receipt):
            save_immutable(output / "manifest.json", artifact)
            rollback = {"artifact_sha256": digest, "status": "PREPARED",
                "before_annotation_revision": receipt["before_annotation_revision"],
                "after_annotation_revision": receipt["after_annotation_revision"],
                "rollback_evidence": receipt["rollback_evidence"]}
            save_immutable(output / "rollback-evidence.json", rollback)
            save_immutable(output / "prepared-receipt.json", receipt)
        receipt = import_semantic_scope(database, artifact, source_reader=source_reader,
            apply=args.apply, before_commit=prepared if args.apply else None)
        save_immutable(output / "manifest.json", artifact)
        revision = receipt["before_annotation_revision"]
        filename = f"{receipt['status'].lower()}-receipt-{revision}.json"
        save_immutable(output / filename, receipt)
        print(json.dumps({key: value for key, value in receipt.items() if key != "rollback_evidence"},
                         ensure_ascii=False, allow_nan=False))
        return 0
    finally:
        database.engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SemanticScopeIncomplete as error:
        print(json.dumps({"error": str(error), "coverage": error.coverage}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    except Exception as error:
        # Diagnostics preserve safe protocol codes without DB URLs/source paths.
        code = str(error) if re.fullmatch(r"SEMANTIC_[A-Z0-9_]+", str(error)) else type(error).__name__
        print(json.dumps({"error": code}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
