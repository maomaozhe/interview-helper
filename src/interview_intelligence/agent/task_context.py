"""Compile bounded task evidence from immutable source spans, including inline prefixes."""
import re


VERSION = "task_context_v1"
SECTION = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\d+[.、\s]*)?"
                     r"(?:手撕(?:代码|算法)?|手写|算法(?:题)?|(?:AI\s*)?coding(?:部分|环节)?|笔试|讲思路)"
                     r"\s*(?:[:：（(]|$)", re.I)


def compile_task_context(text, spans):
    if not spans:
        raise ValueError("ANNOTATION_SOURCE_SPANS_REQUIRED")
    for span in spans:
        start, end = span.get("start_char"), span.get("end_char")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or text[start:end] != span.get("quote")):
            raise ValueError("ANNOTATION_SOURCE_MISMATCH")
    start, end = min(s["start_char"] for s in spans), max(s["end_char"] for s in spans)
    before = text[:start].splitlines()
    after = text[end:].splitlines()
    recent = "\n".join(before[-4:])[-800:]
    section = next((line for line in reversed(before[-20:]) if len(line) < 200 and SECTION.match(line)), None)
    if section and section not in recent:
        recent = section + "\n" + recent
    return {"context_before": recent, "context_after": "\n".join(after[:3])[:400]}
