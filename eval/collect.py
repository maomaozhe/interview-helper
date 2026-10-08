"""Collect real observations without touching corpus builds or task labels."""
from __future__ import annotations

import argparse
import os
import secrets
import time
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx

from eval.budget import EvaluationBudget
from eval.common import PIPELINES, SECTIONS, digest, inside, provenance, read_json, write_json, write_jsonl
from eval.snapshot import assert_snapshot, current_snapshot
from eval.validate_gold import require, validate_gold


def error_code(error):
    # Provider exception text can contain request bodies or credentials.
    if isinstance(error,httpx.HTTPStatusError):
        try:
            code=error.response.json().get("error",{}).get("code")
            if isinstance(code,str) and code.replace("_","").isalnum() and code.isupper(): return code
        except (ValueError,AttributeError): pass
    message = str(error)
    return message if isinstance(error, ValueError) and message.replace("_", "").isalnum() and message.isupper() else type(error).__name__


def phases(calls):
    return {key: sum(c.get(key) or 0 for c in calls) for key in ("queue_ms", "interval_ms", "provider_ms")}


def capped_client(settings):
    from openai import OpenAI
    client = OpenAI(api_key=settings.model_api_key, base_url=settings.model_base_url, max_retries=0,
        http_client=httpx.Client(trust_env=False, timeout=settings.model_request_timeout_seconds))
    create = client.chat.completions.create
    def bounded(**kwargs):
        kwargs["max_tokens"] = min(kwargs.get("max_tokens") or 16384, 16384)
        return create(**kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=bounded)), base_url=client.base_url)


def extraction_result(value):
    from interview_intelligence.ingestion.pipeline import _normalize_company, _normalize_round, _normalize_position, _parse_full_date
    data = value.model_dump(mode="json")
    result = {"document_kind": data["document_kind"], "exclusion_reason": data.get("exclusion_reason"),
              "decision": "EXCLUDED" if data["document_kind"] in {"COMPILATION", "TUTORIAL", "OTHER"} else "NEEDS_REVIEW" if data["document_kind"] == "UNKNOWN" else "INCLUDED",
              "raw_extraction": data, "sessions": [], "questions": [], "followups": []}
    if result["decision"] != "INCLUDED":
        return result
    for interview in data["interviews"]:
        key = interview["local_id"]
        metadata = interview["metadata"]
        result["sessions"].append({"id": key, "metadata": {"company": _normalize_company(metadata.get("company_raw")),
            "round": _normalize_round(metadata.get("round_raw")), "position": _normalize_position(metadata.get("position_raw"))[0],
            **{name: parsed.isoformat() if parsed else None for name in ("interview_date", "publish_date")
               for parsed in [_parse_full_date(metadata.get(name + "_raw"))]}},
            "raw_metadata": metadata, "metadata_evidence": interview["metadata_evidence"]})
        eligible = [q for q in interview["questions"] if q["evidence_kind"] == "INTERVIEW_QUESTION"]
        ids = {q["local_id"]: key + "/" + q["local_id"] for q in eligible}
        result["questions"].extend({**q, "id": ids[q["local_id"]], "session_id": key} for q in eligible)
        result["followups"].extend({"source_id": ids[f["source_local_id"]], "target_id": ids[f["target_local_id"]],
                                    "evidence_spans": f["evidence_spans"]} for f in interview["followups"]
                                  if f["source_local_id"] in ids and f["target_local_id"] in ids)
    if not result["questions"] and result["document_kind"] == "MIXED":
        result.update(decision="NEEDS_REVIEW", sessions=[])
    return result


class SQLCollector:
    """Use production listing SQL against independently exported expectations."""
    def __init__(self, database, settings, snapshot, *, user_id=None):
        self.database, self.settings, self.snapshot = database, settings, snapshot
        self.user_id = user_id or "eval-sql-" + uuid4().hex
        require(self.user_id.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
        self.signing_key = secrets.token_hex(32)

    def __call__(self, gold, calls):
        from interview_intelligence.analytics.listing import ListRequest, list_questions
        request = ListRequest.model_validate(gold["request"])
        if (request.coding_focus or request.response_form) and not request.annotation_status:
            request = request.model_copy(update={"annotation_status": self.settings.task_annotation_policy})
        rows, pages, seen = [], [], set()
        with self.database.session() as session:
            if session.bind.dialect.name == "postgresql":
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            for _ in range(10000):
                response = list_questions(session, request, signing_key=self.signing_key,
                    user_id=self.user_id, as_of=date.fromisoformat(self.snapshot["as_of"]))
                pages.append(response["meta"])
                rows.extend(response["data"])
                cursor = response["meta"]["pagination"]["next_cursor"]
                if not gold.get("all_pages") or not cursor:
                    break
                require(cursor not in seen, "PAGINATION_CURSOR_CYCLE")
                seen.add(cursor)
                request = request.model_copy(update={"cursor": cursor})
            else:
                raise ValueError("PAGINATION_LIMIT_EXCEEDED")
        return {"result": {"rows": rows, "meta": pages[0], "pages": pages, "actor": self.user_id},
                "model_calls": [], "tool_trace": [{"name": "list_questions", "parameters": gold["request"]}],
                "timings": {"tool_ms": sum(sum(p.get("timings", {}).values()) for p in pages)}}


class PublishedCollector:
    """Audit existing published output; this adapter does not run a new model."""
    def __init__(self, facts, section):
        self.facts, self.section = facts, section

    def __call__(self, gold, calls):
        if self.section == "task_labels":
            question = next(q for q in self.facts["questions"] if q["id"] == gold["occurrence_id"])
            require(bool(question.get("task_annotation")), "TASK_ANNOTATION_MISSING")
            return {"result": question["task_annotation"], "model_calls": [], "adapter": "published_output_audit"}
        require("followups" in self.facts, "SNAPSHOT_EXPORT_FOLLOWUPS_MISSING")
        matches = [s for s in self.facts["documents"] if s["source_hash"] == gold["source_hash"]]
        if gold.get("original_path"):
            matches = [s for s in matches if s["path"] == gold["original_path"]]
        require(len(matches) == 1, "PUBLISHED_SOURCE_AMBIGUOUS_OR_MISSING")
        document = matches[0]
        sessions = [s for s in self.facts["interviews"] if s["build_id"] == document["build_id"]]
        questions = [q for q in self.facts["questions"] if q["interview_id"] in {s["id"] for s in sessions}]
        result = {"decision": document["decision"], "document_kind": document["document_kind"],
                  "sessions": [{"id": s["id"], "metadata": {"company": s["company_normalized"], "round": s["round"],
                               "position": s["position_normalized"], "interview_date": s.get("interview_date"), "publish_date": s.get("publish_date")}} for s in sessions],
                  "questions": [{**q, "session_id": q["interview_id"]} for q in questions],
                  "followups": self.facts.get("followups", [])}
        result["followups"] = [f for f in result["followups"] if f["source_id"] in {q["id"] for q in questions}]
        return {"result": result, "model_calls": [], "adapter": "published_output_audit"}


class FixedVector:
    def __init__(self, vector, version):
        self.vector, self.version = vector, version

    def embed_query(self, query):
        return self.vector


class RetrievalCollector:
    """One vector and one physical index; rerank the exact Hybrid candidate list."""
    def __init__(self, database, settings, manifest, encoder, reranker):
        from interview_intelligence.search.elasticsearch import ElasticsearchRetriever
        physical = manifest.get("physical_indices", [])
        require(len(physical) == 1, "ONE_PINNED_PHYSICAL_INDEX_REQUIRED")
        require(bool(settings.elasticsearch_url), "ELASTICSEARCH_NOT_CONFIGURED")
        self.database, self.settings = database, settings
        self.encoder, self.reranker = encoder, reranker
        self.retriever = ElasticsearchRetriever(settings.elasticsearch_url, alias=physical[0])

    def __call__(self, gold, calls):
        from sqlalchemy import select
        from interview_intelligence.contracts import FilterSpec
        from interview_intelligence.domain.models import CanonicalQuestion
        from interview_intelligence.search.service import eligible_canonical_ids
        filters = FilterSpec.model_validate(gold.get("filters", {}))
        with self.database.session() as session:
            eligible = eligible_canonical_ids(session, filters)
            texts = dict(session.execute(select(CanonicalQuestion.id, CanonicalQuestion.canonical_text)
                                         .where(CanonicalQuestion.id.in_(eligible))).all())
        pool, hybrid = {}, None
        vector, vector_error = None, None
        start = time.perf_counter()
        if eligible:
            try:
                vector = self.encoder.embed_query(gold["query"]) if hasattr(self.encoder, "embed_query") else self.encoder.embed(gold["query"])
            except Exception as error:
                vector_error = error_code(error)
        embedding_ms = (time.perf_counter() - start) * 1000
        self.retriever.encoder = FixedVector(vector, self.encoder.version)
        for name in PIPELINES:
            started = time.perf_counter()
            model_start = len(calls)
            observation = {"executed_pipeline": name, "failed": False, "ranked": []}
            try:
                if name != "BM25" and vector_error:
                    raise ValueError(vector_error)
                if name == "HYBRID_RERANK":
                    require(hybrid is not None and not pool["HYBRID"]["failed"], "HYBRID_CANDIDATES_FAILED")
                    candidates = [{**item, "canonical_text": texts[item["canonical_question_id"]]} for item in hybrid]
                    observation["candidate_ids"] = [item["canonical_question_id"] for item in candidates]
                    observation["candidate_sha256"] = digest(candidates)
                    data = self.reranker.rerank(gold["query"], candidates)
                    observation["reranker_version"] = self.reranker.version
                else:
                    result = self.retriever.retrieve(gold["query"], eligible, name, 50)
                    data = result["data"]
                    observation["meta"] = result["meta"]
                    if name == "HYBRID":
                        hybrid = data
                        observation["candidate_ids"] = [item["canonical_question_id"] for item in data]
                        observation["candidate_sha256"] = digest([{**item, "canonical_text": texts[item["canonical_question_id"]]} for item in data])
                observation["ranked"] = [item["canonical_question_id"] for item in data]
                require(set(observation["ranked"]) <= set(eligible), "INELIGIBLE_RETRIEVAL_ID")
            except Exception as error:
                observation.update(failed=True, error_code=error_code(error), ranked=[])
            observation.update(elapsed_ms=(time.perf_counter() - started) * 1000, model_calls=calls[model_start:],
                               shared_embedding_ms=embedding_ms if name != "BM25" else 0)
            observation["timings"] = phases(observation["model_calls"])
            pool[name] = observation
        union = sorted({key for p in pool.values() for key in p["ranked"]})
        return {"pipelines": pool, "model_calls": calls, "shared_embedding_ms": embedding_ms,
                "candidate_pool": [{"canonical_question_id": key, "text": texts[key], "relevance": None} for key in union],
                "failed": any(p["failed"] for p in pool.values()), "timings": phases(calls)}


class ProviderCollector:
    def __init__(self, section, dataset, settings, gate, budget, *, extraction_prompt_version=None):
        self.section, self.dataset, self.settings, self.gate, self.budget = section, dataset, settings, gate, budget
        self.extraction_prompt_version = extraction_prompt_version
        self.client = capped_client(settings) if section != "task_labels" else None

    def __call__(self, gold, calls):
        settings = self.settings
        common = {"client": self.client, "api_key": settings.model_api_key, "base_url": settings.model_base_url,
                  "call_gate": self.gate, "budget": self.budget, "on_call": calls.append,
                  "timeout_seconds": settings.model_request_timeout_seconds}
        if self.section == "extraction":
            from interview_intelligence.extraction.provider import OpenAICompatibleExtractor
            from interview_intelligence.ingestion.snapshot import decode_source
            from interview_intelligence.resources import resource_path
            prompt_version = self.extraction_prompt_version or settings.extraction_prompt_version
            common["prompt_path"] = resource_path(f"prompts/{prompt_version}.md")
            provider = OpenAICompatibleExtractor(model=settings.extraction_model, stream=settings.extraction_stream,
                max_tokens=min(settings.extraction_max_tokens or 16384, 16384),
                thinking_mode=settings.extraction_thinking_mode,
                topic_ids=prompt_version in {"extract_question_v4", "extract_question_v5"}, **common)
            raw = inside(self.dataset, gold["source_path"]).read_bytes()
            text = decode_source(raw)
            extracted = provider.extract(text=text, revision_id=gold["source_hash"])
            from interview_intelligence.ingestion.pipeline import IngestService
            IngestService(None, self.dataset, self.dataset, provider)._validate_extraction(extracted, text, gold["source_hash"])
            result = extraction_result(extracted)
        elif self.section == "dedup":
            from interview_intelligence.dedup.provider import OpenAICompatibleJudge
            provider = OpenAICompatibleJudge(model=settings.judge_model,
                verify_equivalence=settings.dedup_verify_equivalence, **common)
            decision = provider.judge(gold["left"]["text"], gold["right"]["text"])
            result = {"label": decision.decision, "reason_code": decision.reason_code, "confidence": decision.confidence}
        else:
            from interview_intelligence.agent.task_annotation import model_labels
            from interview_intelligence.resources import resource_path
            # Production classifier writes only its telemetry here. Capture those
            # inserts in memory, leaving every persisted occurrence label intact.
            class AuditSession:
                def begin(self): return nullcontext()
                def add(self, entry):
                    calls.append({key: getattr(entry, key, None) for key in ("operation_type", "model", "model_revision",
                        "prompt_version", "input_tokens", "output_tokens", "latency_ms", "queue_ms", "interval_ms",
                        "provider_ms", "status", "retry_count", "error_code")})
            audit = SimpleNamespace(session=lambda: nullcontext(AuditSession()))
            batch = [SimpleNamespace(id=gold["id"], raw_question=gold["raw_question"], source_spans=gold.get("source_spans", []))]
            labels = model_labels(audit, settings, self.gate, self.budget,
                resource_path("prompts/task_annotation_v4.md").read_text(encoding="utf-8"),
                [{"occurrence_id": gold["id"], "raw_question": gold["raw_question"],
                  "context_before": gold.get("context_before"), "context_after": gold.get("context_after"),
                  "source_quotes": [s["quote"] for s in gold.get("source_spans", [])]}], batch, "task_annotation_v4")
            result = labels.items[0].model_dump(mode="json")
            if result["confidence"] < .7:
                result.update(response_form="UNKNOWN", coding_focus="UNKNOWN")
        return {"result": result, "model_calls": calls, "timings": phases(calls), "adapter": "production_provider"}


class HttpQueryCollector:
    """Require an isolated eval host with actual per-request telemetry."""
    def __init__(self, client, manifest, budget, *, allow_writes=False):
        self.client, self.budget, self.allow_writes = client, budget, allow_writes
        self.context = self.get_context()
        require(self.context["user_id"].startswith("eval-") and self.context.get("evaluation_host") is True,
                "ISOLATED_EVALUATION_HOST_REQUIRED")
        require(self.context.get("state_count") == 0 and self.context.get("conversation_count") == 0, "FRESH_EVALUATION_ACTOR_REQUIRED")
        require(self.context.get("model_gate_shared") is True, "SHARED_LINUX_MODEL_GATE_REQUIRED")
        assert_snapshot(manifest["snapshot"], self.context["snapshot"])
        self.run_id, self.seen_calls = uuid4().hex, set()
        self.snapshot = manifest["snapshot"]

    def get_context(self, request_id=None):
        response = self.client.get("/api/evaluation/context", params={"request_id": request_id} if request_id else {})
        response.raise_for_status()
        return response.json()

    def __call__(self, gold, calls):
        context = self.get_context()
        assert_snapshot(self.snapshot, context["snapshot"])
        conversation, version, previous, observations = None, None, None, []
        for index, turn in enumerate(gold.get("turns", [gold])):
            started = time.perf_counter()
            request_id = "eval-" + self.run_id + "-" + digest(gold["id"])[:12] + f"-{index}"
            payload = {"message": turn["message"], "request_id": request_id}
            if "explicit_filters" in turn:
                payload["filters"] = turn["explicit_filters"]
            if "default_page_size" in turn:
                payload["page_size"] = turn["default_page_size"]
            if conversation:
                payload.update(conversation_id=conversation, expected_version=version)
            replay = turn.get("replay_previous", False)
            if replay:
                require(previous is not None, "REPLAY_WITHOUT_PREVIOUS_REQUEST")
                payload = previous.copy(); request_id = payload["request_id"]
            observation = {"failed": False, "model_calls": [], "result": {}}
            before = None
            reserved = False
            try:
                require(not turn.get("allow_write") or self.allow_writes, "EVALUATION_WRITES_DISABLED")
                if observations and observations[-1]["failed"]:
                    raise ValueError("PREVIOUS_TURN_FAILED")
                self.budget.reserve(calls=0 if replay else self.context["query_max_model_calls"],
                                    tokens=0 if replay else self.context["query_max_tokens"])
                reserved = True
                before = self.get_context()
                assert_snapshot(self.snapshot, before["snapshot"])
                response = self.client.post("/api/questions/query", json=payload)
                response.raise_for_status()
                body = response.json()
                meta = body["meta"]
                conversation, version = meta.get("conversation_id"), meta.get("conversation_version")
                after = self.get_context(request_id)
                assert_snapshot(self.snapshot, after["snapshot"])
                observed = [c for c in after["model_calls"] if c["id"] not in self.seen_calls]
                self.seen_calls.update(c["id"] for c in observed)
                observation.update(result={"rows": body["data"], "meta": meta, "plan": meta.get("planning", {}).get("spec", {}),
                    "state": after["states"], "write_events": after["write_events"],
                    "turn_state": after.get("turn_state"),
                    "write_event_delta": after["event_count"] - before["event_count"]},
                    tool_trace=meta.get("tool_trace", []), model_calls=observed,
                    tool_invocations=after.get("tool_invocations", []), trajectory=after.get("trajectory", []))
                if after.get("run_status") == "RUNNING":
                    raise ValueError("QUERY_STILL_RUNNING")
                previous = payload.copy()
            except Exception as error:
                observation.update(failed=True, error_code=error_code(error))
                if reserved:
                    try:
                        after = self.get_context(request_id)
                        observed = [c for c in after["model_calls"] if c["id"] not in self.seen_calls]
                        self.seen_calls.update(c["id"] for c in observed)
                        observation["model_calls"] = observed
                        observation.update(tool_invocations=after.get("tool_invocations",[]),
                            trajectory=after.get("trajectory",[]),run_status=after.get("run_status"))
                        observation["tool_trace"]=[{"name":i["name"],"status":i.get("status")} for i in after.get("tool_invocations",[])]
                        observation["result"].update(state=after["states"],write_events=after["write_events"],
                            turn_state=after.get("turn_state"))
                        if before is not None:
                            observation["result"]["write_event_delta"]=after["event_count"]-before["event_count"]
                        if after.get("run_status") == "RUNNING":
                            observation["telemetry_unavailable"] = True
                    except Exception:
                        observation["telemetry_unavailable"] = True
            finally:
                if reserved:
                    observed = observation["model_calls"]
                    complete = not observation.get("telemetry_unavailable") and all(type(c.get("input_tokens")) is int
                        and (type(c.get("output_tokens")) is int or c.get("operation_type") == "EMBEDDING") for c in observed)
                    self.budget.reconcile(calls=len(observed) if not observation.get("telemetry_unavailable") else self.context["query_max_model_calls"],
                        tokens=sum(c["input_tokens"] + (c.get("output_tokens") or 0) for c in observed) if complete else None)
                calls.extend(observation["model_calls"])
                observation.update(elapsed_ms=(time.perf_counter() - started) * 1000, timings=phases(observation["model_calls"]))
                observation["timings"]["tool_ms"] = observation["result"].get("meta", {}).get("timings", {}).get("tool_ms")
                observations.append(observation)
        return {"turns": observations, "failed": any(t["failed"] for t in observations), "model_calls": calls,
                "telemetry_unavailable": any(t.get("telemetry_unavailable") for t in observations),
                "snapshot": self.get_context()["snapshot"], "timings": phases(calls), "actor": self.context["user_id"]}


def collect_rows(golds, collector, snapshot, observe_snapshot, *, on_row=None):
    rows = []
    for gold in golds:
        started, calls = time.perf_counter(), []
        row = {"id": gold["id"], "snapshot": snapshot, "failed": False}
        try:
            assert_snapshot(snapshot, observe_snapshot())
            row.update(collector(gold, calls))
            assert_snapshot(snapshot, observe_snapshot())
        except Exception as error:
            row.update(failed=True, error_code=error_code(error), model_calls=calls)
            if error_code(error) == "SNAPSHOT_CHANGED":
                # Mark a revision crossing invalid at score time, never attach
                # expected metadata to observations made against another revision.
                row["snapshot"] = observe_snapshot()
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000
        rows.append(row)
        if on_row:
            on_row(rows)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--section", choices=SECTIONS, required=True)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--review-policy", choices=("human", "delegated_agent"), default="human")
    parser.add_argument("--adapter", choices=("sql", "published", "provider", "retrieval", "http"), required=True)
    parser.add_argument("--snapshot-export", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18002")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--extraction-prompt-version", choices=("extract_question_v2", "extract_question_v3", "extract_question_v4", "extract_question_v5"))
    parser.add_argument("--extraction-thinking-mode", choices=("auto", "enabled", "disabled"))
    args = parser.parse_args()
    partial = args.output.with_suffix(".partial.jsonl")
    require(not args.output.exists() and not partial.exists(), "PREDICTION_OUTPUT_ALREADY_EXISTS")
    manifest = validate_gold(args.dataset, args.section, split=args.split, require_labels=args.split == "test", review_policy=args.review_policy)
    golds = [g for g in manifest[args.section] if g["split"] == args.split]
    from interview_intelligence.config import load_settings
    from interview_intelligence.domain.models import create_database
    settings = load_settings()
    if args.extraction_thinking_mode is not None:
        require(args.section == "extraction" and args.adapter == "provider", "thinking override requires provider extraction")
        settings = settings.model_copy(update={"extraction_thinking_mode": args.extraction_thinking_mode})
    budget, client = None, None
    snapshot = manifest.get("snapshot", {})
    if args.adapter == "http":
        require(args.section == "routing", "http adapter supports routing")
        budget = EvaluationBudget(args.max_calls, args.max_tokens)
        client = httpx.Client(base_url=args.base_url, timeout=180, trust_env=False)
        collector = HttpQueryCollector(client, manifest, budget, allow_writes=args.allow_writes)
        observe = lambda: collector.get_context()["snapshot"]
    elif args.adapter == "published":
        require(args.section in {"extraction", "task_labels"} and args.snapshot_export, "published adapter needs export and extraction/task_labels section")
        facts = read_json(args.snapshot_export / "facts.json")
        stamp = read_json(args.snapshot_export / "manifest.json")
        require(digest(facts) == stamp["facts_sha256"], "SNAPSHOT_EXPORT_HASH_MISMATCH")
        assert_snapshot(snapshot, facts["snapshot"])
        snapshot = facts["snapshot"]
        collector = PublishedCollector(facts, args.section)
        observe = lambda: facts["snapshot"]
    else:
        database = create_database(settings.database_url, create_tables=False)
        actual = current_snapshot(database, settings, as_of=snapshot.get("as_of"))
        assert_snapshot(snapshot, actual); snapshot = actual
        observe = lambda: current_snapshot(database, settings, as_of=snapshot["as_of"])
        if args.adapter == "sql":
            require(args.section == "sql", "sql adapter supports sql")
            collector = SQLCollector(database, settings, snapshot)
        else:
            # Windows locks and Linux locks do not coordinate. All model-backed
            # evaluation must join the production Linux runtime volume.
            require(os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock"), "SHARED_LINUX_MODEL_GATE_REQUIRED")
            require(settings.model_api_key and settings.model_base_url, "MODEL_CONFIGURATION_INCOMPLETE")
            from interview_intelligence.providers.gate import ModelCallGate
            gate = ModelCallGate(
                settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
            budget = EvaluationBudget(args.max_calls, args.max_tokens)
            if args.adapter == "provider":
                require(args.section in {"extraction", "task_labels", "dedup"}, "unsupported provider task")
                collector = ProviderCollector(args.section, args.dataset, settings, gate, budget,
                                              extraction_prompt_version=args.extraction_prompt_version)
            else:
                require(args.section == "retrieval" and snapshot["indexed_revision"] == snapshot["corpus_revision"], "RETRIEVAL_SNAPSHOT_NOT_READY")
                from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder
                from interview_intelligence.search.reranker import LLMReranker
                encoder_type = ArkMultimodalEncoder if settings.embedding_model == "doubao-embedding-vision" else OpenAICompatibleEncoder
                # Per-row collectors install callbacks before starting calls.
                collector = RetrievalCollector(database, settings, manifest,
                    encoder_type(model=settings.embedding_model, dimension=settings.embedding_dimension, api_key=settings.model_api_key,
                                 base_url=settings.model_base_url, call_gate=gate, budget=budget),
                    LLMReranker(model=settings.reranker_model, api_key=settings.model_api_key, base_url=settings.model_base_url,
                                client=capped_client(settings), call_gate=gate, budget=budget))
                original = collector
                def with_callbacks(gold, calls):
                    original.encoder.on_call = original.reranker.on_call = calls.append
                    return original(gold, calls)
                collector = with_callbacks
    try:
        def checkpoint(rows):
            write_jsonl(partial, rows)
            print({"completed": len(rows), "failed": sum(r["failed"] for r in rows)}, flush=True)
        rows = collect_rows(golds, collector, snapshot, observe, on_row=checkpoint)
        write_jsonl(args.output, rows)
        write_json(args.output.with_suffix(".collection.json"), {"adapter": args.adapter, "section": args.section,
            "split": args.split, "review_policy": args.review_policy, "snapshot": snapshot, "samples": len(rows), "failed": sum(r["failed"] for r in rows),
            "budget": budget.summary() if budget else {"model_calls": 0}, "provenance": provenance(),
            "predictions_sha256": digest(args.output.read_bytes())})
    finally:
        if client: client.close()
    print({"output": str(args.output), "samples": len(rows), "failed": sum(r["failed"] for r in rows)})


if __name__ == "__main__":
    main()
