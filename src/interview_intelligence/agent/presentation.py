"""Read only a user-facing question from incomplete top-level tool JSON."""
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
