"""Keep every Qwen reranking candidate identifiable without changing host rules.

The host still receives the original r14 array and validates its evidence. Only
the system Qwen SDK boundary uses required object keys instead of repeating an
identity field inside an array; each ranking value supplies an explicit rank.
"""
from copy import copy, deepcopy
from dataclasses import dataclass
import json
from types import SimpleNamespace

from interview_intelligence.providers.runtime import current_limits


INVALID_RESPONSE = "QWEN_IDENTITY_RESPONSE_INVALID"


def _replace(value, **updates):
    if hasattr(value, "model_copy"):
        return value.model_copy(update=updates)
    result = copy(value)
    for key, item in updates.items():
        setattr(result, key, item)
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(INVALID_RESPONSE)
        result[key] = value
    return result


@dataclass(frozen=True)
class _IdentityContract:
    field: str
    aliases: tuple[str, ...]

    @classmethod
    def prepare(cls, payload):
        response_format = payload.get("response_format") or {}
        description = response_format.get("json_schema") or {}
        name = description.get("name", "")
        if response_format.get("type") != "json_schema":
            return None, payload
        if name.startswith("rerank_"):
            field, definition = "rankings", "CandidateRank"
        elif name.startswith("scope_verify_"):
            field, definition = "verifications", "CandidateVerify"
        else:
            return None, payload
        schema = description.get("schema") or {}
        row_schema = (schema.get("$defs") or {}).get(definition) or {}
        aliases = tuple((row_schema.get("properties") or {}).get("candidate_id", {}).get("enum") or ())
        # This boundary is only for the host's explicitly enumerated Top50.
        if (not 1 <= len(aliases) <= 50 or aliases != tuple(f"c{i}" for i in range(len(aliases)))
                or field not in (schema.get("properties") or {})):
            raise ValueError("QWEN_IDENTITY_SCHEMA_INVALID")
        changed = deepcopy(payload)
        mapped_schema = changed["response_format"]["json_schema"]["schema"]
        mapped_row = mapped_schema["$defs"][definition]
        mapped_row["properties"].pop("candidate_id")
        mapped_row["required"] = [key for key in mapped_row.get("required", []) if key != "candidate_id"]
        if field == "rankings":
            mapped_row["properties"]["rank"] = {"type": "integer", "minimum": 1, "maximum": len(aliases)}
            mapped_row["required"].append("rank")
        mapped_schema["properties"][field] = {"type": "object", "additionalProperties": False,
            "properties": {alias: {"$ref": f"#/$defs/{definition}"} for alias in aliases},
            "required": list(aliases), "minProperties": len(aliases), "maxProperties": len(aliases)}
        instruction = (f"\nSDK身份格式：{field}必须为对象，严格包含schema声明的全部候选键c0至c{len(aliases)-1}，"
            "每个键恰好一次，不增删候选。候选值只使用schema字段，不含candidate_id；键就是原候选id。"
            "证据、任务和范围判断要求全部保持。")
        if field == "rankings":
            instruction += "每项rank为1起的相关性排序位置，越小越优先；其余字段按原核验规则填写。"
        messages = changed.get("messages") or []
        if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
            messages[0]["content"] += instruction
        else:
            messages.insert(0, {"role": "system", "content": instruction})
            changed["messages"] = messages
        return cls(field, aliases), changed

    def convert(self, raw):
        try:
            parsed = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(parsed, dict) or not isinstance(parsed.get(self.field), dict):
                raise ValueError(INVALID_RESPONSE)
            mapping = parsed[self.field]
            if set(mapping) != set(self.aliases):
                raise ValueError(INVALID_RESPONSE)
            ordered = []
            for ordinal, alias in enumerate(self.aliases):
                value = mapping[alias]
                if not isinstance(value, dict) or "candidate_id" in value:
                    raise ValueError(INVALID_RESPONSE)
                row = dict(value)
                rank = ordinal + 1
                if self.field == "rankings":
                    rank = row.pop("rank", None)
                    if type(rank) is not int or not 1 <= rank <= len(self.aliases):
                        raise ValueError(INVALID_RESPONSE)
                row["candidate_id"] = alias
                ordered.append((rank, ordinal, row))
            parsed[self.field] = [row for _, _, row in sorted(ordered, key=lambda item: (item[0], item[1]))]
            # Preserve r14's per-row INVALID_SCHEMA/PARTIAL handling. Validate
            # the batch envelope; the unchanged host validates every row and
            # grounds its quotes without normalizing or inventing values here.
            from interview_intelligence.search.reranker import RerankEnvelope, ScopeVerifyEnvelope
            model = RerankEnvelope if self.field == "rankings" else ScopeVerifyEnvelope
            model.model_validate(parsed)
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError, KeyError):
            raise ValueError(INVALID_RESPONSE) from None


class _IdentityStream:
    def __init__(self, original, contract):
        self.original, self.contract, self.closed = original, contract, False
        self.usage, self.model = None, None

    def __iter__(self):
        parts, template, choice, usage, finish = [], None, None, None, None
        try:
            for chunk in self.original:
                limits = current_limits.get()
                if limits:
                    limits.check()
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                    self.usage = usage
                self.model = getattr(chunk, "model", None) or self.model
                for item in chunk.choices or []:
                    template, choice = chunk, item
                    content = getattr(item.delta, "content", None)
                    if content:
                        parts.append(content)
                    finish = item.finish_reason or finish
                # Keep real usage/model visible even if conversion later fails.
                # No provider JSON is sent to the host before full validation.
                yield _replace(chunk, choices=[])
            if template is None:
                return
            limits = current_limits.get()
            if limits:
                limits.check()
            raw = "".join(parts)
            # Keep length/incomplete finish reasons intact so the original
            # host checks fail closed before parsing a partial document.
            content = self.contract.convert(raw) if finish == "stop" else ""
            delta = _replace(choice.delta, content=content)
            yield _replace(template, choices=[_replace(choice, delta=delta, finish_reason=finish)], usage=usage)
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.original.close()


class _IdentityCompletions:
    def __init__(self, original):
        self.original = original

    def create(self, **payload):
        contract, changed = _IdentityContract.prepare(payload)
        response = self.original.create(**changed)
        if contract is None:
            return response
        if payload.get("stream"):
            return _IdentityStream(response, contract)
        choices = []
        for choice in response.choices:
            if choice.finish_reason == "stop":
                content = contract.convert(choice.message.content)
                choice = _replace(choice, message=_replace(choice.message, content=content))
            choices.append(choice)
        return _replace(response, choices=choices)

    def __getattr__(self, name):
        return getattr(self.original, name)


class QwenIdentityClient:
    def __init__(self, original):
        self.original = original
        self.chat = SimpleNamespace(completions=_IdentityCompletions(original.chat.completions))

    def __getattr__(self, name):
        return getattr(self.original, name)
