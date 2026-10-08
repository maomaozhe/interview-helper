"""The local model must stay inside its finite option language."""
import importlib.util
import json
from pathlib import Path

import pytest


module_path = Path(__file__).parents[2] / "services/jev-gateway/choice_constraints.py"
spec = importlib.util.spec_from_file_location("choice_constraints", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
FiniteChoiceSequence = module.FiniteChoiceSequence
decode_finite_answer = module.decode_finite_answer


class CharacterTokenizer:
    eos_token_id = 999999

    def encode(self, text, *, add_special_tokens=False):
        assert add_special_tokens is False
        return list(map(ord, text))


def test_every_prefix_stays_in_language_and_parse_preserves_field_order():
    tokenizer = CharacterTokenizer()
    grammar = FiniteChoiceSequence(tokenizer, {
        "action": {"criteria": {"LIST": "list", "LIST_ALL": "other", "NEXT": "page"}},
        "topic": {"criteria": {"数据库": "domain", "NONE": "none"}},
    })
    selected = ["LIST_ALL", "数据库"]
    generated = tokenizer.encode("[")
    for index, (_, paths) in enumerate(grammar.fields):
        generated += paths[selected[index]]
    for position, token in enumerate(generated):
        assert token in grammar.allowed(generated[:position])
    assert grammar.allowed(generated) == [tokenizer.eos_token_id]
    choices, ranges = grammar.parse(generated + [tokenizer.eos_token_id])
    assert choices == {"action": "LIST_ALL", "topic": "数据库"}
    assert ranges["action"][1] == ranges["topic"][0]
    assert json.loads("".join(map(chr, generated))) == selected


def test_incomplete_or_out_of_language_generation_is_rejected():
    tokenizer = CharacterTokenizer()
    grammar = FiniteChoiceSequence(tokenizer, {"action": {"criteria": {"LIST": "list"}}})
    for invalid in ("{", '["EVIL"]', '["LIST"]x'):
        with pytest.raises(ValueError, match="INVALID_CHOICE"):
            grammar.allowed(tokenizer.encode(invalid))
    with pytest.raises(ValueError, match="MODEL_OUTPUT_INCOMPLETE"):
        grammar.parse(tokenizer.encode('["LI'))
    with pytest.raises(ValueError, match="OPTION_SEQUENCE_TOO_LONG"):
        FiniteChoiceSequence(tokenizer, {"action": {"criteria": {"LIST": "list"}}}, max_tokens=3)


def test_native_json_rejects_missing_unknown_and_out_of_domain_values():
    questions = {"action": {"criteria": {"LIST": "list", "NEXT": "page"}}}
    assert decode_finite_answer('{"action":"LIST"}', questions) == {"action":"LIST"}
    for text in ('{}', '{"action":"LIST","other":"anything"}', '{"action":"DELETE"}',
                 '{"action":null}', '["LIST"]'):
        with pytest.raises(ValueError, match="INVALID_ANSWER"):
            decode_finite_answer(text, questions)
    with pytest.raises(ValueError):
        decode_finite_answer('```json\n{"action":"LIST"}\n```', questions)
