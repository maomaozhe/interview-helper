import pytest

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.analytics.listing import ListRequest
from interview_intelligence.contracts import FilterSpec
from interview_intelligence.ingestion.pipeline import _normalize_company


@pytest.mark.parametrize("language", ["Java", "java", "JAVA"])
def test_ui_model_and_sql_use_the_same_language_scope(language):
    request = QueryRequest(message="列题", request_id="language", filters={"language": language})
    plan = QuerySpec(action="LIST", filters={"language": "Java"})
    sql = ListRequest(**plan.filters.model_dump())
    assert request.filters.language == plan.filters.language == sql.language == "JAVA"
    assert request.filters.model_dump(exclude_unset=True) == {"language": "JAVA"}


def test_unrecognized_language_and_independent_task_dimensions_are_preserved():
    filters = FilterSpec(language="Rust", response_form="VERBAL", coding_focus="ENGINEERING")
    assert filters.language == "Rust"
    assert filters.response_form == "VERBAL" and filters.coding_focus == "ENGINEERING"


@pytest.mark.parametrize("company", ["字节跳动", "抖音", "PDD", "boss", "腾讯音乐", "阿里云", "字节头条"])
def test_ingestion_model_ui_and_sql_share_exact_company_names(company):
    request = QueryRequest(message="列题", request_id="company", filters={"company": company})
    plan = QuerySpec(action="LIST", filters={"company": company})
    sql = ListRequest(company=company)
    assert request.filters.company == plan.filters.company == sql.company == _normalize_company(company)
    assert request.filters.model_dump(exclude_unset=True) == {"company": _normalize_company(company)}
