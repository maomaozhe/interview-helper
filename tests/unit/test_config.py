import importlib

import pytest


def config_module():
    return importlib.import_module("interview_intelligence.config")


def test_corpus_path_rejects_parent_traversal(tmp_path):
    c = config_module()
    root = tmp_path / "md"
    root.mkdir()
    assert c.resolve_corpus_path(root, "post.md") == root / "post.md"
    with pytest.raises(ValueError):
        c.resolve_corpus_path(root, "../secret.md")


def test_model_preflight_rejects_missing_budget_and_dimension():
    c = config_module()
    with pytest.raises(ValueError):
        c.validate_model_preflight(api_key="key", embedding_dimension=0, max_calls=10, max_tokens=100)
    with pytest.raises(ValueError):
        c.validate_model_preflight(api_key="key", embedding_dimension=1024, max_calls=0, max_tokens=100)
    with pytest.raises(ValueError):
        c.validate_model_preflight(api_key="", embedding_dimension=1024, max_calls=10, max_tokens=100)


def test_model_preflight_accepts_user_selected_plan_endpoint():
    c = config_module()
    c.validate_backend_model_endpoint("https://ark.cn-beijing.volces.com/api/coding/v3")
    c.validate_backend_model_endpoint("https://ark.cn-beijing.volces.com/api/v3")
    with pytest.raises(ValueError, match="MODEL_BASE_URL_INVALID"):
        c.validate_backend_model_endpoint("missing-host")


def test_corpus_discovery_keeps_issue_log_out_of_interview_sources(tmp_path):
    c = config_module()
    root = tmp_path / "md"
    root.mkdir()
    (root / "issue.md").write_text("Operational failures, not an interview", encoding="utf-8")
    (root / "interview.md").write_text("Actual interview", encoding="utf-8")
    (root / "notes.txt").write_text("Not Markdown", encoding="utf-8")
    assert [path.name for path in c.discover_corpus_documents(root)] == ["interview.md"]
    with pytest.raises(ValueError, match="RESERVED_OPERATIONAL_DOCUMENT"):
        c.resolve_corpus_document(root, "issue.md")
    with pytest.raises(ValueError, match="only Markdown"):
        c.resolve_corpus_document(root, "notes.txt")
