"""The MCP adapter uses the real API's SQL scope, paging and evidence contract."""
import asyncio

import httpx
import pytest

pytest.importorskip("mcp", reason="install the optional mcp extra for MCP protocol tests")

from interview_intelligence.api import create_app
from interview_intelligence.config import Settings
from interview_intelligence.domain.models import CorpusState
from interview_intelligence.mcp_server import AdapterError, KnowledgeClient
from test_analytics import seed_corpus


def run_with_api(app, operation, **options):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app)) as http:
            client = KnowledgeClient("http://knowledge.test", http_client=http, **options)
            return await operation(client)
    return asyncio.run(run())


def test_mcp_preserves_complete_sql_pagination_statistics_and_filtered_sources():
    database, fast_id, persistence_id = seed_corpus()
    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:"))

    async def operation(client):
        scope = {"topic_l1": "Redis", "page_size": 1}
        first = await client.call("interview_list_questions", {"request": scope})
        cursor = first["meta"]["pagination"]["next_cursor"]
        second = await client.call("interview_list_questions", {"request": {**scope, "cursor": cursor}})
        assert first["data"][0]["canonical_question_id"] == fast_id
        assert first["data"][0]["occurrence_count"] == 2
        assert second["data"][0]["canonical_question_id"] == persistence_id
        assert first["meta"]["pagination"]["total"] == 2
        assert first["meta"]["sample_counts"]["occurrences"] == 3
        assert second["meta"]["pagination"]["next_cursor"] is None
        stats = await client.call("interview_get_stats", {"request": {"company": "字节", "round": "SECOND"}})
        assert [row["canonical_question_id"] for row in stats["data"]] == [persistence_id]
        detail = await client.call("interview_get_question", {"request": {
            "canonical_question_id": fast_id, "filters": {"company": "字节"}}})
        assert detail["data"]["occurrence_count"] == 1
        assert detail["data"]["sources"][0]["quote"] == "Redis为什么快？"
        sources = await client.call("interview_get_occurrences", {"request": {
            "canonical_question_id": fast_id, "filters": {"company": "字节"}, "limit": 1}})
        assert sources["meta"]["pagination"]["total"] == 1
        assert sources["data"][0]["company"] == "字节"
        assert sources["data"][0]["round"] == "FIRST"
        with pytest.raises(AdapterError) as error:
            await client.call("interview_list_questions", {"request": {**scope, "company": "腾讯", "cursor": cursor}})
        assert error.value.payload["error"]["code"] == "INVALID_CURSOR_SCOPE"

    run_with_api(app, operation)


def test_mcp_candidate_search_applies_same_occurrence_filters_and_preserves_budget():
    database, _, persistence_id = seed_corpus()
    with database.session() as session, session.begin():
        state = session.get(CorpusState, 1)
        state.current_revision = state.indexed_revision = 1

    class Retriever:
        def retrieve(self, query, eligible_ids, pipeline, top_k):
            assert eligible_ids == [persistence_id]
            assert pipeline == "BM25"
            return {"data": [{"canonical_question_id": persistence_id}], "meta": {}}

    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:"), Retriever())

    async def operation(client):
        result = await client.call("interview_search_questions", {"request": {
            "query": "Redis", "filters": {"company": "字节", "round": "SECOND"}}})
        assert [row["canonical_question_id"] for row in result["data"]] == [persistence_id]
        assert result["meta"]["result_completeness"]["corpus_complete"] is False
        assert result["meta"]["relevance_status"] == "UNVERIFIED"
        assert result["meta"]["request_budget"]["charged_model_calls"] == 0
        assert result["warnings"]

    run_with_api(app, operation, allowed_pipelines=["BM25"])


def test_mcp_keeps_tenant_authentication_and_index_not_ready_as_failures():
    database, _, _ = seed_corpus()
    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:",
        multi_tenant_enabled=True, app_signing_key="mcp-tenant-authentication-test"))

    async def operation(client):
        with pytest.raises(AdapterError) as error:
            await client.call("interview_list_questions", {"request": {}})
        assert error.value.payload["error"]["code"] == "TENANT_AUTH_REQUIRED"

    run_with_api(app, operation)

    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:"))

    async def unready(client):
        with pytest.raises(AdapterError) as error:
            await client.call("interview_search_questions", {"request": {"query": "Redis"}})
        assert error.value.payload["error"]["code"] == "INDEX_NOT_READY"

    run_with_api(app, unready)


def test_fixed_account_cookie_and_signed_cursor_remain_bound_to_the_account():
    database, _, _ = seed_corpus()
    app = create_app(database, Settings(database_url="sqlite+pysqlite:///:memory:",
        multi_tenant_enabled=True, app_signing_key="mcp-fixed-account-cursor-test"))

    async def operation(unused):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://knowledge.test") as http:
            async def register(username):
                response = await http.post("/api/account/register", headers={"X-Tenant-Action": "1"},
                    json={"username": username, "password": "isolated-test-password"})
                assert response.status_code == 200, response.text
                return response.cookies["ii_tenant_session"]
            alice_cookie = await register("mcp-alice")
            alice = KnowledgeClient("http://knowledge.test", tenant_session=alice_cookie, http_client=http)
            scope = {"page_size": 1}
            first = await alice.call("interview_list_questions", {"request": scope})
            cursor = first["meta"]["pagination"]["next_cursor"]
            bob_cookie = await register("mcp-bob")
            # The transport jar now contains Bob; Alice's process credential
            # still selects Alice and can resume her existing result scope.
            second = await alice.call("interview_list_questions", {"request": {**scope, "cursor": cursor}})
            assert second["meta"]["pagination"]["offset"] == 1
            bob = KnowledgeClient("http://knowledge.test", tenant_session=bob_cookie, http_client=http)
            with pytest.raises(AdapterError) as error:
                await bob.call("interview_list_questions", {"request": {**scope, "cursor": cursor}})
            assert error.value.payload["error"]["code"] == "INVALID_CURSOR_SCOPE"

    run_with_api(app, operation)
