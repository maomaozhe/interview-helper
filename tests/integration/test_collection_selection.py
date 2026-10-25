"""Large audited collections first expose a durable, lossless browse choice."""
from copy import deepcopy

from fastapi.testclient import TestClient

from interview_intelligence.agent.query_contract import QuerySpec
from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from test_complete_engineering_query import fixture, execute, NoCandidateSearch, NoPlanner


def client_fixture(tmp_path, *, threshold=3, extra=0, complete=False):
    db, _ = fixture(tmp_path, extra=extra, complete=complete)
    app = create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:", query_prompt_version="query_agent_v15",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True,
        query_collection_selection_threshold=threshold), NoCandidateSearch(), query_planner=NoPlanner())
    return db, TestClient(app)


def submit(client, message, previous=None, **values):
    request = {"message": message, "request_id": "choice-" + str(submit.counter), **values}
    submit.counter += 1
    if previous:
        request.update(conversation_id=previous["meta"]["conversation_id"],
            expected_version=previous["meta"]["conversation_version"])
    response = client.post("/api/query", json=request)
    assert response.status_code == 200, response.text
    return response.json()


submit.counter = 0


def test_large_scope_reports_dedup_counts_and_all_is_paged_without_reasking(tmp_path):
    _, client = client_fixture(tmp_path, extra=101)
    first = submit(client, "列出所有工程系统设计题", page_size=13)
    meta = first["meta"]
    assert meta["planning"]["spec"]["action"] == "CLARIFY" and first["data"] == []
    assert "105 道" in meta["answer"]
    assert meta["collection_selection"]["counts"]["visible"] == 105
    assert meta["collection_selection"]["counts"]["confirmed"] == 104
    assert meta["collection_selection"]["counts"]["pending_only"] == 1
    current = submit(client, "全量分页", first)
    ids = []
    while True:
        assert current["meta"]["planning"]["spec"]["action"] in {"LIST", "NEXT"}
        assert current["meta"]["collection_selection"]["stage"] == "selected"
        ids.extend(row["canonical_question_id"] for row in current["data"])
        if not current["meta"]["pagination"]["next_cursor"]:
            break
        current = submit(client, "下一页", current)
    assert len(ids) == len(set(ids)) == 105
    pending = submit(client, "这些题一共有多少题", current)
    assert pending["meta"]["counts"]["canonical_questions"] == 104
    assert pending["meta"]["pending_counts"]["canonical_questions"] == 1
    sorted_result = submit(client, "按重要性排序", pending)
    assert sorted_result["meta"]["planning"]["spec"]["action"] == "LIST"
    again = submit(client, "列出所有工程系统设计题", sorted_result)
    assert again["meta"]["planning"]["spec"]["action"] == "LIST"


def test_all_choice_preserves_ui_scope_and_current_page_size(tmp_path):
    _, client = client_fixture(tmp_path, threshold=1)
    first = submit(client, "列出所有工程系统设计题", filters={"company": "字节"})
    result = submit(client, "全量分页", first, page_size=1)
    assert result["meta"]["pagination"]["page_size"] == 1
    assert result["meta"]["applied_filters"]["company"] == "字节"
    assert result["meta"]["pagination"]["result_total"] == 3


def test_categories_are_lossless_and_pending_bucket_keeps_raw_question(tmp_path):
    _, client = client_fixture(tmp_path)
    first = submit(client, "列出所有工程系统设计题")
    menu = submit(client, "按分类浏览", first)
    selection = menu["meta"]["collection_selection"]
    assert selection["stage"] == "categories"
    pending_choice = next(item for item in selection["choices"] if item["kind"] == "pending")
    result = submit(client, pending_choice["message"], menu)
    assert {row["canonical_question_id"] for row in result["data"]} == {"other"}
    assert result["data"][0]["pending_sources"][0]["raw_question"] == "设计一个消息系统"
    assert result["meta"]["list_request"]["collection_category"] == "pending"
    counted = submit(client, "这些题一共有多少题", result)
    assert counted["meta"]["counts"]["canonical_questions"] == 0
    assert counted["meta"]["pending_counts"]["canonical_questions"] == 1
    all_rows = submit(client, "全量分页", counted)
    assert all_rows["meta"]["pagination"]["result_total"] == 4


def test_changing_filters_with_a_category_choice_cannot_drop_the_category(tmp_path):
    _, client = client_fixture(tmp_path)
    first = submit(client, "列出所有工程系统设计题")
    menu = submit(client, "按分类浏览", first)
    choice = next(item for item in menu["meta"]["collection_selection"]["choices"] if item["kind"] == "pending")
    changed = submit(client, choice["message"], menu, filters={"company": "字节"})
    assert changed["meta"]["planning"]["spec"]["action"] == "CLARIFY"
    assert changed["data"] == []
    assert changed["meta"]["applied_filters"]["company"] == "字节"
    assert changed["meta"]["collection_selection"]["counts"]["visible"] == 3


def test_small_top_n_count_and_stats_do_not_get_quantity_menu(tmp_path):
    _, service = fixture(tmp_path, extra=101)
    run, result = execute(service, "列出所有工程系统设计题前2道")
    assert run.plan.action == "LIST" and len(result["facts"]["data"]) == 2
    _, counted = execute(service, "一共有多少工程系统设计题")
    assert counted["intent"] == "COUNT"
    _, stats = execute(service, "所有工程系统设计题按公司统计", plan=QuerySpec(
        action="STATS", semantic_task="ENGINEERING_DESIGN", group_by="company"))
    assert stats["intent"] == "STATS"


def test_preview_is_idempotent_and_next_does_not_reuse_an_old_page(tmp_path):
    _, client = client_fixture(tmp_path)
    request = {"message": "列出所有工程系统设计题", "request_id": "same-preview"}
    first = client.post("/api/query", json=request)
    assert first.status_code == 200, first.text
    assert client.post("/api/query", json=request).json() == first.json()
    repeated = submit(client, "下一页", first.json())
    assert repeated["data"] == []
    assert repeated["meta"]["planning"]["spec"]["action"] == "CLARIFY"
    assert repeated["meta"]["collection_selection"]["stage"] == "scope"


def test_structured_library_group_page_retains_selected_collection(tmp_path):
    db, _ = fixture(tmp_path, complete=True)
    class StatsPlanner:
        def plan(self, run, context):
            assert "公司" in run.request.message
            return QuerySpec(action="STATS", semantic_task="ENGINEERING_DESIGN", group_by="company", page_size=1)
    client = TestClient(create_app(db, Settings(database_url="sqlite+pysqlite:///:memory:", query_prompt_version="query_agent_v15",
        snapshot_root=tmp_path / "snapshots", query_router_enabled=True,
        query_collection_selection_threshold=3), NoCandidateSearch(), query_planner=StatsPlanner()))
    first = submit(client, "列出所有工程系统设计题")
    all_page = submit(client, "全量分页", first)
    groups = submit(client, "这些题按公司统计", all_page)
    meta = groups["meta"]
    assert meta["pagination"]["next_cursor"]
    page = client.post("/api/questions/list", json={"request_id": "library-group-next",
        "conversation_id": meta["conversation_id"], "expected_version": meta["conversation_version"],
        "list_request": {**meta["list_request"], "cursor": meta["pagination"]["next_cursor"]}})
    assert page.status_code == 200, page.text
    assert page.json()["data"][0]["key"] != groups["data"][0]["key"]
    assert page.json()["meta"]["pagination"]["offset"] == 1
