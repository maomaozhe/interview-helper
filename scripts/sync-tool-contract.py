"""Generate Pi tool and evidence declarations without runtime dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/interview_intelligence/agent/tool_definitions.py"
EVIDENCE_SOURCE = ROOT / "src/interview_intelligence/agent/evidence_projection.py"
TARGET = ROOT / "services/pi-agent/runtime.mjs"
BEGIN = "// BEGIN GENERATED TOOL CONTRACT (python scripts/sync-tool-contract.py)"
END = "// END GENERATED TOOL CONTRACT"
EVIDENCE_BEGIN = "// BEGIN GENERATED EVIDENCE PROJECTION (python scripts/sync-tool-contract.py)"
EVIDENCE_END = "// END GENERATED EVIDENCE PROJECTION"


def generated_section():
    definitions = runpy.run_path(str(SOURCE))
    # One generated record per tool keeps executable orchestration easy to scan.
    encode = lambda value: json.dumps(value, ensure_ascii=False)
    records = [f"    {encode(name)}: {encode(tool)}" for name, tool in definitions["TOOL_DEFINITIONS"].items()]
    return (BEGIN + "\nconst toolContract = {\n  common_required: " + encode(list(definitions["COMMON_REQUIRED"]))
            + ",\n  tools: {\n" + ",\n".join(records) + "\n  }\n};\n" + END)


def generated_evidence_section():
    projection = runpy.run_path(str(EVIDENCE_SOURCE))["EVIDENCE_PROJECTION"]
    records = [f"  {key}: {json.dumps(value, ensure_ascii=False)}" for key, value in projection.items()]
    return EVIDENCE_BEGIN + "\nconst evidenceProjection = {\n" + ",\n".join(records) + "\n};\n" + EVIDENCE_END


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if either committed Pi declaration drifts")
    args = parser.parse_args()
    source = TARGET.read_text(encoding="utf-8")
    expected = source
    for begin, end, generate in ((BEGIN, END, generated_section),
                                 (EVIDENCE_BEGIN, EVIDENCE_END, generated_evidence_section)):
        start, stop = expected.index(begin), expected.index(end) + len(end)
        expected = expected[:start] + generate() + expected[stop:]
    if args.check:
        if source != expected:
            parser.exit(1, "Pi declarations are stale; run python scripts/sync-tool-contract.py\n")
        print("Pi tool and evidence declarations match the Python sources.")
    else:
        TARGET.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
