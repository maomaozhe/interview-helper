"""Protocol and trust-boundary checks for the optional stdio REST adapter."""
import asyncio
import json

import httpx
import pytest

pytest.importorskip("mcp", reason="Install the optional mcp extra to test the MCP adapter")
from mcp.client import Client

from interview_intelligence.mcp_server import (
    KnowledgeClient, MAX_RESPONSE_BYTES, TOOL_NAMES, create_server,
)


QUESTION_ID = "ad38ebca-41ba-45da-b3d3-b9e355626deb"


def envelope(data=None, **meta):
    return {"data": data if data is not None else [],
            "meta": {"corpus_revision": 400, "indexed_revision": 400, **meta}, "warnings": []}


def test_sdk_negotiation_lists_readonly_tools_and_preserves_evidence_in_both_result_channels():
    async def scenario():
        seen = []
        expected = envelope([{"canonical_question_id": QUESTION_ID, "raw_question": "Redis 为什么快？",
                              "source_summary": {"raw_file_hash": "a" * 64, "spans": [{"quote": "Redis 为什么快？"}]}}],
                            requested_pipeline="HYBRID_RERANK", executed_pipeline="BM25", degraded=True,
                            rerank_status="NOT_REQUESTED", result_completeness={"complete": False},
                            applied_filters={"company": "字节"})
        expected["warnings"] = ["本次结果来自检索候选，不代表题库全部匹配题。"]

        def backend(request):
            seen.append(request)
            if request.url.path.endswith("/health"):
                return httpx.Response(200, json=envelope({"search_request_limits": {
                    "version": "public_search_limits_v1", "max_model_calls": 6, "max_tokens": 96000}}))
            return httpx.Response(200, json=expected)

        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            knowledge = KnowledgeClient("http://knowledge.test/interview", tenant_session="secret-cookie",
                                        http_client=http)
            async with Client(create_server(knowledge)) as session:
                listing = await session.list_tools()
                assert {tool.name for tool in listing.tools} == TOOL_NAMES
                assert all(tool.annotations.read_only_hint for tool in listing.tools)
                assert all(not tool.annotations.idempotent_hint for tool in listing.tools)
                assert all(tool.input_schema["additionalProperties"] is False for tool in listing.tools)
                stats = next(tool for tool in listing.tools if tool.name == "interview_get_stats")
                assert stats.input_schema["$defs"]["StatsInput"]["properties"]["cursor"]["type"] == "null"
                result = await session.call_tool("interview_search_questions", {"request": {
                    "query": "缓存设计", "filters": {"company": "字节"}, "pipeline": "HYBRID_RERANK"}})
                assert not result.is_error
                output = result.structured_content
                assert {key: output[key] for key in expected} == expected
                assert json.loads(result.content[0].text) == output
                assert output["mcp_adapter"]["execution_contract"] == "existing_rest_api"
                assert "relevance_status" not in output["meta"]
        assert len(seen) == 2
        request = seen[-1]
        assert request.method == "GET" and request.url.path == "/interview/api/questions/search"
        assert request.url.params["query"] == "缓存设计"
        assert request.url.params["company"] == "字节"
        assert request.url.params["usage_purpose"] == "evaluation"
        assert request.headers["X-Usage-Purpose"] == "evaluation"
        assert request.headers["Cookie"] == "ii_tenant_session=secret-cookie"
        assert "secret-cookie" not in json.dumps(output)

    asyncio.run(scenario())


def test_capabilities_report_actual_allowlist_and_omit_sensitive_health_fields():
    async def scenario():
        def backend(request):
            if request.url.path.endswith("health"):
                return httpx.Response(200, json=envelope({
                    "database": "ready", "index": "ready", "current_revision": 400,
                    "indexed_revision": 400, "task_filters_enabled": True,
                    "api_key": "never expose health extras", "model_lock_path": "/private/path"}))
            return httpx.Response(200, json=envelope({"taxonomy_version": "v1", "topics": {"Redis": {}}}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            knowledge = KnowledgeClient("http://knowledge.test", allowed_pipelines=["BM25"], http_client=http)
            async with Client(create_server(knowledge)) as session:
                result = await session.call_tool("interview_get_capabilities", {})
                output = result.structured_content
                assert output["data"]["allowed_pipelines"] == ["BM25"]
                assert output["data"]["taxonomy"]["taxonomy_version"] == "v1"
                assert output["data"]["filter_schema"]["additionalProperties"] is False
                assert output["data"]["supports"]["conversational_scope"] is False
                assert "private" not in json.dumps(output) and "api_key" not in json.dumps(output)
    asyncio.run(scenario())


@pytest.mark.parametrize("name,arguments", [
    ("interview_search_questions", {"request": {"query": "Redis", "user_id": "other"}}),
    ("interview_search_questions", {"request": {"query": "Redis", "top_k": 51}}),
    ("interview_search_questions", {"request": {"query": "Redis", "filters": {"user_id": "other"}}}),
    ("interview_get_question", {"request": {"canonical_question_id": "../../api/review"}}),
    ("interview_get_stats", {"request": {}, "url": "https://evil.example"}),
    ("interview_get_stats", {"request": {"cursor": "ignored-by-rest"}}),
    ("interview_get_capabilities", {"user_id": "other"}),
    ("interview_record_review", {"request": {"status": "MASTERED"}}),
])
def test_protocol_rejects_undocumented_identity_write_and_unbounded_inputs_without_http(name, arguments):
    async def scenario():
        def backend(request):
            raise AssertionError("invalid tool inputs must not reach the knowledge API")
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool(name, arguments)
                assert result.is_error
    asyncio.run(scenario())


def test_server_allowlist_rejects_model_pipeline_even_when_caller_explicitly_requests_it():
    async def scenario():
        def backend(request):
            raise AssertionError("disallowed pipeline must not reach the knowledge API")
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            async with Client(create_server(KnowledgeClient(
                    "http://knowledge.test", allowed_pipelines=["BM25"], http_client=http))) as session:
                result = await session.call_tool("interview_search_questions", {"request": {
                    "query": "缓存设计", "pipeline": "HYBRID_RERANK"}})
                assert result.is_error
                assert result.structured_content["error"]["code"] == "MCP_PIPELINE_NOT_ALLOWED"
    asyncio.run(scenario())


def test_model_search_requires_backend_request_limits_before_any_retrieval():
    async def scenario():
        seen = []
        def backend(request):
            seen.append(request.url.path)
            assert request.url.path == "/api/health"
            return httpx.Response(200, json=envelope({"index": "ready"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool("interview_search_questions", {"request": {
                    "query": "Redis", "pipeline": "HYBRID_RERANK"}})
                assert result.is_error
                assert result.structured_content["error"]["code"] == "MCP_BACKEND_LIMITS_REQUIRED"
        assert seen == ["/api/health"]
    asyncio.run(scenario())


def test_signed_cursor_repeated_filters_and_occurrence_pagination_are_transmitted_losslessly():
    async def scenario():
        seen = []
        def backend(request):
            seen.append(request)
            return httpx.Response(200, json=envelope(pagination={"total": 8, "next_cursor": "signed+/=cursor"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            knowledge = KnowledgeClient("http://knowledge.test/prefix/", http_client=http)
            result = await knowledge.call("interview_list_questions", {"request": {
                "company": "字节", "round": "SECOND", "page_size": 1,
                "cursor": "signed+/=cursor", "review_statuses": ["WEAK", "UNSEEN"]}})
            assert result["meta"]["pagination"]["total"] == 8
            await knowledge.call("interview_get_occurrences", {"request": {
                "canonical_question_id": QUESTION_ID, "filters": {"company": "字节", "round": "SECOND"},
                "offset": 20, "limit": 5}})
        assert seen[0].url.params["cursor"] == "signed+/=cursor"
        assert seen[0].url.params.get_list("review_statuses") == ["WEAK", "UNSEEN"]
        assert seen[1].url.path == f"/prefix/api/questions/{QUESTION_ID}/occurrences"
        assert seen[1].url.params["company"] == "字节" and seen[1].url.params["round"] == "SECOND"
        assert seen[1].url.params["offset"] == "20" and seen[1].url.params["limit"] == "5"
    asyncio.run(scenario())


@pytest.mark.parametrize("status,body,code", [
    (401, {"error": {"code": "TENANT_AUTH_REQUIRED", "message": "secret-cookie"}}, "TENANT_AUTH_REQUIRED"),
    (409, {"error": {"code": "SNAPSHOT_CHANGED", "details": {"secret": "secret-cookie"}}}, "SNAPSHOT_CHANGED"),
    (400, {"error": {"code": "INVALID_SCOPE_EXPIRED", "message": "secret-cookie"}}, "INVALID_SCOPE_EXPIRED"),
    (503, {"error": {"code": "QUERY_RELEVANCE_UNAVAILABLE", "message": "secret-cookie"}}, "QUERY_RELEVANCE_UNAVAILABLE"),
    (500, {"error": {"code": "ARBITRARY_SECRET", "message": "secret-cookie"}}, "UPSTREAM_HTTP_ERROR"),
    (503, {"error": ["secret-cookie"]}, "UPSTREAM_HTTP_ERROR"),
    (503, {"error": "secret-cookie"}, "UPSTREAM_HTTP_ERROR"),
    (302, None, "UPSTREAM_HTTP_ERROR"),
])
def test_api_failures_remain_tool_errors_without_raw_payloads_or_redirects(status, body, code):
    async def scenario():
        seen = []
        def backend(request):
            seen.append(request)
            return httpx.Response(status, json=body, headers={"Location": "https://evil.example/steal"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend), follow_redirects=True) as http:
            async with Client(create_server(KnowledgeClient(
                    "http://knowledge.test", tenant_session="secret-cookie", http_client=http))) as session:
                result = await session.call_tool("interview_get_stats", {"request": {}})
                assert result.is_error and result.structured_content["error"]["code"] == code
                assert "secret-cookie" not in result.content[0].text
                assert "ARBITRARY_SECRET" not in result.content[0].text
        assert len(seen) == 1
    asyncio.run(scenario())


def test_provider_nonretryable_failure_is_preserved_despite_http_503():
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                503, json={"error": {"code": "MODEL_PROVIDER_UNAVAILABLE", "retryable": False}}))) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool("interview_get_stats", {"request": {}})
                assert result.is_error
                assert result.structured_content["error"]["retryable"] is False
    asyncio.run(scenario())


def test_capabilities_reject_corpus_change_between_health_and_taxonomy_reads():
    async def scenario():
        def backend(request):
            return httpx.Response(200, json=envelope({}, corpus_revision=(
                400 if request.url.path.endswith("health") else 401)))
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool("interview_get_capabilities", {})
                assert result.is_error
                assert result.structured_content["error"]["code"] == "SNAPSHOT_CHANGED"
    asyncio.run(scenario())


@pytest.mark.parametrize("response,error_code", [
    (httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)), "MCP_RESULT_TOO_LARGE"),
    (httpx.Response(200, content=b"<html>login</html>"), "MCP_INVALID_API_RESPONSE"),
    (httpx.Response(200, json={"data": [], "meta": {}}), "MCP_INVALID_API_RESPONSE"),
])
def test_oversized_or_malformed_success_is_not_silently_truncated_or_accepted(response, error_code):
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool("interview_get_stats", {"request": {}})
                assert result.is_error and result.structured_content["error"]["code"] == error_code
    asyncio.run(scenario())


def test_timeout_does_not_retry_or_expose_request_and_credentials():
    async def scenario():
        seen = []
        def backend(request):
            seen.append(request)
            raise httpx.ReadTimeout("secret-cookie http://private-server/", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as http:
            async with Client(create_server(KnowledgeClient("http://knowledge.test", http_client=http))) as session:
                result = await session.call_tool("interview_get_stats", {"request": {}})
                assert result.is_error and result.structured_content["error"]["code"] == "MCP_UPSTREAM_TIMEOUT"
                assert "secret-cookie" not in result.content[0].text and "private-server" not in result.content[0].text
        assert len(seen) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("base,changes", [
    ("file:///private/database", {}), ("http://secret:password@knowledge.test", {}),
    ("http://knowledge.test?token=secret", {}), ("http://knowledge.test#part", {}),
    ("http://knowledge.test", {"tenant_session": "a; evil=secret"}),
    ("http://knowledge.test", {"timeout_seconds": float("nan")}),
    ("http://knowledge.test", {"timeout_seconds": 1831}),
    ("http://knowledge.test", {"allowed_pipelines": []}),
    ("http://knowledge.test", {"allowed_pipelines": ["custom"]}),
])
def test_invalid_process_configuration_is_rejected(base, changes):
    with pytest.raises(ValueError):
        KnowledgeClient(base, **changes)


def test_environment_configuration_is_explicit_and_bm25_restriction_is_enforced(monkeypatch):
    monkeypatch.delenv("INTERVIEW_API_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="required"):
        KnowledgeClient.from_env()
    monkeypatch.setenv("INTERVIEW_API_BASE_URL", "http://knowledge.test/interview")
    monkeypatch.setenv("INTERVIEW_MCP_ALLOWED_PIPELINES", "BM25")
    knowledge = KnowledgeClient.from_env()
    assert knowledge.allowed_pipelines == ("BM25",)
    assert knowledge.usage_purpose == "evaluation"
    asyncio.run(knowledge.aclose())
