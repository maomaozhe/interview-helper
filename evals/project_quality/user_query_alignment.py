"""Replay historical user queries and assess source-backed scope alignment.

Gold is reviewed and frozen independently before collection. An unreviewed
result stays unreviewed, and a positive canonical never repairs a wrong source.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from eval.budget import EvaluationBudget
from eval.collect import HttpQueryCollector
from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from eval.snapshot import assert_snapshot
from eval.validate_gold import require


def load_gold(dataset):
    from evals.agent_gold.user_query_source_20261015 import validate_dataset
    return validate_dataset(dataset)


def seal_evaluator(root):
    """Freeze the collector, validator and scorer independently of the API image."""
    require(not (root / "evaluator-seal.json").exists(), "EVALUATOR_ALREADY_SEALED")
    files = {}
    for folder in ("src", "eval", "evals", "config", "prompts"):
        for path in sorted(Path(folder).rglob("*")):
            if (not path.is_file() or path.suffix not in {".py", ".md", ".json", ".yaml"}
                    or {"__pycache__", "reports", "results"}.intersection(path.parts)):
                continue
            target = root / "evaluator" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            files[path.as_posix()] = digest(path.read_bytes())
    write_json(root / "evaluator-seal.json", {"files": files, "sha256": digest(files),
        "created_at": datetime.now(timezone.utc).isoformat()})


def evaluator_identity(root):
    seal = read_json(root / "evaluator-seal.json")
    for name, expected in seal["files"].items():
        require(digest((root / "evaluator" / name).read_bytes()) == expected, "SEALED_EVALUATOR_CHANGED")
    require(digest(Path(__file__).read_bytes()) == seal["files"]["evals/project_quality/user_query_alignment.py"],
            "RUN_SEALED_EVALUATOR_REQUIRED")
    return seal["sha256"]


def prepare_runtime(dataset, root, *, actor=None):
    """Create a fresh, reproducible local replay host without starting services."""
    bundle = load_gold(dataset)
    require(not root.exists(), "QUERY_RUNTIME_ALREADY_EXISTS")
    actor = actor or "eval-user-query-gold-" + uuid4().hex[:12]
    require(actor.startswith("eval-"), "EVALUATION_ACTOR_REQUIRED")
    files = {}
    for folder in ("src", "eval", "evals", "prompts", "config", "services/pi-agent"):
        for path in sorted(Path(folder).rglob("*")):
            if (not path.is_file() or path.suffix not in {".py", ".md", ".json", ".yaml", ".mjs"}
                    or {"__pycache__", "reports", "results"}.intersection(path.parts)):
                continue
            target = root / "runtime" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            files[path.as_posix()] = digest(path.read_bytes())
    as_of = bundle["manifest"]["snapshot"]["as_of"]
    write_json(root / "runtime-seal.json", {"schema": "user_query_runtime_v1", "files": files,
        "runtime_sha256": digest(files), "actor": actor, "as_of": as_of,
        "snapshot": bundle["manifest"]["snapshot"], "production_writes": 0})
    runtime = (root / "runtime").resolve()
    host_path = runtime.as_posix()
    if runtime.drive:
        host_path = "/mnt/" + runtime.drive[0].lower() + host_path[2:]
    override = {"services": {
        "evaluation-api": {"environment": {"MULTI_TENANT_ENABLED": "false", "LOCAL_USER_ID": actor,
            "QUERY_PROMPT_VERSION": "query_agent_v13"},
            "command": ["python", "-m", "eval.serve", "--host", "0.0.0.0", "--port", "8000", "--as-of", as_of],
            "volumes": [f"{host_path}/{folder}:/app/{folder}:ro" for folder in ("src", "eval", "prompts", "config")]},
        "evaluation-pi": {"volumes": [f"{host_path}/services/pi-agent:/app/services/pi-agent:ro"]}}}
    write_json(root / "compose.sealed.json", override)
    seal_evaluator(root)
    print({"runtime": str(root), "actor": actor, "gold_sha256": digest(bundle),
           "next": "Set EVAL_USER_ID to actor; start compose.evaluation.yaml with this compose.sealed.json override, then collect using --runtime-root."}, flush=True)


def role_sets(scope, expected):
    boundary = set(scope.get("boundary_gold_ids", [])) | set(expected.get("case_boundary_gold_ids", []))
    negative = (set(scope.get("negative_gold_ids", []))
                | set(expected.get("hard_negative_gold_ids", scope.get("hard_negative_gold_ids", [])))
                | set(expected.get("excluded_positive_gold_ids", [])))
    return {
        "POSITIVE": set(expected.get("positive_gold_ids", scope.get("positive_gold_ids", []))) - negative - boundary,
        "NEGATIVE": negative - boundary,
        "BOUNDARY": boundary,
    }


def same_source_span(source, span):
    if source.get("quote") != span.get("quote"):
        return False
    for field in ("start_char", "end_char", "start_line", "end_line"):
        if field in source and field in span and source[field] != span[field]:
            return False
    if source.get("occurrence_id"):
        return True
    # A revision and a generic repeated quote cannot identify an occurrence.
    # LIST's source_summary supplies positions even though it omits its ID.
    return (all(field in source and field in span and source[field] == span[field]
                for field in ("start_char", "end_char"))
            or "start_line" in source and "start_line" in span
            and source["start_line"] == span["start_line"])


def result_set_identity(token):
    """Compare runtime-produced scope bodies, ignoring token refresh time.

    This is diagnostic identity comparison, not signature authentication. The
    isolated host validates its signed tokens; the evaluator lacks that key.
    """
    if not isinstance(token, str):
        return None
    try:
        body, _ = token.rsplit(".", 1)
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if not isinstance(value, dict):
            return None
        return {k: v for k, v in value.items() if k != "issued_at"}
    except (ValueError, UnicodeError):
        return None


def assess_item(item, questions, roles):
    """Keep identity-level relevance and actual displayed-source support separate."""
    canonical = item.get("canonical_question_id") or item.get("key")
    candidates = [q for q in questions if q.get("canonical_question_id") == canonical and canonical]
    by_occurrence = {q["occurrence_id"]: q for q in candidates if q.get("occurrence_id")}
    ids = {q["id"] for q in candidates}
    canonical_role = next((role for role in ("POSITIVE", "BOUNDARY", "NEGATIVE")
                           if ids & roles[role]), "UNREVIEWED")
    sources = item.get("sources", [])
    if not sources and item.get("source_summary"):
        summary = item["source_summary"]
        sources = [{"revision_id": summary.get("revision_id"), **span}
                   for span in summary.get("spans", [])]
    witnesses = []
    for source in sources:
        matched = []
        for question in candidates:
            if source.get("occurrence_id"):
                if by_occurrence.get(source["occurrence_id"]) != question:
                    continue
            elif not source.get("revision_id"):
                continue
            revision = question.get("source_revision_id") or question.get("revision_id")
            if source.get("revision_id") and revision and source["revision_id"] != revision:
                continue
            if source.get("raw_file_hash") and source["raw_file_hash"] != question.get("source_hash"):
                continue
            spans = question.get("source_spans", [])
            if not any(same_source_span(source, span) for span in spans):
                continue
            matched.append(question)
        labels = {role for role, members in roles.items() if any(q["id"] in members for q in matched)}
        witnesses.append({"occurrence_id": source.get("occurrence_id"),
                          "gold_ids": [q["id"] for q in matched],
                          "literal_source_grounded": bool(matched),
                          "role": next(iter(labels)) if len(labels) == 1 else
                          "MIXED" if labels else "UNVERIFIED"})
    supported = bool(witnesses) and all(w["role"] == "POSITIVE" for w in witnesses)
    return {"canonical_question_id": canonical, "canonical_role": canonical_role,
            "positive_gold_ids": sorted(ids & roles["POSITIVE"]),
            "literal_source_grounded": bool(witnesses) and all(w["literal_source_grounded"] for w in witnesses),
            "source_scope_accepted": supported,
            "source_support_pass": supported, "witnesses": witnesses}


def assess_turn(turn, observation, bundle):
    expected = turn["expected"]
    result = observation.get("result", {})
    meta, plan = result.get("meta", {}), result.get("plan", {})
    action = plan.get("action") or meta.get("planning", {}).get("spec", {}).get("action")
    filters = meta.get("applied_filters", plan.get("filters") or {})
    explicit = turn.get("explicit_filters", {})
    filters_pass = (all(filters.get(k) == v for k, v in expected.get("required_filters", {}).items())
                    and not any(filters.get(k) is not None and k not in explicit
                                for k in expected.get("forbidden_inferred_filters", [])))
    accepted_sort = expected.get("accepted_sort", [])
    actual_sort = meta.get("sort", plan.get("sort"))
    sort_pass = not accepted_sort or actual_sort in accepted_sort
    scope = bundle["scopes"].get(expected.get("scope_id"), {})
    roles = role_sets(scope, expected)
    rows = result.get("rows", [])
    items = [assess_item(row, bundle["questions"], roles) for row in rows]
    family_mapping = scope.get("family_by_gold_id", {})
    families = sorted({family_mapping[identity] for item in items for witness in item["witnesses"]
                       if witness["role"] == "POSITIVE" for identity in witness["gold_ids"]
                       if identity in roles["POSITIVE"] and identity in family_mapping})
    counts = Counter(item["canonical_role"] for item in items)
    positive_canonical = {q.get("canonical_question_id") for q in bundle["questions"]
                          if q["id"] in roles["POSITIVE"] and q.get("canonical_question_id")}
    returned_ids = {item["canonical_question_id"] for item in items}
    unique = len(returned_ids) == len(items)
    certification = expected.get("expected_certification", expected.get("scope_certification"))
    certification_blocked = certification in {"MISSING_ANNOTATION", "NOT_CERTIFIED"}
    certified = scope.get("reference_completeness") == "corpus_occurrence_census" and not certification_blocked
    completeness_pass = None
    if expected.get("requested_completeness") == "all":
        completeness_pass = certified and returned_ids == positive_canonical and unique
    semantic = action in {"SEARCH", "LIST", "NEXT"}
    source_pass = all(item["source_support_pass"] for item in items)
    results_pass = (counts["POSITIVE"] >= expected.get("min_positive_results", 0)
                    and counts["NEGATIVE"] <= expected.get("max_negative_results", 0)
                    and counts["BOUNDARY"] == counts["UNREVIEWED"] == 0 and unique)
    # FIND cannot pass vacuously because the caller left a minimum unspecified.
    if semantic and roles["POSITIVE"] and not items:
        results_pass = False
    if semantic and expected.get("require_source_support", True):
        results_pass = results_pass and source_pass
    if completeness_pass is not None:
        results_pass = results_pass and completeness_pass
    checks = {"request_succeeded": not observation.get("failed", False),
              "action": action in expected["accepted_actions"],
              "filters": filters_pass, "sort": sort_pass}
    if semantic:
        checks["results"] = results_pass
    require_certification = (action == "COUNT" or expected.get("requested_completeness") == "all"
                             or expected.get("require_scope_certification", False)
                             or certification == "MISSING_ANNOTATION" and action in {"LIST", "NEXT"})
    if certification_blocked and require_certification:
        checks["scope_certified"] = False
    if semantic and expected.get("min_distinct_families", 0):
        checks["family_coverage"] = len(families) >= expected["min_distinct_families"]
    if action in {"LIST", "NEXT"} and accepted_sort == ["frequency"]:
        frequencies = [row.get("occurrence_count") for row in rows]
        checks["frequency_order"] = (all(type(n) is int and n >= 0 for n in frequencies)
                                     and all(a >= b for a, b in zip(frequencies, frequencies[1:])))
    if action == "COUNT":
        expected_counts = expected.get("expected_counts")
        actual_counts = meta.get("counts", {})
        checks["count_certified"] = bool(certified and expected_counts and all(
            type(actual_counts.get(key)) is int and actual_counts[key] == value for key, value in expected_counts.items()))
    return {"case_id": turn["case_id"], "turn_index": turn["turn_index"],
            "message": turn["message"], "expected": expected, "action": action,
            "checks": checks, "aligned": all(checks.values()), "returned": len(items),
            "canonical_role_counts": dict(counts), "source_support_pass": source_pass,
            "literal_source_grounded": all(item["literal_source_grounded"] for item in items),
            "reference_completeness": scope.get("reference_completeness", "panel_only"),
            "known_positive_retrieved": len(returned_ids & positive_canonical),
            "canonical_known_positive_retrieved": len(returned_ids & positive_canonical),
            "source_supported_positive_retrieved": sum(item["canonical_role"] == "POSITIVE"
                and item["source_support_pass"] for item in items),
            "indexed_positive_canonicals": len(positive_canonical),
            "completeness_pass": completeness_pass, "items": items,
            "distinct_families": len(families), "family_ids": families,
            "review_required": bool(counts["BOUNDARY"] or counts["UNREVIEWED"] or any(w["role"] in {"BOUNDARY", "UNVERIFIED", "MIXED"}
                for item in items for w in item["witnesses"])),
            "scope_certification_available": certified,
            "scope_certification_required": bool(require_certification),
            "certification_blocked": bool(require_certification and not certified),
            "error_code": observation.get("error_code"),
            "elapsed_ms": observation.get("elapsed_ms")}


def assess_case(case, saved, bundle):
    """Assess continuation against the same case's actual prior responses."""
    require(len(saved["turns"]) == len(case["turns"]), "QUERY_TURNS_MISSING")
    results = []
    for index, (turn, observation) in enumerate(zip(case["turns"], saved["turns"])):
        assessed = assess_turn(turn, observation, bundle)
        expected, checks = turn["expected"], assessed["checks"]
        if expected.get("requires_prior_results"):
            checks["prior_results"] = bool(results and results[-1]["returned"]
                and results[-1]["checks"]["request_succeeded"])
        if expected.get("no_repeat_previous_results"):
            previous = {item["canonical_question_id"] for prior in results for item in prior["items"]}
            checks["new_page_results"] = not previous.intersection(
                item["canonical_question_id"] for item in assessed["items"])
        if expected.get("requires_conversation_continuity"):
            previous_meta = saved["turns"][index - 1].get("result", {}).get("meta", {}) if index else {}
            current_meta = observation.get("result", {}).get("meta", {})
            old_version, new_version = previous_meta.get("conversation_version"), current_meta.get("conversation_version")
            checks["conversation_continuity"] = bool(previous_meta.get("conversation_id")
                and current_meta.get("conversation_id") == previous_meta["conversation_id"]
                and type(old_version) is int and type(new_version) is int
                and new_version == old_version + (0 if turn.get("replay_previous") else 1))
        inherited = expected.get("inherits_scope_from_turn")
        if inherited is not None:
            require(type(inherited) is int and 0 <= inherited < index, "INVALID_INHERITED_SCOPE_TURN")
            previous_result = saved["turns"][inherited].get("result", {})
            previous_plan, previous_meta = previous_result.get("plan", {}), previous_result.get("meta", {})
            current_result = observation.get("result", {})
            current_plan, current_meta = current_result.get("plan", {}), current_result.get("meta", {})
            old_filters = previous_meta.get("applied_filters", previous_plan.get("filters") or {})
            new_filters = current_meta.get("applied_filters", current_plan.get("filters") or {})
            explicit = turn.get("explicit_filters", {})
            checks["inherited_scope"] = (all(new_filters.get(k) == v for k, v in old_filters.items() if k not in explicit)
                and all(v is None or k in old_filters or k in explicit for k, v in new_filters.items())
                and current_plan.get("semantic_task") == previous_plan.get("semantic_task")
                and current_plan.get("top_n") == previous_plan.get("top_n"))
            if assessed["action"] == "NEXT" and previous_meta.get("result_set_id"):
                old_scope = result_set_identity(previous_meta["result_set_id"])
                checks["same_result_set"] = old_scope is not None and old_scope == result_set_identity(current_meta.get("result_set_id"))
            if expected.get("accepted_sort") == ["frequency"] and assessed["action"] == "NEXT":
                prior_rows = saved["turns"][index - 1].get("result", {}).get("rows", []) if index else []
                current_rows = current_result.get("rows", [])
                old_last = prior_rows[-1].get("occurrence_count") if prior_rows else None
                new_first = current_rows[0].get("occurrence_count") if current_rows else None
                checks["frequency_page_boundary"] = (type(old_last) is int and type(new_first) is int
                                                       and old_last >= new_first)
        assessed["aligned"] = all(checks.values())
        results.append(assessed)
    return results


def load_checkpoint(parent, bundle, seal, *, base_url, max_calls, max_tokens):
    """Restore only complete case prefixes, retaining original evidence and cost."""
    protocol = read_json(parent / "protocol.json")
    require(protocol["gold_sha256"] == digest(bundle), "RESUME_GOLD_MISMATCH")
    require(protocol["runtime_sha256"] == seal["runtime_sha256"]
            and protocol["base_url"] == base_url, "RESUME_RUNTIME_MISMATCH")
    require((protocol["max_calls"], protocol["max_tokens"]) == (max_calls, max_tokens),
            "RESUME_BUDGET_CHANGED")
    old_root = Path(protocol["runtime_root"])
    old_seal = read_json(old_root / "evaluator-seal.json")
    require(digest(old_seal["files"]) == protocol["evaluator_sha256"], "RESUME_EVALUATOR_MISMATCH")
    for name, expected in old_seal["files"].items():
        require(digest((old_root / "evaluator" / name).read_bytes()) == expected,
                "RESUME_EVALUATOR_CHANGED")
    before = read_json(parent / "before.json")
    require(before["user_id"] == seal["actor"], "RESUME_ACTOR_MISMATCH")
    require(all(type(before.get(key)) is int and before[key] > 0
                for key in ("query_max_model_calls", "query_max_tokens")), "RESUME_QUERY_LIMITS_INVALID")
    assert_snapshot(bundle["manifest"]["snapshot"], before["snapshot"])
    rows = read_jsonl(parent / "predictions.partial.jsonl")
    cases = bundle["cases"]
    require(0 < len(rows) < len(cases), "RESUME_INCOMPLETE_PREFIX_REQUIRED")
    require([r["id"] for r in rows] == [c["id"] for c in cases[:len(rows)]], "RESUME_CASE_PREFIX_MISMATCH")
    conversations, seen, requests = set(), set(), {}
    charged_calls = charged_tokens = 0
    for case, row in zip(cases, rows):
        assert_snapshot(bundle["manifest"]["snapshot"], row["snapshot"])
        require(row["actor"] == seal["actor"] and not row["failed"], "RESUME_UNFINISHED_CASE")
        require(len(row["turns"]) == len(case["turns"]), "RESUME_UNFINISHED_CASE")
        case_conversation = None
        flat_calls = []
        for expected, turn in zip(case["turns"], row["turns"]):
            meta = turn.get("result", {}).get("meta", {})
            require(not turn.get("failed") and turn.get("run_status") == "SUCCEEDED"
                    and meta.get("request_id") and meta.get("conversation_id"), "RESUME_UNFINISHED_TURN")
            require(case_conversation in {None, meta["conversation_id"]}, "RESUME_CONVERSATION_CHANGED")
            case_conversation = meta["conversation_id"]
            request = {"request_id": meta["request_id"], "conversation_id": case_conversation,
                "run_id": meta.get("run_id"), "message": expected["message"],
                "model_calls": turn["model_calls"]}
            if meta["request_id"] in requests:
                require(expected.get("replay_previous") and not turn["model_calls"], "RESUME_REQUEST_REUSED")
            else:
                requests[meta["request_id"]] = request
            calls = turn["model_calls"]
            require(all(c.get(key) is None or type(c[key]) is int and c[key] >= 0
                for c in calls for key in ("input_tokens", "output_tokens")), "RESUME_TOKEN_USAGE_INVALID")
            require(all(c.get("id") and c["id"] not in seen for c in calls)
                    and len({c["id"] for c in calls}) == len(calls), "RESUME_DUPLICATE_MODEL_CALL")
            seen.update(c["id"] for c in calls)
            flat_calls.extend(calls)
            complete = not turn.get("telemetry_unavailable") and all(type(c.get("input_tokens")) is int
                and (type(c.get("output_tokens")) is int or c.get("operation_type") == "EMBEDDING") for c in calls)
            charged_calls += before["query_max_model_calls"] if turn.get("telemetry_unavailable") else len(calls)
            charged_tokens += (sum(c["input_tokens"] + (c.get("output_tokens") or 0) for c in calls)
                if complete else before["query_max_tokens"])
        require(row["model_calls"] == flat_calls, "RESUME_TELEMETRY_MISMATCH")
        require(case_conversation not in conversations, "RESUME_CASE_CONVERSATION_REUSED")
        conversations.add(case_conversation)
    require(charged_calls <= max_calls and charged_tokens <= max_tokens, "EVALUATION_BUDGET_EXCEEDED")
    identity = {"parent": str(parent), "partial_sha256": digest((parent / "predictions.partial.jsonl").read_bytes()),
        "protocol_sha256": digest(protocol), "parent_evaluator_sha256": protocol["evaluator_sha256"],
        "before_sha256": digest(before), "query_max_model_calls": before["query_max_model_calls"],
        "query_max_tokens": before["query_max_tokens"],
        "completed_cases": len(rows), "actor": seal["actor"], "conversation_count": len(conversations),
        "charged_calls": charged_calls, "charged_tokens": charged_tokens}
    return rows, seen, list(requests.values()), identity


def verify_checkpoint_requests(client, requests):
    for request in requests:
        current = client.get("/api/evaluation/context", params={"request_id": request["request_id"]}).raise_for_status().json()
        require(current.get("run_status") == "SUCCEEDED", "RESUME_PERSISTED_TURN_NOT_SUCCEEDED")
        require(current.get("model_calls") == request["model_calls"], "RESUME_PERSISTED_TELEMETRY_CHANGED")
        receipt = client.get("/api/query/receipts/" + request["request_id"]).raise_for_status().json()["data"]
        require(receipt.get("status") == "SUCCEEDED" and all(receipt.get(key) == request[key]
            for key in ("request_id", "run_id", "conversation_id")), "RESUME_PERSISTED_IDENTITY_CHANGED")
        conversation = client.get("/api/conversations/" + request["conversation_id"],
            params={"limit": 50}).raise_for_status().json()["data"]
        matches = [t for t in conversation["turns"] if t.get("request_id") == request["request_id"]]
        require(len(matches) == 1 and matches[0].get("status") == "SUCCEEDED"
                and matches[0].get("run_id") == request["run_id"]
                and matches[0].get("message") == request["message"], "RESUME_PERSISTED_MESSAGE_CHANGED")


def collect(dataset, output, *, base_url, runtime_root, max_calls, max_tokens, resume_from=None):
    evaluator_sha256 = evaluator_identity(runtime_root)
    bundle = load_gold(dataset)
    require(not output.exists(), "QUERY_ATTEMPT_ALREADY_EXISTS")
    output.mkdir(parents=True)
    seal = read_json(runtime_root / "runtime-seal.json")
    for name, expected in seal["files"].items():
        require(digest((runtime_root / "runtime" / name).read_bytes()) == expected, "SEALED_RUNTIME_CHANGED")
    manifest = bundle["manifest"]
    budget = EvaluationBudget(max_calls, max_tokens)
    rows, seen, requests, checkpoint = [], set(), [], None
    if resume_from is not None:
        rows, seen, requests, checkpoint = load_checkpoint(resume_from, bundle, seal,
            base_url=base_url, max_calls=max_calls, max_tokens=max_tokens)
        budget.used_calls, budget.used_tokens = checkpoint["charged_calls"], checkpoint["charged_tokens"]
    with httpx.Client(base_url=base_url, trust_env=False, timeout=195,
                      headers={"X-Usage-Purpose": "evaluation"}) as client:
        before = client.get("/api/evaluation/context").raise_for_status().json()
        require(before["user_id"] == seal["actor"], "SEALED_ACTOR_MISMATCH")
        require(before["as_of_frozen"], "FROZEN_EVALUATION_CLOCK_REQUIRED")
        if checkpoint:
            require(all(before.get(k) == checkpoint[k] for k in ("query_max_model_calls", "query_max_tokens")),
                    "RESUME_QUERY_LIMITS_CHANGED")
        verify_checkpoint_requests(client, requests)
        collector = HttpQueryCollector(client, manifest, budget, resume_checkpoint=checkpoint)
        collector.seen_calls.update(seen)
        write_json(output / "before.json", before)
        write_json(output / "protocol.json", {"dataset": str(dataset), "gold_sha256": digest(bundle),
            "runtime_sha256": seal["runtime_sha256"], "base_url": base_url,
            "evaluator_sha256": evaluator_sha256, "runtime_root": str(runtime_root),
            "max_calls": max_calls, "max_tokens": max_tokens,
            "resume": checkpoint,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scope": "Agent-reviewed seen-source user-query diagnostic; no production label writes."})
        write_jsonl(output / "predictions.partial.jsonl", rows)
        for case in bundle["cases"][len(rows):]:
            calls = []
            observation = collector(case, calls)
            rows.append({"id": case["id"], "snapshot": manifest["snapshot"], **observation})
            write_jsonl(output / "predictions.partial.jsonl", rows)
            print({"case": case["id"], "turns": len(observation["turns"]),
                   "failed": observation["failed"], "budget": budget.summary()}, flush=True)
        after = client.get("/api/evaluation/context").raise_for_status().json()
        assert_snapshot(manifest["snapshot"], after["snapshot"])
        require(after["state_count"] == after["event_count"] == 0, "UNEXPECTED_REVIEW_WRITES")
        write_json(output / "after.json", after)
    write_jsonl(output / "predictions.jsonl", rows)
    write_json(output / "collection.json", {"budget": budget.summary(), "samples": len(rows),
        "predictions_sha256": digest((output / "predictions.jsonl").read_bytes()),
        "gold_sha256": digest(bundle), "runtime_sha256": seal["runtime_sha256"],
        "evaluator_sha256": evaluator_sha256, "protocol_sha256": digest(read_json(output / "protocol.json"))})
    score(dataset, output)


def score(dataset, output):
    bundle = load_gold(dataset)
    protocol = read_json(output / "protocol.json")
    require(digest(bundle) == protocol["gold_sha256"], "QUERY_GOLD_CHANGED")
    observations = read_jsonl(output / "predictions.jsonl")
    receipt = read_json(output / "collection.json")
    require(digest((output / "predictions.jsonl").read_bytes()) == receipt["predictions_sha256"],
            "QUERY_PREDICTIONS_CHANGED")
    require(receipt["protocol_sha256"] == digest(protocol), "QUERY_PROTOCOL_CHANGED")
    require(evaluator_identity(Path(protocol["runtime_root"])) == protocol["evaluator_sha256"]
            == receipt["evaluator_sha256"], "QUERY_SCORER_IDENTITY_MISMATCH")
    require(receipt["gold_sha256"] == protocol["gold_sha256"]
            and receipt["runtime_sha256"] == protocol["runtime_sha256"], "QUERY_COLLECTION_IDENTITY_MISMATCH")
    observed = {row["id"]: row for row in observations}
    require(len(observed) == len(observations) == len(bundle["cases"]), "FULL_QUERY_SUITE_REQUIRED")
    require(set(observed) == {case["id"] for case in bundle["cases"]}, "QUERY_CASE_SET_CHANGED")
    results = []
    for case in bundle["cases"]:
        saved = observed[case["id"]]
        assert_snapshot(bundle["manifest"]["snapshot"], saved["snapshot"])
        results.extend(assess_case(case, saved, bundle))
    summary = {"turns": len(results), "aligned": sum(r["aligned"] for r in results),
               "failed_checks": dict(Counter(k for r in results for k, passed in r["checks"].items() if not passed)),
               "review_required_turns": sum(r["review_required"] for r in results),
               "certification_blocked_turns": sum(r["certification_blocked"] for r in results),
               "human_verified": False, "release_eligible": False}
    write_json(output / "alignment.json", {"summary": summary, "turns": results})
    lines = ["# 历史用户查询与真实面经对齐", "", f"逐轮对齐：{summary['aligned']}/{summary['turns']}。Agent审核、已见回归，不代表整体发布通过。", "",
             "| 用例 | 原问 | 返回 | 对齐 | 失败检查 |", "| --- | --- | ---: | --- | --- |"]
    for row in results:
        message = row["message"].replace("|", "／").replace("\n", " ")
        failures = ", ".join(k for k, v in row["checks"].items() if not v)
        lines.append(f"| {row['case_id']}:{row['turn_index']} | {message} | {row['returned']} | {row['aligned']} | {failures} |")
    (output / "alignment.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary, flush=True)


def audit_reporting(dataset, source_run, output):
    """Clarify reporting fields without rewriting frozen first-pass judgments."""
    require(not output.exists(), "QUERY_REPORTING_AUDIT_ALREADY_EXISTS")
    bundle = load_gold(dataset)
    protocol, receipt = read_json(source_run / "protocol.json"), read_json(source_run / "collection.json")
    predictions_path = source_run / "predictions.jsonl"
    require(protocol["gold_sha256"] == receipt["gold_sha256"] == digest(bundle), "QUERY_GOLD_CHANGED")
    require(digest(predictions_path.read_bytes()) == receipt["predictions_sha256"], "QUERY_PREDICTIONS_CHANGED")
    require(digest(protocol) == receipt["protocol_sha256"], "QUERY_PROTOCOL_CHANGED")
    require(protocol["runtime_sha256"] == receipt["runtime_sha256"]
            and protocol["evaluator_sha256"] == receipt["evaluator_sha256"], "QUERY_COLLECTION_IDENTITY_MISMATCH")
    old_root = Path(protocol["runtime_root"])
    old_seal = read_json(old_root / "evaluator-seal.json")
    require(digest(old_seal["files"]) == old_seal["sha256"] == protocol["evaluator_sha256"],
            "QUERY_SCORER_IDENTITY_MISMATCH")
    for name, expected in old_seal["files"].items():
        require(digest((old_root / "evaluator" / name).read_bytes()) == expected, "SEALED_EVALUATOR_CHANGED")
    after = read_json(source_run / "after.json")
    assert_snapshot(bundle["manifest"]["snapshot"], after["snapshot"])
    require(after["state_count"] == after["event_count"] == 0, "UNEXPECTED_REVIEW_WRITES")
    original = read_json(source_run / "alignment.json")
    observations = read_jsonl(predictions_path)
    require(len(observations) == len(bundle["cases"])
            and [r["id"] for r in observations] == [c["id"] for c in bundle["cases"]], "FULL_QUERY_SUITE_REQUIRED")
    results = []
    for case, saved in zip(bundle["cases"], observations):
        assert_snapshot(bundle["manifest"]["snapshot"], saved["snapshot"])
        results.extend(assess_case(case, saved, bundle))
    require(len(results) == len(original["turns"]) and all(
        (new["case_id"], new["turn_index"], new["checks"], new["aligned"]) ==
        (old["case_id"], old["turn_index"], old["checks"], old["aligned"])
        for new, old in zip(results, original["turns"])), "REPORTING_AUDIT_CHANGED_ALIGNMENT")
    summary = {"cases": len(observations), "turns": len(results), "aligned": sum(r["aligned"] for r in results),
        "request_failed_turns": sum(not r["checks"]["request_succeeded"] for r in results),
        "literal_source_unverified_turns": sum(r["returned"] > 0 and not r["literal_source_grounded"] for r in results),
        "boundary_review_turns": sum(bool(r["canonical_role_counts"].get("BOUNDARY") or any(
            w["role"] == "BOUNDARY" for item in r["items"] for w in item["witnesses"])) for r in results),
        "review_required_turns": sum(r["review_required"] for r in results),
        "certification_required_turns": sum(r["scope_certification_required"] for r in results),
        "certification_blocked_turns": sum(r["certification_blocked"] for r in results),
        "displayed_items": sum(r["returned"] for r in results),
        "literal_source_grounded_items": sum(item["literal_source_grounded"] for r in results for item in r["items"]),
        "source_scope_accepted_items": sum(item["source_scope_accepted"] for r in results for item in r["items"]),
        "human_verified": False, "release_eligible": False}
    output.mkdir(parents=True)
    write_json(output / "reporting-audit.json", {"summary": summary, "turns": results,
        "bindings": {"source_run": str(source_run), "gold_sha256": digest(bundle),
            "predictions_sha256": receipt["predictions_sha256"], "protocol_sha256": digest(protocol),
            "original_alignment_sha256": digest((source_run / "alignment.json").read_bytes()),
            "reporting_code_sha256": digest(Path(__file__).read_bytes())},
        "note": "Same frozen predictions, checks and aligned judgments; reporting clarification only, zero new model calls."})
    print(summary, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "seal-evaluator", "collect", "score", "audit-reporting"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--actor")
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:18002")
    parser.add_argument("--runtime-root", type=Path, default=Path("data/reports/user-query-source-reviewed-20261015-v1"))
    parser.add_argument("--max-calls", type=int, default=160)
    parser.add_argument("--max-tokens", type=int, default=5000000)
    args = parser.parse_args()
    if args.dataset is None:
        prior_run = (args.source_run if args.mode == "audit-reporting" else
                     args.output if args.mode == "score" else args.resume_from)
        args.dataset = (Path(read_json(prior_run / "protocol.json")["dataset"]) if prior_run else
                        Path("data/gold/user-query-source-reviewed-20261015-v2"))
    if args.mode == "seal-evaluator":
        seal_evaluator(args.runtime_root)
        print({"evaluator_sealed": str(args.runtime_root)}, flush=True)
        raise SystemExit(0)
    require(args.output is not None, "QUERY_OUTPUT_REQUIRED")
    if args.mode == "audit-reporting":
        require(args.source_run is not None, "QUERY_SOURCE_RUN_REQUIRED")
        audit_reporting(args.dataset, args.source_run, args.output)
        raise SystemExit(0)
    if args.mode == "prepare":
        prepare_runtime(args.dataset, args.output, actor=args.actor)
        raise SystemExit(0)
    runtime_root = args.runtime_root
    if args.mode == "score":
        runtime_root = Path(read_json(args.output / "protocol.json")["runtime_root"])
    entry = (runtime_root / "evaluator/evals/project_quality/user_query_alignment.py").resolve()
    if Path(__file__).resolve() != entry:
        require(entry.is_file(), "SEAL_EVALUATOR_BEFORE_COLLECTION")
        existing = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = os.pathsep.join([str(entry.parents[2] / "src"),
            str(entry.parents[2]), *([existing] if existing else [])])
        command = [sys.executable, str(entry), *sys.argv[1:]]
        if "--dataset" not in sys.argv:
            command.extend(["--dataset", str(args.dataset)])
        if os.name == "nt":
            # Windows execv launches a replacement child without keeping the
            # caller's wait/exit-code contract. Keep a parent to relay it.
            raise SystemExit(subprocess.call(command))
        os.execv(sys.executable, command)
    if args.mode == "collect":
        collect(args.dataset, args.output, base_url=args.base_url, runtime_root=args.runtime_root,
                max_calls=args.max_calls, max_tokens=args.max_tokens, resume_from=args.resume_from)
    else:
        score(args.dataset, args.output)
