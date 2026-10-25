"""Check selected documentation references, not prose semantics or rendered diagrams."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import yaml


CORE = (
    "docs/README.md", "docs/architecture/system.md", "docs/architecture/data-model.md",
    "docs/architecture/ingestion-flow.md", "docs/architecture/query-flow.md",
    "docs/reference/module-map.md", "docs/reference/invariants.md",
    "docs/reference/runtime-config.md", "docs/reference/agent-workflow.md", "docs/status/current.md",
)
DEFAULTS = (*CORE, "AGENTS.md", "README.md", "eval/README.md", "docs/retrospectives/README.md")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
INLINE_CODE = re.compile(r"(`+)(?!`)(.*?)\1(?!`)", re.S)
DESTINATION = r"(<[^>\n]+>|(?:[^\s()]|\([^()\n]*\))+)"
LINK = re.compile(r"!?\[[^\]\n]*\]\(\s*" + DESTINATION + r"(?:\s+[^\n]*?)?\s*\)")
REFERENCE = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*" + DESTINATION, re.M)


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links, self.anchors = [], set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if value and name in {"href", "src"}:
                self.links.append((self.getpos()[0], value))
            if value and (name == "id" or (tag == "a" and name == "name")):
                self.anchors.add(value)


def python_symbols(text):
    names = set()

    def visit(node, parents=()):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            parents = (*parents, node.name)
            # Include nested route functions and class-qualified methods, but not
            # arbitrary dotted names whose final component happens to exist.
            names.update(".".join(parents[i:]) for i in range(len(parents)))
        for child in ast.iter_child_nodes(node):
            visit(child, parents)

    visit(ast.parse(text))
    return names


class Checker:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.errors, self.skipped = [], set()
        self.counts = Counter()

    def error(self, page, line, message):
        self.errors.append(f"{page.relative_to(self.root).as_posix()}:{line}: {message}")

    def target(self, page, line, raw, base):
        path = (base / unquote(raw)).resolve()
        if not path.is_relative_to(self.root):
            self.error(page, line, f"path leaves repository: {raw}")
            return None
        relative = path.relative_to(self.root)
        if relative.parts and (relative.parts[0].lower() in {"md", "data", "vendor", ".tmp"}
                               or any(p.lower().startswith(".env") for p in relative.parts)):
            self.skipped.add(relative.as_posix())
            return None
        if not path.exists():
            self.error(page, line, f"missing path: {raw}")
            return None
        return path

    def visible(self, page, text, report=False):
        """Blank front matter and fenced code while preserving source line numbers."""
        lines, marker, start, language, content = text.splitlines(), None, 0, "", []
        output = []
        metadata_end = 0
        if lines and lines[0] == "---":
            metadata_end = next((i for i in range(1, len(lines)) if lines[i] == "---"), len(lines))
        for number, line in enumerate(lines, 1):
            if metadata_end and number <= metadata_end + 1:
                output.append("")
                continue
            match = FENCE.match(line)
            if marker:
                if match and match[1][0] == marker[0] and len(match[1]) >= len(marker) and not match[2].strip():
                    if report and language == "mermaid" and not any(s.strip() and not s.lstrip().startswith("%%") for s in content):
                        self.error(page, start, "empty Mermaid fence")
                    marker = None
                else:
                    content.append(line)
                output.append("")
            elif match:
                marker, start, language, content = match[1], number, match[2].strip(), []
                if report:
                    self.counts["fences"] += 1
                output.append("")
            else:
                output.append(line)
        if marker and report:
            self.error(page, start, f"unclosed {language or 'code'} fence")
        return "\n".join(output)

    def anchors(self, page):
        text = self.visible(page, page.read_text(encoding="utf-8-sig"))
        anchors, used = Markup(text).anchors, set()
        for match in re.finditer(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
            title = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", match[1])
            title = unescape(re.sub(r"<[^>]+>", "", title)).lower()
            slug = re.sub(r"[^\w\-\s]", "", title)
            slug = re.sub(r"\s", "-", slug)
            candidate, suffix = slug, 0
            while candidate in used:
                suffix += 1
                candidate = f"{slug}-{suffix}"
            used.add(candidate)
        return anchors | used

    def metadata(self, page, text):
        match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        if not match:
            if page.relative_to(self.root).as_posix() in CORE or text.startswith("---\n"):
                self.error(page, 1, "missing or unclosed front matter")
            return
        try:
            meta = yaml.safe_load(match[1])
            if not isinstance(meta, dict):
                raise ValueError("front matter must be a mapping")
            if page.relative_to(self.root).as_posix() in CORE or meta.get("status") == "current":
                for key in ("id", "status", "scope", "verified_at", "sources"):
                    if not meta.get(key):
                        raise ValueError(f"missing metadata field: {key}")
                date.fromisoformat(str(meta["verified_at"]))
            sources, tests = meta.get("sources", []), meta.get("tests", [])
            if not isinstance(sources, list) or not isinstance(tests, list):
                raise ValueError("sources and tests must be lists")
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("path"), str):
                    raise ValueError("each source must contain a string path")
                symbols = source.get("symbols", [])
                if not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols):
                    raise ValueError("source symbols must be a list of strings")
                self.counts["sources"] += 1
                target = self.target(page, 1, source["path"], self.root)
                if target and symbols:
                    body = target.read_text(encoding="utf-8-sig")
                    names = python_symbols(body) if target.suffix == ".py" else None
                    for symbol in symbols:
                        self.counts["symbols"] += 1
                        missing = symbol not in names if names is not None else symbol not in body
                        if missing:
                            self.error(page, 1, f"missing symbol {symbol} in {source['path']}")
            for test in tests:
                if not isinstance(test, str):
                    raise ValueError("test paths must be strings")
                self.counts["tests"] += 1
                self.target(page, 1, test, self.root)
        except (yaml.YAMLError, ValueError, OSError, SyntaxError) as exc:
            self.error(page, 1, f"metadata/source check: {exc}")

    def check(self, paths=DEFAULTS):
        for raw in paths:
            page = self.target(self.root / "check-docs", 1, raw, self.root)
            if page is None:
                continue
            if not page.is_file() or page.suffix.lower() != ".md":
                self.error(page, 1, "expected a Markdown file")
                continue
            self.counts["pages"] += 1
            text = page.read_text(encoding="utf-8-sig")
            self.metadata(page, text)
            visible = self.visible(page, text, report=True)
            visible = INLINE_CODE.sub(lambda m: "\n" * m[0].count("\n"), visible)
            links = Markup(visible).links
            for pattern in (LINK, REFERENCE):
                links.extend((visible.count("\n", 0, m.start()) + 1, m[1].strip("<>")) for m in pattern.finditer(visible))
            for line, link in links:
                parsed = urlsplit(unescape(link))
                if parsed.scheme or parsed.netloc:
                    continue
                self.counts["links"] += 1
                base = self.root if parsed.path.startswith("/") else page.parent
                target = self.target(page, line, parsed.path.lstrip("/") if parsed.path else page.name, base)
                if target and parsed.fragment and target.suffix.lower() in {".md", ".html", ".htm"}:
                    self.counts["anchors"] += 1
                    if unquote(parsed.fragment) not in self.anchors(target):
                        self.error(page, line, f"missing anchor: {link}")
        return self.errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="repository-relative Markdown paths; defaults to current entry pages")
    args = parser.parse_args()
    checker = Checker(Path(__file__).resolve().parents[1])
    checker.check(args.paths or DEFAULTS)
    for error in checker.errors:
        print(error)
    print(f"Checked {dict(checker.counts)}; skipped {len(checker.skipped)} private/upstream references (no content read).")
    print("Static references and fence checks only; prose alignment and Mermaid syntax/rendering are not verified.")
    return bool(checker.errors)


if __name__ == "__main__":
    raise SystemExit(main())
