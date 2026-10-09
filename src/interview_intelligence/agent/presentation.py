"""Project user-facing text from incomplete top-level model JSON."""
import json
import time


def partial_object(text):
    """Read complete fields and at most one unfinished string; ignore nested keys."""
    decoder = json.JSONDecoder()
    position, values = 0, {}
    text = text.lstrip()
    if not text.startswith("{"):
        return values
    position = 1
    while position < len(text):
        while position < len(text) and text[position] in " \r\n\t,":
            position += 1
        try:
            key, position = decoder.raw_decode(text, position)
        except ValueError:
            break
        if not isinstance(key, str):
            break
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text) or text[position] != ":":
            break
        position += 1
        while position < len(text) and text[position].isspace():
            position += 1
        try:
            value, end = decoder.raw_decode(text, position)
        except ValueError:
            if position < len(text) and text[position] == '"':
                # Trim only the unfinished escape. json.loads handles all actual
                # JSON escapes, including unicode, without executing content.
                suffix = text[position + 1:]
                for trim in range(min(6, len(suffix)) + 1):
                    candidate = suffix[:len(suffix) - trim] if trim else suffix
                    try:
                        values[key] = json.loads('"' + candidate + '"')
                        return values
                    except ValueError:
                        continue
            break
        values[key], position = value, end
    return values


class ClarificationStream:
    """Coalesced question snapshots, never raw arguments or hidden reasoning."""
    def __init__(self):
        self.arguments, self.last_text = {}, {}
        self.last_emit = 0.0

    def update(self, chunk):
        latest = None
        for choice in chunk.get("choices", []):
            for call in choice.get("delta", {}).get("tool_calls", []):
                index = call.get("index", 0)
                text = self.arguments.get(index, "") + (call.get("function", {}).get("arguments") or "")
                if len(text) > 64_000:
                    continue
                self.arguments[index] = text
                fields = partial_object(text)
                question = fields.get("clarification")
                if fields.get("action") != "CLARIFY" or not isinstance(question, str) or not question:
                    continue
                # A unicode escape can end between the two halves of an emoji.
                # Keep that unfinished surrogate out of UTF-8/SSE until paired.
                question = question.encode("utf-8", errors="ignore").decode("utf-8")
                if not question:
                    continue
                if len(question) > 1000 or question == self.last_text.get(index):
                    continue
                if time.monotonic() - self.last_emit < .08:
                    continue
                self.last_text[index], self.last_emit = question, time.monotonic()
                latest = {"text": question, "temporary": True, "call_index": index}
        return latest


class AnswerStream:
    """Decode only answer_text as it arrives, preserving Markdown and escapes.

    Snapshots support replay/replacement while deltas let clients reveal fresh
    characters. These are temporary projections; only a checked tool result
    establishes a completed answer.
    """
    def __init__(self, *, content_json=False):
        self.content_json = content_json
        self.arguments, self.names, self.last_text = {}, {}, {}

    def update(self, chunk):
        events = []
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta", {})
            if self.content_json:
                fragments = [(0, "", delta.get("content") or "")]
            else:
                fragments = [(call.get("index", 0), call.get("function", {}).get("name") or "",
                    call.get("function", {}).get("arguments") or "")
                    for call in delta.get("tool_calls", [])]
            for index, name, fragment in fragments:
                self.names[index] = self.names.get(index, "") + name
                text = self.arguments.get(index, "") + fragment
                if len(text) > 100_000:
                    continue
                self.arguments[index] = text
                fields = partial_object(text)
                # A named ANSWER tool may put answer_text before action. Other
                # tools and free-text reasoning must never become an answer.
                known_answer = not self.content_json and self.names[index] == "answer_question"
                if (self.names[index] and not known_answer or
                        fields.get("action") not in ({None, "ANSWER"} if known_answer else {"ANSWER"})):
                    continue
                answer = fields.get("answer_text")
                if not isinstance(answer, str) or not answer or len(answer) > 12_000:
                    continue
                # Withhold an unfinished UTF-16 surrogate until its partner
                # arrives so ensure_ascii=False SSE remains valid UTF-8.
                answer = answer.encode("utf-8", errors="ignore").decode("utf-8")
                previous = self.last_text.get(index, "")
                if not answer or answer == previous:
                    continue
                self.last_text[index] = answer
                events.append({"delta": answer[len(previous):] if answer.startswith(previous) else answer,
                    "text": answer, "temporary": True, "call_index": index})
        return events
