import pytest

from interview_intelligence.providers.budget import CallBudget


def test_budget_stops_before_next_model_call():
    budget = CallBudget(max_calls=1, max_tokens=100)
    budget.before_call(estimated_input_tokens=10)
    budget.after_call(input_tokens=10, output_tokens=20)
    with pytest.raises(ValueError, match="MODEL_CALL_BUDGET_EXCEEDED"):
        budget.before_call(estimated_input_tokens=1)


def test_token_budget_stops_before_call_when_remaining_too_small():
    budget = CallBudget(max_calls=3, max_tokens=10)
    with pytest.raises(ValueError, match="MODEL_TOKEN_BUDGET_EXCEEDED"):
        budget.before_call(estimated_input_tokens=11)
