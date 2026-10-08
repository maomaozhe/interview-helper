import json

from interview_intelligence.agent.presentation import ClarificationStream, partial_object


def chunk(text):
    return {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":text}}]}}]}


def test_partial_strings_unicode_and_escapes():
    assert partial_object('{"action":"CLARIFY","clarification":"想看哪') == {
        "action":"CLARIFY", "clarification":"想看哪"}
    assert partial_object(r'{"action":"CLARIFY","clarification":"第一行\n\u65b9\u5') == {
        "action":"CLARIFY", "clarification":"第一行\n方"}
    assert partial_object(r'{"clarification":"带\"引号\"和反斜杠\\') == {
        "clarification":'带"引号"和反斜杠\\'}


def test_nested_or_quoted_fields_cannot_become_a_question():
    assert "clarification" not in partial_object('{"filters":{"clarification":"secret"}')
    assert "action" not in partial_object('{"note":"\\\"action\\\":\\\"CLARIFY\\\""}')
    assert partial_object('not json') == {}
    assert partial_object('{"action":null,"clarification":null}') == {"action":None,"clarification":None}


def test_stream_uses_question_snapshots_and_never_search_arguments(monkeypatch):
    tick=iter([1., 1.1, 1.2, 1.3])
    monkeypatch.setattr("interview_intelligence.agent.presentation.time.monotonic",lambda:next(tick))
    stream=ClarificationStream()
    assert stream.update(chunk('{"action":"CLARIFY","filters":{"company":null},"clarification":"想看'))["text"] == "想看"
    assert stream.update(chunk('哪个方向？","clarification_options":["Agent","业务"]}'))["text"] == "想看哪个方向？"
    assert stream.update(chunk('')) is None
    assert ClarificationStream().update(chunk('{"action":"SEARCH","clarification":"不能展示","search_query":"raw"}')) is None
    assert ClarificationStream().update({"choices":[{"delta":{"content":"hidden reasoning"}}]}) is None


def test_incomplete_tool_arguments_are_not_repaired_into_a_final_result():
    stream=ClarificationStream()
    event=stream.update(chunk('{"action":"CLARIFY","clarification":"临时问句'))
    assert event == {"text":"临时问句", "temporary":True,"call_index":0}
    assert "options" not in event
    assert len(json.dumps(event,ensure_ascii=False)) < 100


def test_split_emoji_escape_never_breaks_the_public_utf8_event_stream(monkeypatch):
    monkeypatch.setattr("interview_intelligence.agent.presentation.time.monotonic",lambda:100.)
    stream=ClarificationStream()
    event=stream.update(chunk(r'{"action":"CLARIFY","clarification":"方向\ud83d'))
    assert event["text"] == "方向"
    json.dumps(event,ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr("interview_intelligence.agent.presentation.time.monotonic",lambda:101.)
    event=stream.update(chunk(r'\ude00？"}'))
    assert event["text"] == "方向😀？"
    json.dumps(event,ensure_ascii=False).encode("utf-8")
