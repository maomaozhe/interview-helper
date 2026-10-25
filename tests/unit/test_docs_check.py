from pathlib import Path
import runpy

import pytest


Checker = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check-docs.py"))["Checker"]


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def test_links_anchors_and_code_examples(tmp_path):
    write(tmp_path, "target.md", "# 中文 `code`\n# Repeat\n# Repeat\n<a id='custom'></a>\n")
    write(tmp_path, "page.md", """# Here
[target](target.md#中文-code) [duplicate](target.md#repeat-1)
<a href="target.md#custom">custom</a> <img src="picture.svg" />
[reference][id]
[id]: target.md#repeat
[self](#here) [web](https://example.invalid/nope)
`[inline example](missing.md)`
```markdown
[example](missing.md#missing)
<img src="missing.svg">
```
~~~mermaid
flowchart LR
    A --> B
~~~
""")
    write(tmp_path, "picture.svg", "<svg/>")
    checker = Checker(tmp_path)
    assert checker.check(["page.md"]) == []
    assert checker.counts["anchors"] == 5
    assert checker.counts["fences"] == 2


@pytest.mark.parametrize("link,error", [
    ("[bad](missing.md)", "missing path"),
    ("[bad](target.md#missing)", "missing anchor"),
    ('<a href="target.md#missing">bad</a>', "missing anchor"),
    ("[bad](../outside.md)", "path leaves repository"),
])
def test_bad_link_destinations(tmp_path, link, error):
    write(tmp_path, "target.md", "# Present\n")
    write(tmp_path, "page.md", link)
    assert any(error in message for message in Checker(tmp_path).check(["page.md"]))


def test_sources_accept_nested_functions_and_qualified_methods(tmp_path):
    write(tmp_path, "source.py", """def create_app():
    async def route():
        pass
class Service:
    def run(self):
        pass
""")
    write(tmp_path, "runtime.mjs", "export function runQuery() {}")
    write(tmp_path, "test.py", "")
    page = write(tmp_path, "page.md", """---
id: example
status: current
scope: test
verified_at: 2026-10-15
sources:
  - path: source.py
    symbols: [create_app, route, create_app.route, Service.run]
  - path: runtime.mjs
    symbols: [runQuery]
tests: [test.py]
---
# Example
""")
    assert Checker(tmp_path).check(["page.md"]) == []
    page.write_text(page.read_text().replace("Service.run", "Other.run"), encoding="utf-8")
    assert any("missing symbol Other.run" in error for error in Checker(tmp_path).check(["page.md"]))


@pytest.mark.parametrize("body,error", [
    ("---\nstatus: current\n---\n", "missing metadata field"),
    ("---\nsources: [{path: missing.py}]\n---\n", "missing path"),
    ("---\ntests: [missing.py]\n---\n", "missing path"),
    ("---\nsources: [\n---\n", "metadata/source check"),
    ("```mermaid\nflowchart LR\n", "unclosed mermaid fence"),
    ("```mermaid\n%% comment only\n```\n", "empty Mermaid fence"),
    ("````text\n```\n", "unclosed text fence"),
])
def test_invalid_metadata_and_fences(tmp_path, body, error):
    write(tmp_path, "page.md", body)
    assert any(error in message for message in Checker(tmp_path).check(["page.md"]))


def test_private_and_upstream_targets_are_never_read(tmp_path, monkeypatch):
    write(tmp_path, "page.md", """---
sources:
  - path: md/issue.md
    symbols: [DoNotRead]
---
[private](data/reports/absent.md#secret)
[upstream](vendor/missing.md#secret)
[environment](.env)
""")
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.name == "page.md"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    checker = Checker(tmp_path)
    assert checker.check(["page.md"]) == []
    assert checker.skipped == {"md/issue.md", "data/reports/absent.md", "vendor/missing.md", ".env"}


def test_linked_history_is_not_recursively_checked(tmp_path):
    write(tmp_path, "page.md", "[history](history.md#evidence)\n")
    write(tmp_path, "history.md", "# Evidence\n[old broken link](absent.md)\n")
    checker = Checker(tmp_path)
    assert checker.check(["page.md"]) == []
    assert checker.counts["pages"] == 1
    assert any("missing path" in error for error in Checker(tmp_path).check(["history.md"]))
