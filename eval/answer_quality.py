"""Check final responses without treating host completion as semantic success.

Rubrics are frozen gold. Independent reviews bind the exact response and rubric;
neither tool success nor the model's own account of its work supplies that review.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import re

from eval.common import digest
from eval.validate_gold import assertions_valid, require


ANSWER_SCHEMA = "query_answer_v1"
BASES = {"CORPUS", "MIXED", "GENERAL_KNOWLEDGE"}
READ_ACTIONS = {"LIST", "NEXT", "COUNT", "STATS", "SEARCH", "DETAILS", "REVIEW_STATE"}
COMPLETION_VERSION = "query_goal_completion_v1"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_answer_rubric(rubric):
    """Validate an explicitly supplied, independently authored turn rubric."""
    require(isinstance(rubric, dict), "ANSWER_RUBRIC_OBJECT_REQUIRED")
    require(not set(rubric) - {"schema", "required_basis", "criteria", "assertions"},
            "ANSWER_RUBRIC_FIELD_UNKNOWN")
    require(rubric.get("schema") == ANSWER_SCHEMA, "ANSWER_RUBRIC_SCHEMA_INVALID")
    if "required_basis" in rubric:
        require(isinstance(rubric["required_basis"], str) and rubric["required_basis"] in BASES,
                "ANSWER_RUBRIC_BASIS_INVALID")
    criteria = rubric.get("criteria")
    require(isinstance(criteria, list) and bool(criteria), "ANSWER_RUBRIC_CRITERIA_REQUIRED")
    seen = set()
    for criterion in criteria:
        require(isinstance(criterion, dict) and set(criterion) == {"id", "description"},
                "ANSWER_CRITERION_FIELDS_INVALID")
        identity = criterion.get("id")
        require(_text(identity) and identity not in seen, "ANSWER_CRITERION_IDS_INVALID")
        require(_text(criterion.get("description")), "ANSWER_CRITERION_DESCRIPTION_REQUIRED")
        seen.add(identity)
    if "assertions" in rubric:
        require(isinstance(rubric["assertions"], list), "ANSWER_ASSERTIONS_INVALID")
        if rubric["assertions"]:
            assertions_valid(rubric["assertions"])
    return rubric


def _review_assessments(review, rubric, answer):
    if review is None:
        return {}
    require(isinstance(review, dict), "ANSWER_REVIEW_OBJECT_REQUIRED")
    require(review.get("answer_sha256") == digest(answer.encode("utf-8")), "ANSWER_REVIEW_TEXT_CHANGED")
    require(review.get("rubric_sha256") == digest(rubric), "ANSWER_REVIEW_RUBRIC_CHANGED")
    require(review.get("reviewer_kind") in ("agent", "human") and _text(review.get("reviewer")),
            "ANSWER_REVIEW_PROVENANCE_REQUIRED")
    require(review.get("independent") is True, "ANSWER_REVIEW_INDEPENDENT_REQUIRED")
    require(_text(review.get("reviewed_at")), "ANSWER_REVIEW_TIMESTAMP_REQUIRED")
    try:
        stamp = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "ANSWER_REVIEW_TIMEZONE_REQUIRED")
    except ValueError as error:
        from eval.validate_gold import GoldValidationError
        raise GoldValidationError("ANSWER_REVIEW_TIMESTAMP_INVALID") from error
    assessments = review.get("assessments")
    require(isinstance(assessments, dict), "ANSWER_REVIEW_ASSESSMENTS_REQUIRED")
    require(set(assessments) <= {c["id"] for c in rubric["criteria"]}, "ANSWER_REVIEW_CRITERION_UNKNOWN")
    for assessment in assessments.values():
        require(isinstance(assessment, dict) and set(assessment) == {"passed", "evidence_quote", "reason"},
                "ANSWER_REVIEW_ASSESSMENT_INVALID")
        require(assessment["passed"] is None or type(assessment["passed"]) is bool,
                "ANSWER_REVIEW_VERDICT_INVALID")
        quote = assessment["evidence_quote"]
        require(isinstance(quote, str) and _text(assessment["reason"]), "ANSWER_REVIEW_EVIDENCE_REQUIRED")
        require(not quote or quote in answer, "ANSWER_REVIEW_QUOTE_NOT_IN_TEXT")
        require(assessment["passed"] is not True or _text(quote), "ANSWER_REVIEW_POSITIVE_QUOTE_REQUIRED")
    return assessments


def _status(checks):
    statuses = {c["status"] for c in checks}
    return "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"


def _identities(value, *, ordinal=False):
    return (isinstance(value, list)
            and all(type(item) is int and item > 0 if ordinal else _text(item) for item in value)
            and len(value) == len(set(value)))


def _receipt_parts(invocation):
    result = invocation.get("result")
    result = result if isinstance(result, dict) else {}
    facts = result.get("facts")
    facts = facts if isinstance(facts, dict) else {}
    meta = facts.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    completion = meta.get("completion")
    completion = completion if isinstance(completion, dict) else {}
    ordinal = invocation.get("ordinal", completion.get("tool_ordinal"))
    action = result.get("intent")
    return result, facts, meta, completion, ordinal, action


def _ordinal_values(invocation):
    completion = _receipt_parts(invocation)[3]
    return ([invocation["ordinal"]] if "ordinal" in invocation else []) + (
        [completion["tool_ordinal"]] if "tool_ordinal" in completion else [])


def _ordinal_valid(invocation):
    values = _ordinal_values(invocation)
    if not values:
        return None
    if any(type(value) is not int or value <= 0 for value in values):
        return False
    return all(value == values[0] for value in values)


def _current_receipt(invocation, current):
    _, _, meta, _, _, _ = _receipt_parts(invocation)
    current_goal = current.get("user_goal")
    receipt_goal = meta.get("user_goal")
    identities = [current, invocation, meta, *([current_goal] if isinstance(current_goal, dict) else []),
                  *([receipt_goal] if isinstance(receipt_goal, dict) else [])]
    if any(not _text(entity[key]) for entity in identities for key in ("run_id", "request_id") if key in entity):
        return False
    current_run = current.get("run_id") or (current_goal.get("run_id") if isinstance(current_goal, dict) else None)
    receipt_run = invocation.get("run_id") or meta.get("run_id") or (
        receipt_goal.get("run_id") if isinstance(receipt_goal, dict) else None)
    known_runs = [entity["run_id"] for entity in identities if "run_id" in entity]
    if known_runs and any(value != known_runs[0] for value in known_runs):
        return False
    known_requests = [entity["request_id"] for entity in identities if "request_id" in entity]
    if known_requests and any(value != known_requests[0] for value in known_requests):
        return False
    expected = [(current.get("request_id"), meta.get("request_id")), (current_run, receipt_run)]
    if any(wanted and observed and wanted != observed for wanted, observed in expected):
        return False
    if not any(wanted for wanted, _ in expected) or any(wanted and not observed for wanted, observed in expected):
        return None
    return True


def _read_support(invocation):
    _, facts, meta, completion, ordinal, action = _receipt_parts(invocation)
    if invocation.get("status") != "COMPLETED" or not isinstance(action, str) or action not in READ_ACTIONS:
        return False
    ordinal_valid = _ordinal_valid(invocation)
    if (meta.get("relevance_status") == "UNAVAILABLE" or completion.get("tool_action", action) != action
            or ordinal_valid is False):
        return False
    if ordinal_valid is None:
        return None
    if not isinstance(facts.get("data"), list):
        return None
    if action == "COUNT":
        return True if isinstance(meta.get("counts"), dict) else None
    if action == "STATS":
        return True
    if action in {"DETAILS", "REVIEW_STATE"}:
        return True if meta.get("route") == "SQL" else None
    pagination = meta.get("pagination")
    if action in {"LIST", "NEXT"} and isinstance(pagination, dict) and type(pagination.get("result_total")) is int:
        return True
    coverage = meta.get("result_completeness")
    if (action not in {"SEARCH", "NEXT"} or meta.get("relevance_status") not in {"VERIFIED", "UNVERIFIED"}
            or not isinstance(coverage, dict)):
        return None
    if coverage.get("scope") in ("retrieval_candidates", "empty_sql_scope", "engineering_candidate_workset"):
        return True if (type(coverage.get("corpus_complete")) is bool
                        and type(coverage.get("verification_complete")) is bool
                        and type(coverage.get("returned_count")) is int and coverage["returned_count"] >= 0) else None
    from interview_intelligence.agent.evidence_projection import project_workset_progress
    return True if (project_workset_progress(meta.get("candidate_workset_progress")) is not None
                    and all(type(coverage.get(key)) is bool for key in
                            ("complete", "candidate_universe_complete", "business_membership_complete"))) else None


def _detail_sources(row):
    """Check citation identity/shape; frozen source bytes need separate reference scoring."""
    count = row.get("occurrence_count")
    if count is None:
        return None
    if type(count) is not int or count <= 0:
        return False
    if row.get("source_evidence_verified") is False or row.get("source_issues"):
        return None
    sources = row.get("sources")
    if sources is None or sources == []:
        summary = row.get("source_summary")
        if not isinstance(summary, dict) or not _text(summary.get("revision_id")):
            return None
        spans = summary.get("spans")
        if not isinstance(spans, list) or not spans:
            return None
        return all(_span_shape(span) for span in spans)
    if not isinstance(sources, list):
        return False
    return all(isinstance(source, dict) and _text(source.get("occurrence_id"))
               and _text(source.get("revision_id"))
               and isinstance(source.get("raw_file_hash"), str)
               and re.fullmatch(r"[0-9a-f]{64}", source["raw_file_hash"]) is not None
               and _span_shape(source) for source in sources)


def _span_shape(span):
    if not isinstance(span, dict) or not _text(span.get("quote")):
        return False
    if "start_char" in span or "end_char" in span:
        start, end = span.get("start_char"), span.get("end_char")
        if type(start) is not int or type(end) is not int or start < 0 or end - start != len(span["quote"]):
            return False
    elif "start_line" not in span or "end_line" not in span:
        return False
    if "start_line" in span or "end_line" in span:
        start, end = span.get("start_line"), span.get("end_line")
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            return False
    return True


def _host_completion(completion):
    if not isinstance(completion, dict):
        return None
    required = {"version": COMPLETION_VERSION, "status": "COMPLETE", "tool_action": "ANSWER",
                "terminal": True, "answer_completed": True}
    if any(key in completion and (type(completion[key]) is not type(expected) or completion[key] != expected)
           for key, expected in required.items()):
        return False
    if "tool_ordinal" in completion and (type(completion["tool_ordinal"]) is not int or completion["tool_ordinal"] <= 0):
        return False
    return True if all(key in completion for key in (*required, "tool_ordinal")) else None


def _final_receipt(plan, meta, actual):
    invocations = actual.get("tool_invocations")
    if not isinstance(invocations, list):
        return None, None
    completion = meta.get("completion")
    ordinal = completion.get("tool_ordinal") if isinstance(completion, dict) else None
    if type(ordinal) is not int or ordinal <= 0:
        return None, None
    matches = [inv for inv in invocations if isinstance(inv, dict)
               and any(value == ordinal for value in _ordinal_values(inv))]
    if not matches:
        return None, None
    if len(matches) != 1:
        return False, None
    invocation = matches[0]
    result, _, _, saved_completion, _, action = _receipt_parts(invocation)
    if (invocation.get("status") != "COMPLETED" or action != "ANSWER"
            or _ordinal_valid(invocation) is False or _current_receipt(invocation, meta) is False):
        return False, None
    persisted = result.get("planning")
    persisted = persisted.get("spec") if isinstance(persisted, dict) else None
    if not isinstance(persisted, dict) or _current_receipt(invocation, meta) is None:
        return None, invocation
    fields = ("action", "answer_text", "answer_basis", "question_ids", "evidence_tool_ordinals")
    saved_status = _host_completion(saved_completion)
    if (any(persisted.get(key, [] if key in {"question_ids", "evidence_tool_ordinals"} else None)
            != plan.get(key, [] if key in {"question_ids", "evidence_tool_ordinals"} else None) for key in fields)
            or saved_status is False):
        return False, invocation
    if saved_status is None:
        return None, invocation
    return True, invocation


def _references(plan, meta, actual):
    """Check references against current-request persisted read invocations.

    List positions are never ordinals. A follow-up may reference a previous page's
    question IDs, but its persisted ANSWER detail is not an independent source gold.
    """
    ids, ordinals = plan.get("question_ids", []), plan.get("evidence_tool_ordinals", [])
    if not _identities(ids) or not _identities(ordinals, ordinal=True):
        return False, "invalid_or_duplicate_reference_ids"
    for key, expected in (("evidence_question_ids", ids), ("evidence_tool_ordinals", ordinals)):
        if key in meta and meta[key] != expected:
            return False, "plan_and_host_references_disagree"
    basis = plan.get("answer_basis")
    if basis == "GENERAL_KNOWLEDGE":
        return not (ids or ordinals), "general_knowledge_must_not_claim_corpus_references"
    if not ids and not ordinals:
        return False, "corpus_reference_required"
    invocations = actual.get("tool_invocations")
    if invocations is None or invocations == []:
        return None, "persistent_read_receipts_missing"
    if not isinstance(invocations, list) or any(not isinstance(i, dict) for i in invocations):
        return False, "persistent_receipt_shape_invalid"
    final = meta.get("completion")
    final_ordinal = final.get("tool_ordinal") if isinstance(final, dict) else None
    by_ordinal, read_ids, read_receipts, uncertain_receipts, missing_ordinals = {}, set(), [], [], False
    for invocation in invocations:
        result, facts, receipt_meta, completion, ordinal, action = _receipt_parts(invocation)
        if (_ordinal_valid(invocation) is False
                and any(value == reference for value in _ordinal_values(invocation) for reference in ordinals)):
            return False, "persistent_reference_ordinal_invalid_or_inconsistent"
        if type(ordinal) is int and ordinal > 0:
            if ordinal in by_ordinal:
                return False, "duplicate_persistent_tool_ordinal"
            by_ordinal[ordinal] = invocation
        else:
            missing_ordinals = True
        authority, support = _current_receipt(invocation, meta), _read_support(invocation)
        if authority is False or support is False:
            continue
        if type(final_ordinal) is int and type(ordinal) is int and ordinal >= final_ordinal:
            continue
        if authority is None or support is None:
            uncertain_receipts.append(invocation)
            continue
        read_receipts.append(invocation)
        read_ids.update(row["canonical_question_id"] for row in facts["data"]
                        if isinstance(row, dict) and _text(row.get("canonical_question_id")))
    for ordinal in ordinals:
        invocation = by_ordinal.get(ordinal)
        if invocation is None and missing_ordinals:
            return None, "persistent_tool_ordinal_missing"
        if invocation in uncertain_receipts:
            return None, "persistent_read_authority_or_facts_incomplete"
        if invocation is None or invocation not in read_receipts:
            return False, "tool_reference_not_a_current_completed_read"
    projected = meta.get("answer_evidence")
    if projected is not None:
        if (not isinstance(projected, list) or any(not isinstance(row, dict) for row in projected)
                or [row.get("ordinal") for row in projected] != ordinals):
            return False, "projected_answer_evidence_disagrees"
        for projection in projected:
            if projection.get("intent") != _receipt_parts(by_ordinal[projection["ordinal"]])[5]:
                return False, "projected_answer_action_disagrees"
    if not set(ids) <= read_ids:
        status, invocation = _final_receipt(plan, meta, actual)
        if status is False:
            return False, "persisted_answer_disagrees"
        if invocation is not None:
            _, facts, _, _, _, _ = _receipt_parts(invocation)
            rows = facts.get("data")
            persisted_ids = {r.get("canonical_question_id") for r in rows
                             if isinstance(r, dict) and _text(r.get("canonical_question_id"))} if isinstance(rows, list) else set()
            if not set(ids) <= persisted_ids:
                return False, "question_ids_missing_from_persisted_answer_details"
            if actual.get("result", {}).get("rows") != rows:
                return False, "displayed_details_disagree_with_persisted_answer"
            proofs = [_detail_sources(row) for row in rows
                      if isinstance(row, dict) and row.get("canonical_question_id") in ids]
            if False in proofs:
                return False, "persisted_answer_details_have_invalid_source_shape"
            if status is True and proofs and all(proof is True for proof in proofs):
                return True, "question_ids_match_current_persisted_answer_details"
        return None, "current_persisted_answer_detail_proof_incomplete"
    return True, "references_match_current_completed_reads"


def score_answer(gold_turn, actual_turn, *, review=None):
    """Return engineering checks, independent semantic checks and diagnostics."""
    rubric = gold_turn.get("answer_rubric")
    if rubric is None:
        return [], {}
    validate_answer_rubric(rubric)
    from eval.scoring import assertion, check

    require(isinstance(actual_turn, dict), "ANSWER_OBSERVATION_OBJECT_REQUIRED")
    result = actual_turn.get("result")
    result = result if isinstance(result, dict) else {}
    plan = result.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    meta = result.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    text = plan.get("answer_text")
    answer = text if isinstance(text, str) else ""
    assessments = _review_assessments(review, rubric, answer)
    completion = meta.get("completion")
    completion_valid = _host_completion(completion)
    run_status = actual_turn.get("run_status")
    checks = [check("answer.action", plan.get("action") == "ANSWER", actual=plan.get("action")),
              check("answer.nonempty_text", _text(text)),
              check("answer.execution_succeeded", not actual_turn.get("failed")
                    and run_status == "SUCCEEDED" if run_status is not None else
                    False if actual_turn.get("failed") else None, actual=run_status, dimension="process"),
              check("answer.host_completion", completion_valid, actual=completion, dimension="process"),
              check("answer.basis_valid", isinstance(plan.get("answer_basis"), str)
                    and plan["answer_basis"] in BASES, actual=plan.get("answer_basis"))]
    if "required_basis" in rubric:
        checks.append(check("answer.required_basis", plan.get("answer_basis") == rubric["required_basis"],
                            expected=rubric["required_basis"], actual=plan.get("answer_basis")))
    for container, key in ((meta, "answer_text"), (result, "answer"), (meta, "answer")):
        if key in container:
            checks.append(check("answer.display_text" + ("_meta" if container is meta else "_result") + "_" + key,
                                container[key] == answer, dimension="risk"))
    if "answer_basis" in meta:
        checks.append(check("answer.host_basis", meta["answer_basis"] == plan.get("answer_basis"), dimension="risk"))
    receipt_valid, _ = _final_receipt(plan, meta, actual_turn)
    checks.append(check("answer.persisted_final", receipt_valid, dimension="risk"))
    reference_valid, reference_reason = _references(plan, meta, actual_turn)
    checks.append(check("answer.current_evidence", reference_valid, reason=reference_reason, dimension="risk"))
    for rule in rubric.get("assertions", []):
        checks.append({**assertion(rule, actual_turn), "id": "answer.assertion." + rule["id"]})
    engineering = _status(checks)
    semantic = []
    for criterion in rubric["criteria"]:
        assessment = assessments.get(criterion["id"])
        semantic.append(check("answer.semantic." + criterion["id"],
                              assessment["passed"] if assessment is not None else None,
                              reason=assessment["reason"] if assessment is not None else "Independent answer review required"))
    checks.extend(semantic)
    return checks, {"schema": ANSWER_SCHEMA, "answer_sha256": digest(answer.encode("utf-8")),
                    "rubric_sha256": digest(rubric), "engineering_status": engineering,
                    "semantic_status": _status(semantic), "status": _status(checks),
                    "review_status": "MISSING" if review is None else "BOUND_INDEPENDENT_REVIEW",
                    "criterion_counts": dict(Counter(c["status"] for c in semantic)),
                    "not_measured": "Host completion and reference identities do not prove semantic correctness."}
