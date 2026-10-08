import pytest

from interview_intelligence.agent.query_contract import QueryRequest, QuerySpec
from interview_intelligence.analytics.listing import ListRequest
from interview_intelligence.contracts import FilterSpec


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
