"""Generate Pi's static declarations from the host source without runtime dependencies."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/interview_intelligence/agent/tool_definitions.py"
TARGET = ROOT / "services/pi-agent/runtime.mjs"
BEGIN = "// BEGIN GENERATED TOOL CONTRACT (python scripts/sync-tool-contract.py)"
END = "// END GENERATED TOOL CONTRACT"


def generated_section():
    definitions = runpy.run_path(str(SOURCE))
    # One generated record per tool keeps executable orchestration easy to scan.
    encode = lambda value: json.dumps(value, ensure_ascii=False)
    records = [f"    {encode(name)}: {encode(tool)}" for name, tool in definitions["TOOL_DEFINITIONS"].items()]
    return (BEGIN + "\nconst toolContract = {\n  common_required: " + encode(list(definitions["COMMON_REQUIRED"]))
            + ",\n  tools: {\n" + ",\n".join(records) + "\n  }\n};\n" + END)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed Pi declarations drift")
    args = parser.parse_args()
    source = TARGET.read_text(encoding="utf-8")
    start, end = source.index(BEGIN), source.index(END) + len(END)
    expected = source[:start] + generated_section() + source[end:]
    if args.check:
        if source != expected:
            parser.exit(1, "Pi tool declarations are stale; run python scripts/sync-tool-contract.py\n")
        print("Pi tool declarations match the Python source.")
    else:
        TARGET.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
