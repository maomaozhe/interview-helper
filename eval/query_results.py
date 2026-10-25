"""Source-reviewed question lists, scored through the normal routing pipeline.

References describe a finite judged pool. Unreviewed results remain unknown;
neither legacy taxonomy labels nor an existing model response creates a gold label.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import shutil

from eval.common import SECTIONS, digest, inside, read_json, write_json
from eval.pilot import active_facts
from eval.validate_gold import assertions_valid, require, reviewed

CONTRACT = "source_query_results_v1"
CONTRACT_V2 = "source_query_results_v2"
ROLES = {"POSITIVE", "NEGATIVE", "BOUNDARY"}
SOURCE_ACTIONS = {"SEARCH", "LIST", "NEXT", "DETAILS"}
TURN_FIELDS = {"expected_plan", "required_tools", "forbidden_tools", "tool_order", "allowed_plans",
               "assertions", "answer_rubric", "allow_write", "source_results_required"}


def source_references_for_case(reference, case):
    """Bind each source reference to exactly one original conversation turn.

    A v1 reference is single-turn only. A v2 null is an explicit exemption from
    source-list scoring, never an instruction to reuse another turn's labels.
    ``review_query`` is an independent review lookup key; ``query`` remains the
    verbatim user message, including short follow-ups such as "next page".
    """
    require(isinstance(reference, dict) and reference.get("schema") in {CONTRACT, CONTRACT_V2},
            "UNKNOWN_QUERY_RESULT_REFERENCE")
    turns = case.get("turns", [case])
    require(isinstance(turns, list) and turns and all(isinstance(t, dict) for t in turns),
            "QUERY_REFERENCE_TURNS_INVALID")
    require(all(isinstance(t.get("message"), str) and t["message"].strip()
                and isinstance(t.get("expected_plan", {}), dict) for t in turns),
            "QUERY_REFERENCE_TURNS_INVALID")
    queries = reference.get("queries")
    require(isinstance(queries, dict) and case.get("id") in queries, "QUERY_REFERENCE_CASE_SET_CHANGED")
    entry = queries[case["id"]]
    if reference["schema"] == CONTRACT:
        require(len(turns) == 1, "QUERY_REFERENCE_ORIGINAL_MESSAGE_CHANGED")
        references = [entry]
    else:
        require(isinstance(entry, dict) and isinstance(entry.get("turns"), list),
                "QUERY_REFERENCE_TURNS_INVALID")
        references = entry["turns"]
        require(len(references) == len(turns), "QUERY_REFERENCE_TURN_COUNT_CHANGED")
    for turn, query in zip(turns, references):
        if query is None:
            require(reference["schema"] == CONTRACT_V2
                    and turn.get("source_results_required") is False
                    and turn.get("expected_plan", {}).get("action") not in SOURCE_ACTIONS,
                    "QUERY_REFERENCE_SOURCE_TURN_REQUIRED")
            continue
        require(isinstance(query, dict) and isinstance(query.get("query"), str)
                and query["query"] and turn.get("message") == query["query"],
                "QUERY_REFERENCE_ORIGINAL_MESSAGE_CHANGED")
        require(turn.get("source_results_required") is not False, "QUERY_REFERENCE_SOURCE_TURN_CONTRADICTED")
        require("review_query" not in query or isinstance(query["review_query"], str) and query["review_query"].strip(),
                "QUERY_REFERENCE_REVIEW_QUERY_INVALID")
        require("tool_ordinal" not in query or type(query["tool_ordinal"]) is int and query["tool_ordinal"] > 0,
                "QUERY_REFERENCE_TOOL_ORDINAL_INVALID")
    return references


def _facts(root, reference, *, verify_sources=True):
    path = inside(root, reference["source_facts_path"])
    facts = read_json(path)
    require(digest(facts) == reference.get("facts_sha256"), "QUERY_REFERENCE_FACTS_CHANGED")
    require(facts["snapshot"] == reference.get("source_snapshot"), "QUERY_REFERENCE_SNAPSHOT_CHANGED")
    rows = active_facts(facts)
    documents = {d["revision_id"]: d for d in facts["documents"]}
    if verify_sources:
        from interview_intelligence.ingestion.snapshot import decode_source
        texts = {}
        for document in documents.values():
            source = inside(path.parent, document["source_path"])
            require(source.is_file() and digest(source.read_bytes()) == document["source_hash"],
                    "QUERY_REFERENCE_SOURCE_HASH_CHANGED")
            texts[document["revision_id"]] = decode_source(source.read_bytes())
        for row in rows:
            require(bool(row.get("source_spans")), "QUERY_REFERENCE_SPAN_MISSING")
            for span in row["source_spans"]:
                revision = span.get("revision_id", row["revision_id"])
                require(revision in texts, "QUERY_REFERENCE_SOURCE_REVISION_MISSING")
                text = texts[revision]
                start, end = span.get("start_char"), span.get("end_char")
                require(type(start) is int and type(end) is int and 0 <= start < end <= len(text)
                        and text[start:end] == span.get("quote"), "QUERY_REFERENCE_SPAN_CHANGED")
                for key, offset in (("start_line", start), ("end_line", end - 1)):
                    require(key not in span or span[key] == text.count("\n", 0, offset) + 1,
                            "QUERY_REFERENCE_LINE_CHANGED")
    return facts, rows


def validate_query_results(root, manifest, *, verify_sources=True):
    reference = manifest.get("query_result_reference")
    if reference is None:
        return None
    require(isinstance(reference, dict) and reference.get("schema") in {CONTRACT, CONTRACT_V2},
            "UNKNOWN_QUERY_RESULT_REFERENCE")
    if reference.get("recipe_artifact"):
        artifact = reference["recipe_artifact"]
        path = inside(Path(root), artifact["source_path"])
        require(path.is_file() and digest(path.read_bytes()) == artifact["source_hash"], "QUERY_REFERENCE_RECIPE_CHANGED")
    for artifact in reference.get("review_artifacts", []):
        path = inside(Path(root), artifact["source_path"])
        require(path.is_file() and digest(path.read_bytes()) == artifact["source_hash"], "QUERY_REFERENCE_REVIEW_CHANGED")
        review = read_json(path)
        require(review.get("candidate_predictions_seen") is False and review.get("human_verified") is False
                and review.get("facts_sha256") == reference["facts_sha256"], "QUERY_REFERENCE_REVIEW_IDENTITY_CHANGED")
    # Source-reviewed contracts remain development-only, independent of sample size.
    require(manifest.get("release_eligible") is False and manifest.get("exposure") == "seen_source_development",
            "QUERY_REFERENCE_IS_DEVELOPMENT_ONLY")
    facts, rows = _facts(Path(root), reference, verify_sources=verify_sources)
    require(all(manifest["snapshot"].get(k) == value for k, value in facts["snapshot"].items() if k != "as_of"),
            "QUERY_REFERENCE_RUNTIME_REVISION_CHANGED")
    by_id = {q["id"]: q for q in rows}
    cases = manifest["routing"]
    require(isinstance(reference.get("queries"), dict)
            and set(reference["queries"]) == {c["id"] for c in cases}, "QUERY_REFERENCE_CASE_SET_CHANGED")
    for case in cases:
        require(case["split"] == "dev" and case.get("human_verified") is False,
                "QUERY_REFERENCE_REVIEW_IDENTITY_INVALID")
        for query in source_references_for_case(reference, case):
            if query is None:
                continue
            require(query.get("coverage", {}).get("scope") == "judged_pool", "QUERY_REFERENCE_COVERAGE_INVALID")
            require(type(query.get("minimum_positive")) is int and query["minimum_positive"] > 0,
                    "QUERY_REFERENCE_MINIMUM_INVALID")
            require(type(query.get("minimum_families", 0)) is int and query.get("minimum_families", 0) >= 0,
                    "QUERY_REFERENCE_FAMILY_MINIMUM_INVALID")
            labels = query.get("labels", [])
            require(isinstance(labels, list) and labels and all(isinstance(x, dict) for x in labels),
                    "QUERY_REFERENCE_LABEL_INVALID")
            require(all(isinstance(x.get("occurrence_id"), str) for x in labels)
                    and len({x["occurrence_id"] for x in labels}) == len(labels), "QUERY_REFERENCE_LABELS_DUPLICATE")
            for label in labels:
                require(label.get("occurrence_id") in by_id and label.get("role") in ROLES
                        and isinstance(label.get("reason"), str) and label["reason"].strip(), "QUERY_REFERENCE_LABEL_INVALID")
                reviewed(label.get("review"))
                require(label["review"].get("reviewer_kind") == "agent", "QUERY_REFERENCE_REVIEWER_INVALID")
            positives = {by_id[x["occurrence_id"]]["canonical_question_id"] for x in labels if x["role"] == "POSITIVE"}
            require(len(positives) >= query["minimum_positive"] and any(x["role"] == "NEGATIVE" for x in labels),
                    "QUERY_REFERENCE_ANCHORS_INCOMPLETE")
            families = {x.get("object_family") for x in labels if x["role"] == "POSITIVE" and x.get("object_family")}
            require(len(families) >= query.get("minimum_families", 0), "QUERY_REFERENCE_FAMILIES_INCOMPLETE")
    return {"reference": reference, "facts": facts, "rows": rows}


def _recipe_turns(entry):
    turns = entry.get("turns", [entry])
    require(isinstance(turns, list) and turns and all(isinstance(t, dict) for t in turns),
            "QUERY_REFERENCE_TURNS_INVALID")
    require("turns" not in entry or "query" not in entry, "QUERY_REFERENCE_AMBIGUOUS_RECIPE")
    normalized = []
    for turn in turns:
        message = turn.get("message", turn.get("query"))
        require(isinstance(message, str) and message.strip(), "QUERY_REFERENCE_MESSAGE_MISSING")
        query = turn.get("query")
        require(query is None or isinstance(query, str) and query.strip(), "QUERY_REFERENCE_REVIEW_QUERY_INVALID")
        require(query is not None or turn.get("source_results_required") is False,
                "QUERY_REFERENCE_SOURCE_TURN_REQUIRED")
        require(query is not None or not any(k in turn for k in
                ("minimum_positive", "minimum_families", "required_company", "tool_ordinal")),
                "QUERY_REFERENCE_SOURCELESS_CONSTRAINT")
        normalized.append({**turn, "message": message})
    return normalized


def _frozen_turn(entry):
    """Copy supported evaluation controls while retaining the read-only boundary."""
    turn = {"message": entry["message"], "expected_plan": {},
            **{key: deepcopy(value) for key, value in entry.items() if key in TURN_FIELDS}}
    require(isinstance(turn["expected_plan"], dict), "QUERY_REFERENCE_PLAN_INVALID")
    require(turn.get("allow_write", False) is False, "QUERY_REFERENCE_WRITE_NOT_ALLOWED")
    turn["allow_write"] = False
    require("source_results_required" not in turn or type(turn["source_results_required"]) is bool,
            "QUERY_REFERENCE_SOURCE_REQUIREMENT_INVALID")
    for key in ("required_tools", "forbidden_tools", "tool_order"):
        values = turn.get(key, [])
        require(isinstance(values, list) and all(isinstance(v, str) and v for v in values),
                "QUERY_REFERENCE_TOOL_CONSTRAINT_INVALID")
    alternatives = turn.get("allowed_plans", [])
    require(isinstance(alternatives, list) and all(isinstance(plan, list)
            and all(isinstance(tool, str) and tool for tool in plan) for plan in alternatives),
            "QUERY_REFERENCE_TOOL_CONSTRAINT_INVALID")
    turn["forbidden_tools"] = list(dict.fromkeys(["record_review", *turn.get("forbidden_tools", [])]))
    defaults = [{"id": "terminal", "path": "run_status", "op": "eq", "value": "SUCCEEDED"},
                {"id": "no_review_writes", "path": "result.write_event_delta", "op": "eq", "value": 0}]
    extra = turn.get("assertions", [])
    require(isinstance(extra, list), "QUERY_REFERENCE_ASSERTIONS_INVALID")
    if extra:
        assertions_valid(extra)
    fixed = {rule["id"]: rule for rule in defaults}
    require(all(rule["id"] not in fixed or rule == fixed[rule["id"]] for rule in extra),
            "QUERY_REFERENCE_REQUIRED_ASSERTION_CHANGED")
    turn["assertions"] = defaults + [rule for rule in extra if rule["id"] not in fixed]
    return turn


def build_query_results(snapshot, review_paths, recipe_path, output, *, authorization):
    """Freeze a new private dataset; never overwrite a historical reference."""
    snapshot, output = Path(snapshot), Path(output)
    require(not output.exists(), "QUERY_REFERENCE_OUTPUT_EXISTS")
    require(authorization.get("thread_id") and authorization.get("user_instruction"), "QUERY_REFERENCE_AUTHORIZATION_MISSING")
    facts, recipe = read_json(snapshot / "facts.json"), read_json(recipe_path)
    entries = recipe.get("cases")
    require(isinstance(entries, list) and entries and all(isinstance(e, dict) for e in entries),
            "QUERY_REFERENCE_RECIPE_INVALID")
    require(all(isinstance(e.get("id"), str) and e["id"] for e in entries)
            and len({e["id"] for e in entries}) == len(entries), "QUERY_REFERENCE_CASE_IDS_INVALID")
    recipe_turns = [(entry, _recipe_turns(entry)) for entry in entries]
    schema = CONTRACT_V2 if any("turns" in entry for entry in entries) else CONTRACT
    reviews, queries = [], {}
    for path in review_paths:
        review = read_json(path)
        require(review.get("facts_sha256") == digest(facts), "QUERY_REFERENCE_STALE_REVIEW")
        require(review.get("candidate_predictions_seen") is False and review.get("human_verified") is False,
                "QUERY_REFERENCE_INDEPENDENCE_MISSING")
        identity = {"reviewer_kind": "agent", "reviewer": review.get("reviewer"),
                    "reviewed_at": review.get("reviewed_at"), "guide_version": recipe["guide_version"]}
        reviewed(identity)
        for query in review["queries"]:
            require(query["query"] not in queries, "QUERY_REFERENCE_DUPLICATE_QUERY")
            queries[query["query"]] = {**query, "labels": [
                {"occurrence_id": label["occurrence_id"], "role": label["role"], "reason": label["reason"],
                 **({"object_family": label["object_family"]} if label.get("object_family") else {}),
                 "review": identity} for label in query["labels"]]}
        reviews.append((Path(path), review))
    require(set(queries) == {turn["query"] for _, turns in recipe_turns for turn in turns
                            if turn.get("query") is not None}, "QUERY_REFERENCE_QUERY_SET_CHANGED")
    now = datetime.now(timezone.utc).isoformat()
    cases, references = [], {}
    for entry, turn_entries in recipe_turns:
        turn_references, turns, criteria = [], [], []
        for turn_entry in turn_entries:
            turns.append(_frozen_turn(turn_entry))
            if turn_entry.get("query") is None:
                turn_references.append(None)
                continue
            query = queries[turn_entry["query"]]
            reference = {**deepcopy(query), "query": turn_entry["message"],
                         "minimum_positive": turn_entry.get("minimum_positive")}
            if schema == CONTRACT_V2:
                reference["review_query"] = turn_entry["query"]
            for key in ("minimum_families", "required_company", "tool_ordinal"):
                if key in turn_entry:
                    reference[key] = turn_entry[key]
            turn_references.append(reference)
            criteria.append(query["criteria"])
        references[entry["id"]] = {"turns": turn_references} if schema == CONTRACT_V2 else turn_references[0]
        cases.append({"id": entry["id"], "group_id": recipe["id"] + "-" + entry["id"], "split": "dev",
            "human_verified": False, "agent_verified": True,
            "tags": ["user_original_short_query", "seen_source", "source_result_reference", "not_release_eligible"],
            "review": {"reviewer_kind": "agent", "reviewer": "Independent source reviewers",
                       "reviewed_at": now, "guide_version": recipe["guide_version"],
                       "basis": criteria[0] if schema == CONTRACT else criteria}, "turns": turns})
    source_reference = {"schema": schema, "source_facts_path": "snapshot/facts.json", "facts_sha256": digest(facts),
                        "source_snapshot": facts["snapshot"], "queries": references}
    manifest = {"version": output.name, "status": "frozen", "kind": "corpus", "snapshot": facts["snapshot"],
        "physical_indices": facts.get("physical_indices", []), "id_namespace": "canonical",
        "exposure": "seen_source_development", "release_eligible": False,
        "review_authorization": {"policy": "delegated_agent", **authorization},
        "query_result_reference": source_reference,
        **{section: cases if section == "routing" else [] for section in SECTIONS}}
    if recipe.get("as_of"):
        manifest["snapshot"] = {**facts["snapshot"], "as_of": recipe["as_of"]}
    # Validate all input evidence before creating an output directory.
    source_reference["source_facts_path"] = "facts.json"
    validate_query_results(snapshot, manifest)
    source_reference["source_facts_path"] = "snapshot/facts.json"
    source_reference["review_artifacts"] = [
        {"source_path": f"reviews/{number:02d}-{path.name}", "source_hash": digest(path.read_bytes())}
        for number, (path, _) in enumerate(reviews, 1)]
    source_reference["recipe_artifact"] = {"source_path": "recipe.json", "source_hash": digest(Path(recipe_path).read_bytes())}
    output.mkdir(parents=True)
    (output / "snapshot").mkdir()
    shutil.copy2(snapshot / "facts.json", output / "snapshot/facts.json")
    for document in facts["documents"]:
        target = inside(output / "snapshot", document["source_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(snapshot, document["source_path"]), target)
    (output / "reviews").mkdir()
    for number, (path, _) in enumerate(reviews, 1):
        shutil.copy2(path, output / "reviews" / f"{number:02d}-{path.name}")
    shutil.copy2(recipe_path, output / "recipe.json")
    write_json(output / "manifest.json", manifest)
    write_expected_answers(output, manifest, facts)
    hashes = {p.relative_to(output).as_posix(): digest(p.read_bytes()) for p in output.rglob("*") if p.is_file()}
    write_json(output / "freeze.json", {"dataset_sha256": digest(manifest), "created_at": now, "file_hashes": hashes})
    return {"dataset": str(output.resolve()), "cases": len(cases), "dataset_sha256": digest(manifest),
            "model_calls": 0, "release_eligible": False}


def write_expected_answers(output, manifest, facts):
    rows = {q["id"]: q for q in active_facts(facts)}
    docs = {d["revision_id"]: d for d in facts["documents"]}
    reference = manifest["query_result_reference"]
    multi = reference["schema"] == CONTRACT_V2
    lines = ["# 多轮固定参考题单" if multi else "# 五问固定参考题单", "", "原问保持不变。以下是独立审核原文得到的正确查题参考；不是这几类技术问题的解答。",
             "仅覆盖已审核面板，未列出不代表不相关；边界不计强正例。Agent 审核，非人工认证，开发集，无发布资格。", ""]
    queries = [(case, index, query) for case in manifest["routing"]
               for index, query in enumerate(source_references_for_case(reference, case), 1)]
    for case, index, query in queries:
        heading = f"{case['id']} · 第 {index} 轮：{case['turns'][index - 1]['message']}" if multi else query["query"]
        if query is None:
            lines += [f"## {heading}", "", "本轮未要求来源题单；按本轮过程断言和回答规则检查，不继承其他轮的题目标签。", ""]
            continue
        criteria = query["criteria"]
        if isinstance(criteria, dict):
            criteria = "\n\n".join(str(criteria[key]) for key in ("intent", "positive", "negative") if criteria.get(key))
        lines += [f"## {heading}", "", str(criteria), ""]
        if multi and query.get("review_query") != query["query"]:
            lines += [f"独立审核意图：{query['review_query']}", ""]
        groups = {}
        for label in query["labels"]:
            if label["role"] == "POSITIVE":
                q = rows[label["occurrence_id"]]
                groups.setdefault(q["canonical_question_id"], []).append((q, label))
        lines += [f"已审核确定相关：{len(groups)} 道归并题。最低可用检查仍为 {query['minimum_positive']} 道；不是用户指定数量。", ""]
        for number, variants in enumerate(groups.values(), 1):
            q, label = variants[0]
            document = docs[q["revision_id"]]
            span = q["source_spans"][0]
            line = span.get("start_line", 1)
            text = q["raw_question"].replace("\n", " ")
            lines += [f"{number}. {text}",
                      f"   来源：[{q['company']} · 原文 L{line}](snapshot/{document['source_path']}#L{line})；{label['reason']}",
                      ""]
        boundaries = [x for x in query["labels"] if x["role"] == "BOUNDARY"]
        lines += [f"<details><summary>边界项：{len(boundaries)} 条原始提问，不计入正确主结果</summary>", ""]
        lines += [f"- {rows[x['occurrence_id']]['raw_question'].replace(chr(10), ' ')}：{x['reason']}" for x in boundaries]
        lines += ["", "</details>", ""]
    (Path(output) / "EXPECTED-ANSWERS.md").write_text("\n".join(lines), encoding="utf-8")


def _witness(source, row):
    if not isinstance(source, dict) or source.get("occurrence_id") != row["id"]:
        return False
    revision = source.get("revision_id", source.get("source_revision_id"))
    if revision != row["revision_id"]:
        return False
    if source.get("raw_file_hash") != row["source_hash"]:
        return False
    for key, expected in (("interview_id", row["interview_id"]), ("source_document_id", row["document_id"])):
        if key in source and source[key] != expected:
            return False
    spans = source.get("source_spans", source.get("spans", [source]))
    if not isinstance(spans, list) or not spans or any(not isinstance(span, dict) for span in spans):
        return False
    located = lambda span: (all(type(span.get(k)) is int for k in ("start_char", "end_char"))
                            or all(type(span.get(k)) is int for k in ("start_line", "end_line")))
    return all(located(span) and any(span.get("quote") == gold["quote"] and all(
        field not in span or span[field] == gold.get(field)
        for field in ("start_char", "end_char", "start_line", "end_line"))
        for gold in row["source_spans"]) for span in spans)


def _summary_witness(summary, row):
    """SQL exposes immutable revision + exact full spans, not SEARCH identities.

    The caller must require exactly one matching occurrence. The source hash is
    verified independently against the frozen snapshot, never invented in a
    copied response. A bare revision/quote or a line-only summary is insufficient.
    """
    if not isinstance(summary, dict) or summary.get("revision_id") != row["revision_id"]:
        return False
    spans = summary.get("spans")
    if not isinstance(spans, list) or not spans or len(spans) != len(row["source_spans"]):
        return False
    for observed, gold in zip(spans, row["source_spans"]):
        if not isinstance(observed, dict) or any(type(observed.get(k)) is not int for k in ("start_char", "end_char")):
            return False
        if any(observed.get(k) != gold.get(k) for k in ("start_char", "end_char", "quote")):
            return False
        if any(k in observed and observed[k] != gold.get(k) for k in ("start_line", "end_line", "revision_id")):
            return False
    return True


def score_query_results(query, actual, facts_rows):
    """Return checks and diagnostics; positive coverage is explicitly pool-bound."""
    from eval.scoring import check
    result = actual.get("result", {})
    returned = result.get("rows", [])
    require(isinstance(returned, list) and all(isinstance(r, dict) for r in returned), "QUERY_RESULT_ROWS_INVALID")
    labels = {x["occurrence_id"]: x for x in query["labels"]}
    grouped = {}
    for row in facts_rows:
        grouped.setdefault(row["canonical_question_id"], []).append(row)
    known = {q["canonical_question_id"] for q in facts_rows
             if labels.get(q["id"], {}).get("role") == "POSITIVE"}
    outcomes, covered, families, valid_sources = [], set(), set(), True
    for item in returned:
        key = item.get("canonical_question_id")
        candidates = grouped.get(key, [])
        sources = item.get("sources") or []
        matched, source_valid = [], bool(sources) and bool(candidates)
        identity_basis = "returned_occurrence_hash_and_span"
        if (not sources and item.get("source_summary") and result.get("meta", {}).get("route") == "SQL"
                and result.get("plan", {}).get("action") in {"LIST", "NEXT"}):
            witnesses = [q for q in candidates if _summary_witness(item["source_summary"], q)]
            source_valid = len(witnesses) == 1
            matched = witnesses if source_valid else []
            identity_basis = "unique_exact_span_in_hash_verified_frozen_facts"
        for source in sources:
            witnesses = [q for q in candidates if _witness(source, q)]
            source_valid = source_valid and bool(witnesses)
            matched.extend(witnesses)
        valid_sources = valid_sources and source_valid
        roles = {labels[q["id"]]["role"] for q in matched if q["id"] in labels}
        # A relevant canonical needs a displayed positive occurrence, not a label
        # borrowed from another occurrence or from an adjacent interview question.
        role = ("POSITIVE" if "POSITIVE" in roles else "NEGATIVE" if roles == {"NEGATIVE"}
                else "BOUNDARY" if roles else "UNREVIEWED") if source_valid else "INVALID_SOURCE"
        if role == "POSITIVE":
            covered.add(key)
            families.update(labels[q["id"]]["object_family"] for q in matched
                            if labels.get(q["id"], {}).get("role") == "POSITIVE" and labels[q["id"]].get("object_family"))
        outcomes.append({"canonical_question_id": key, "role": role, "source_valid": bool(source_valid),
                         "witness_ids": sorted({q["id"] for q in matched}), "reviewed_roles": sorted(roles),
                         "identity_basis": identity_basis})
    counts = Counter(o["role"] for o in outcomes)
    identities = [r.get("canonical_question_id") for r in returned]
    checks = [check("source_results.unique", len(identities) == len(set(identities)), dimension="result"),
              check("source_results.sources", valid_sources, dimension="risk"),
              check("source_results.no_negative", counts["NEGATIVE"] == 0, actual=counts["NEGATIVE"]),
              check("source_results.minimum_positive", len(covered) >= query["minimum_positive"],
                    expected=query["minimum_positive"], actual=len(covered)),
              check("source_results.all_reviewed_positive", None if any(o["role"] in {"BOUNDARY", "UNREVIEWED"} for o in outcomes)
                    else bool(returned) and all(o["role"] == "POSITIVE" for o in outcomes), actual=dict(counts))]
    if query.get("minimum_families"):
        checks.append(check("source_results.object_families", len(families) >= query["minimum_families"],
                            expected=query["minimum_families"], actual=len(families)))
    meta = result.get("meta", {})
    if meta.get("route") == "SEARCH" and "result_completeness" in meta:
        complete = meta.get("result_completeness") or {}
        checks.append(check("source_results.candidate_verification_complete",
                            True if complete.get("verification_complete") is True else None,
                            actual=complete, reason="Verified partial results can be useful but do not count as a complete task success."))
    pagination = meta.get("pagination", {})
    if (meta.get("route") == "SQL" and meta.get("semantic_scope", {}).get("complete") is True
            and pagination.get("offset") == 0 and pagination.get("top_n") is None
            and pagination.get("next_cursor") is None
            and type(pagination.get("result_total")) is int and pagination["result_total"] == len(returned)):
        checks.append(check("source_results.complete_sql_contains_known_positives", not (known - covered),
                            expected=len(known), actual=len(covered),
                            reason="A claimed complete result cannot omit a known source-reviewed positive; passing is not proof of corpus recall."))
    if query.get("required_company"):
        wanted = query["required_company"]
        all_witnesses = [q for o in outcomes for q in facts_rows if q["id"] in o["witness_ids"]]
        checks.append(check("source_results.same_occurrence_company", bool(all_witnesses)
                            and all(q["company"] == wanted for q in all_witnesses), expected=wanted))
    diagnostics = {"returned": len(returned), "roles": dict(counts), "known_positive_canonicals": len(known),
        "covered_positive_canonicals": len(covered), "judged_pool_positive_coverage": len(covered) / len(known) if known else None,
        "missed_positive_ids": sorted(known - covered), "object_families": sorted(families),
        "outcomes": outcomes, "reference_scope": "judged_pool",
        "not_measured": "Full-corpus recall, unreviewed relevance, or correctness of model-written technical answers."}
    return checks, diagnostics
