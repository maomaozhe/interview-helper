import json

from interview_intelligence.agent.presentation import AnswerStream, ClarificationStream, partial_object


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


def test_answer_stream_decodes_markdown_from_each_real_fragment():
    stream = AnswerStream()
    first = stream.update(chunk(r'{"action":"ANSWER","answer_text":"## Redis\n\n**内存'))
    assert first == [{"delta":"## Redis\n\n**内存", "text":"## Redis\n\n**内存",
                      "temporary":True, "call_index":0}]
    second = stream.update(chunk(r'访问**\n\n```python\nprint(\"快\")\n```"}'))
    assert second[0]["delta"] == '访问**\n\n```python\nprint("快")\n```'
    assert second[0]["text"] == first[0]["text"] + second[0]["delta"]
    assert stream.update(chunk('')) == []


def test_answer_stream_withholds_split_unicode_escapes():
    stream = AnswerStream()
    first = stream.update(chunk(r'{"action":"ANSWER","answer_text":"你好\u4e'))
    assert first[0]["text"] == "你好"
    assert stream.update(chunk(r'16\ud83d'))[0]["delta"] == "世"
    event = stream.update(chunk(r'\ude00\n结束"}'))[0]
    assert event["text"] == "你好世😀\n结束"
    assert event["delta"] == "😀\n结束"
    json.dumps(event, ensure_ascii=False).encode("utf-8")


def test_only_answer_fields_become_public_text():
    for arguments in [
        '{"action":"SEARCH","answer_text":"hidden"}',
        '{"action":"ANSWER","filters":{"answer_text":"nested"}}',
        '{"action":"ANSWER","note":"\\\"answer_text\\\":\\\"quoted\\\""}',
    ]:
        assert AnswerStream().update(chunk(arguments)) == []
    assert AnswerStream().update({"choices":[{"delta":{"content":"private reasoning"}}]}) == []
    wrong_tool = chunk('{"action":"ANSWER","answer_text":"hidden"}')
    wrong_tool["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] = "search_questions"
    assert AnswerStream().update(wrong_tool) == []


def test_named_answer_tool_can_stream_before_action_field():
    stream = AnswerStream()
    first = chunk('{"answer_text":"先输出')
    first["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] = "answer_question"
    assert stream.update(first)[0]["delta"] == "先输出"
    assert stream.update(chunk('正文","action":"ANSWER"}'))[0]["delta"] == "正文"


def test_json_fallback_projects_answer_text_and_keeps_calls_separate():
    stream = AnswerStream(content_json=True)
    event = stream.update({"choices":[{"index":0,"delta":{
        "content":'{"action":"ANSWER","answer_text":"**正文'}}]})[0]
    assert event["delta"] == "**正文" and event["call_index"] == 0
    assert stream.update({"choices":[{"index":0,"delta":{"content":'**"}'}}]})[0]["delta"] == "**"
    assert AnswerStream(content_json=True).update({"choices":[{"delta":{"content":"private reasoning"}}]}) == []
