"""Offline viewer must preserve provenance and never turn source text into HTML."""
from html.parser import HTMLParser
import json

import pytest

from eval.common import digest
from eval.validate_gold import GoldValidationError
from evals.agent_gold import user_query_viewer as viewer


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.data, self.capture = [], [], False
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script" and dict(attrs).get("id") == "gold-data": self.capture = True
    def handle_endtag(self, tag):
        if tag == "script": self.capture = False
    def handle_data(self, value):
        if self.capture: self.data.append(value)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "file:///D:/secret", "//example.com/x",
    "https://example.com/\nscript", "https://example.com/\"x", "https://name:secret@example.com/",
    "https://example.com\\@other.com/", " https://example.com", "https://"])
def test_only_unambiguous_http_urls_can_become_links(url):
    assert viewer.safe_url(url) is None


def test_original_link_requires_an_explicit_source_field_and_keeps_its_quote():
    text = "# 面经\n正文里提到 https://irrelevant.example/\n- 链接：https://source.example/post?id=1&x=2\n- 链接：javascript:alert(1)\n"
    values = viewer.original_urls(text, "a" * 64, "sources/a.md")
    assert [v["url"] for v in values] == ["https://source.example/post?id=1&x=2"]
    span = values[0]["evidence"]
    assert text[span["start_char"]:span["end_char"]] == span["quote"] == values[0]["url"]
    assert span["source_hash"] == "a" * 64


def test_script_termination_and_html_payload_stay_literal_data():
    attack = '</script><img src=x onerror="window.pwned=1"><script>window.pwned=2</script>'
    payload = {"provenance": {"dataset_id": attack}, "questions": [{"raw_question": attack}],
               "scopes": {}, "cases": [], "history": []}
    document = viewer.render_html(payload)
    page = Page(); page.feed(document)
    assert json.loads("".join(page.data)) == payload
    assert len([t for t, _ in page.tags if t == "script"]) == 2
    assert not any(t == "img" for t, _ in page.tags)
    assert not any("onerror" in attrs for _, attrs in page.tags)
    assert "Content-Security-Policy" in document


def fixture_bundle(tmp_path):
    text = "# 原文\n- 链接：https://source.example/post\n\n原题甲\n相邻追问乙\n"
    raw = text.encode("utf-8"); sha = digest(raw)
    (tmp_path / "source.md").write_bytes(raw)
    (tmp_path / "history.json").write_text('{"entries":[]}', encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "freeze.json").write_text("{}", encoding="utf-8")
    start = text.index("原题甲")
    q = {"id": "q", "source_path": "source.md", "source_hash": sha,
         "source_spans": [{"start_char": start, "end_char": start + 3, "quote": "原题甲"}],
         "context_spans": [{"start_char": start, "end_char": len(text), "quote": text[start:]}]}
    b = {"manifest": {"dataset_id": "fixture", "snapshot": {}, "history_path": "history.json"},
         "questions": [q], "scopes": {}, "cases": []}
    return b


@pytest.mark.parametrize("tamper", ["source_bytes", "source_span", "context_span", "path"])
def test_viewer_cannot_rebind_a_verified_label_to_changed_source_content(tmp_path, tamper):
    bundle = fixture_bundle(tmp_path)
    q = bundle["questions"][0]
    if tamper == "source_bytes": (tmp_path / "source.md").write_bytes(b"changed")
    if tamper == "source_span": q["source_spans"][0]["quote"] = "另外的题"
    if tamper == "context_span": q["context_spans"][0]["start_char"] += 1
    if tamper == "path": q["source_path"] = "../source.md"
    with pytest.raises(ValueError if tamper == "path" else GoldValidationError):
        viewer.viewer_payload(tmp_path, bundle)


def test_build_uses_strict_validator_and_refuses_outputs_inside_frozen_dataset(monkeypatch, tmp_path):
    bundle = fixture_bundle(tmp_path)
    calls = []
    def validate(path): calls.append(path); return bundle
    monkeypatch.setattr(viewer, "validate_dataset", validate)
    with pytest.raises(GoldValidationError, match="VIEWER_OUTPUT_INSIDE_FROZEN_GOLD"):
        viewer.build(tmp_path, tmp_path / "browser")
    assert calls == [tmp_path]
