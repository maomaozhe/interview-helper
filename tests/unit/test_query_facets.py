"""Optional semantic-planner facets stay bounded and ship with the v10 prompt."""
from pathlib import Path
import tomllib

import pytest

from interview_intelligence.agent.query_contract import QUERY_AGENT_VERSION, QuerySpec
from interview_intelligence.config import Settings


def test_v10_prompt_is_default_and_included_in_built_wheel():
    root = Path(__file__).parents[2]
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    files = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert QUERY_AGENT_VERSION == Settings().query_prompt_version == "query_agent_v10"
    assert files["prompts/query_agent_v10.md"].endswith("/query_agent_v10.md")
    prompt = (root / "prompts/query_agent_v10.md").read_text(encoding="utf-8")
    assert "requery_origin" in prompt and "lexical_facets" in prompt
    assert "relevance_query" in prompt and "当前明确纠正" in prompt


def test_optional_lexical_facets_schema_declares_max_three_bounded_strings():
    assert QuerySpec(action="SEARCH", search_query="Agent").lexical_facets == []
    field = QuerySpec.model_json_schema()["properties"]["lexical_facets"]
    assert field["maxItems"] == 3
    assert field["items"]["maxLength"] == 150 and field["items"]["minLength"] == 1


@pytest.mark.parametrize("action,facets", [("LIST", ["memory"]), ("SEARCH", [" "]),
    ("SEARCH", ["x" * 151]), ("SEARCH", ["a", "b", "c", "d"])])
def test_invalid_or_non_search_lexical_facets_are_rejected(action, facets):
    with pytest.raises(ValueError):
        QuerySpec(action=action, search_query="Agent", lexical_facets=facets)
