"""Evidence-based rubrics and module scorers. Never consume self-reported success."""
from __future__ import annotations

import math
from collections import Counter
from statistics import mean

from eval.common import MISSING, PIPELINES, digest, finite_number, inside, lookup
from eval.metrics import classification_metrics, efficiency, prf, retrieval_metrics, retrieval_sample
from eval.validate_gold import canonical_mapping, require, reviewed


def check(key, passed, *, expected=None, actual=None, dimension="result", reason=None):
    return {"id": key, "status": "UNKNOWN" if passed is None else "PASS" if passed else "FAIL",
            "dimension": dimension, "expected": expected, "actual": actual, "reason": reason}


def assertion(rule, observation):
    actual = lookup(observation, rule["path"])
    expected, op = rule.get("value"), rule["op"]
    missing = actual is MISSING
    if missing and op != "exists":
        return check(rule["id"], None, expected=expected, dimension=rule.get("dimension", "result"),
                     reason=f"missing evidence at {rule['path']}")
    try:
        if op == "exists":
            passed = not missing
        elif op == "eq":
            passed = actual == expected and (not isinstance(actual, bool) or type(expected) is bool)
        elif op == "ne":
            passed = actual != expected
        elif op == "set_eq":
            passed = {digest(v) for v in actual} == {digest(v) for v in expected}
        elif op == "subset":
            passed = {digest(v) for v in expected} <= {digest(v) for v in actual}
        elif op == "contains":
            passed = expected in actual
        elif op == "length":
            passed = len(actual) == expected
        elif op == "unique":
            if not isinstance(actual, list):
                raise TypeError("unique expects a list")
            passed = len(actual) == len({digest(v) for v in actual})
        elif op == "approx":
            passed = finite_number(actual) and finite_number(expected) and math.isclose(actual, expected, rel_tol=0,
                                                                                       abs_tol=rule.get("tolerance", 1e-6))
        elif op in {"lte", "gte"}:
            passed = finite_number(actual) and finite_number(expected) and (actual <= expected if op == "lte" else actual >= expected)
        elif op == "not_empty":
            passed = len(actual) > 0
        else:
            raise ValueError("unsupported rubric operation")
    except (TypeError, ValueError, KeyError):
        return check(rule["id"], None, expected=expected, actual=None if missing else actual,
                     dimension=rule.get("dimension", "result"), reason="evidence has an incompatible type")
    return check(rule["id"], passed, expected=expected, actual=None if missing else actual,
                 dimension=rule.get("dimension", "result"))


def _row(gold, prediction, checks, **scores):
    statuses = [c["status"] for c in checks]
    status = "FAIL" if prediction.get("failed") or "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
    return {"id": gold["id"], "tags": gold.get("tags", []), "status": status,
            "failed": bool(prediction.get("failed")), "error_code": prediction.get("error_code"),
            "checks": checks, "scores": scores, "observation": prediction}


def score_query(golds, predictions, *, sql=False):
    rows, tool_scores, field_scores, plan_scores, tool_counts, writes = [], [], [], [], [], 0
    for gold, prediction in zip(golds, predictions):
        turns = gold.get("turns", [gold])
        observed = prediction.get("turns", [prediction])
        checks = [check("turn_count", len(observed) == len(turns), expected=len(turns), actual=len(observed), dimension="process")]
        for index, turn in enumerate(turns):
            actual = observed[index] if index < len(observed) else {"failed": True}
            trace = actual.get("tool_trace", [])
            require(isinstance(trace, list) and all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in trace), "invalid tool trace")
            used = [t["name"] for t in trace]
            tool_counts.append(len(used))
            if not sql:
                alternatives = turn.get("allowed_plans") or [turn.get("required_tools", [])]
                correct = any(set(plan) <= set(used) for plan in alternatives)
                correct = correct and not set(turn.get("forbidden_tools", [])) & set(used)
                order = iter(used)
                correct = correct and all(any(tool == candidate for candidate in order) for tool in turn.get("tool_order", []))
                bad_write = "record_review" in used and not turn.get("allow_write", False)
                writes += int(bad_write)
                correct = bool(correct and not bad_write and not actual.get("failed"))
                tool_scores.append(int(correct))
                checks.append(check(f"turn{index}.tools", correct, expected=alternatives, actual=used, dimension="process"))
                checks.append(check(f"turn{index}.write_authorized", not bad_write, actual=used, dimension="risk"))
            expected = turn.get("expected_plan", {})
            plan = actual.get("result", {}).get("plan", {})
            fields = []
            for key, value in expected.items():
                current = lookup(plan, key)
                passed = current is not MISSING and current == value
                field_scores.append(int(passed))
                fields.append(passed)
                checks.append(check(f"turn{index}.argument.{key}", passed if current is not MISSING else None,
                                    expected=value, actual=None if current is MISSING else current, dimension="process"))
            if expected:
                plan_scores.append(int(all(fields)))
            checks.extend({**assertion(rule, actual), "id": f"turn{index}.{rule['id']}"} for rule in turn["assertions"])
            checks.append(check(f"turn{index}.executed", not actual.get("failed"), actual=actual.get("error_code"), dimension="process"))
        rows.append(_row(gold, prediction, checks))
    metrics = {"task_success_rate": mean(r["status"] == "PASS" for r in rows) if rows else None,
               "evaluated_queries": len(rows), "tool_selection_accuracy": mean(tool_scores) if tool_scores else None,
               "argument_accuracy": mean(field_scores) if field_scores else None,
               "argument_fields": len(field_scores), "complete_plan_accuracy": mean(plan_scores) if plan_scores else None,
               "unauthorized_write_count": writes, "mean_tool_calls": mean(tool_counts) if tool_counts else None,
               "unknown_assertions": sum(c["status"] == "UNKNOWN" for r in rows for c in r["checks"])}
    return metrics, rows


def score_labels(golds, predictions):
    rows, metrics = [], {}
    for field in ("response_form", "coding_focus"):
        expected = [g[field] for g in golds]
        actual = ["FAILED" if p.get("failed") else p.get("result", {}).get(field, "FAILED") for p in predictions]
        metrics[field] = classification_metrics(expected, actual, positive="CODE" if field == "response_form" else "ENGINEERING")
    known, covered, covered_correct = 0, 0, 0
    for gold, pred in zip(golds, predictions):
        result = pred.get("result", {})
        checks = [check(field, not pred.get("failed") and result.get(field) == gold[field], expected=gold[field], actual=result.get(field))
                  for field in ("response_form", "coding_focus")]
        rows.append(_row(gold, pred, checks))
        if gold["response_form"] != "UNKNOWN" and gold["coding_focus"] != "UNKNOWN":
            known += 1
            if not pred.get("failed") and all(result.get(k) not in {None, "UNKNOWN", "FAILED"} for k in ("response_form", "coding_focus")):
                covered += 1
                covered_correct += int(rows[-1]["status"] == "PASS")
    metrics.update(joint_accuracy=mean(r["status"] == "PASS" for r in rows) if rows else None,
                   known_gold=known, predicted_coverage=covered / known if known else None,
                   covered_accuracy=covered_correct / covered if covered else None)
    # The product's ENGINEERING/ALGORITHM filters also include MIXED. Report
    # that business meaning separately; never replace the strict class gate.
    filter_metrics = {}
    for name, focuses, form in (("engineering", {"ENGINEERING", "MIXED"}, None),
                               ("algorithm", {"ALGORITHM", "MIXED"}, None),
                               ("code_engineering", {"ENGINEERING", "MIXED"}, "CODE"),
                               ("code_algorithm", {"ALGORITHM", "MIXED"}, "CODE")):
        pairs, unknown = [], 0
        for gold, pred in zip(golds, predictions):
            if gold["coding_focus"] == "UNKNOWN" or form and gold["response_form"] == "UNKNOWN":
                unknown += 1
                continue
            result = pred.get("result", {})
            expected = gold["coding_focus"] in focuses and (not form or gold["response_form"] == form)
            actual = not pred.get("failed") and result.get("coding_focus") in focuses and (
                not form or result.get("response_form") == form)
            pairs.append((bool(expected), bool(actual)))
        filter_metrics[name] = {**prf(sum(e and p for e, p in pairs),
            sum(not e and p for e, p in pairs), sum(e and not p for e, p in pairs)),
            "evaluated_samples": len(pairs), "unknown_gold_samples": unknown,
            "definition": "MIXED qualifies for both focus filters; CODE additionally requires response_form=CODE."}
    metrics["task_filter_metrics"] = filter_metrics
    return metrics, rows


def score_dedup(golds, predictions):
    expected = [g["label"] for g in golds]
    actual = ["FAILED" if p.get("failed") else p.get("result", {}).get("label", "FAILED") for p in predictions]
    metrics = classification_metrics(expected, actual, positive="SAME")
    rows, recalled, e2e, candidate_samples = [], 0, 0, 0
    same = sum(g["label"] == "SAME" for g in golds)
    for gold, pred, label in zip(golds, predictions, actual):
        checks = [check("pair_label", label == gold["label"], expected=gold["label"], actual=label)]
        if gold["label"] == "SAME" and "candidate_ids" in pred.get("result", {}):
            candidate_ids = pred["result"]["candidate_ids"]
            require(isinstance(candidate_ids, list) and all(isinstance(i, str) for i in candidate_ids)
                    and len(candidate_ids) == len(set(candidate_ids)), "invalid dedup candidate IDs")
            candidate_samples += 1
            hit = gold["right"]["id"] in pred["result"]["candidate_ids"][:10]
            recalled += int(hit and not pred.get("failed"))
            e2e += int(hit and label == "SAME" and not pred.get("failed"))
            checks.append(check("candidate_recalled", hit, expected=gold["right"]["id"], actual=pred["result"]["candidate_ids"][:10], dimension="process"))
        rows.append(_row(gold, pred, checks))
    metrics.update(candidate_same_samples=candidate_samples, same_gold_samples=same,
                   candidate_metrics_status="EVALUATED" if candidate_samples == same and same else "PARTIAL" if candidate_samples else "NOT_RUN",
                   embedding_threshold_baseline="NOT_RUN",
                   candidate_observation_coverage=candidate_samples / same if same else None,
                   candidate_recall_at_10=recalled / same if same and candidate_samples == same else None,
                   end_to_end_same_recall=e2e / same if same and candidate_samples == same else None,
                   failed_samples=sum(label == "FAILED" for label in actual))
    return metrics, rows


def score_retrieval(golds, predictions, manifest):
    mapping = canonical_mapping(manifest)
    pools = any("pipelines" in p for p in predictions)
    names = list(PIPELINES) if pools else [manifest.get("pipeline", "HYBRID")]
    metrics, rows = {}, []
    for pipeline in names:
        samples, observations = [], []
        for gold, pred in zip(golds, predictions):
            relevant = {}
            for key, grade in gold["relevance"].items():
                canonical = mapping.get(key, key)
                relevant[canonical] = max(grade, relevant.get(canonical, 0))
            current = pred.get("pipelines", {}).get(pipeline, {"failed": True}) if pools else pred
            # A top-level aggregate failure describes the whole ablation row.
            # Independent successful pipelines retain their observed score.
            failed = bool(current.get("failed")) or (not pools and bool(pred.get("failed")))
            checks = []
            if pools and pipeline == "HYBRID_RERANK":
                candidate_ids = pred.get("pipelines", {}).get("HYBRID", {}).get("candidate_ids")
                same_candidates = candidate_ids is not None and candidate_ids == current.get("candidate_ids")
                checks.append(check("same_hybrid_candidates", same_candidates, expected=candidate_ids, actual=current.get("candidate_ids"), dimension="process"))
                failed = failed or not same_candidates or not set(current.get("ranked", [])) <= set(candidate_ids or [])
                hybrid_hash = pred.get("pipelines", {}).get("HYBRID", {}).get("candidate_sha256")
                if hybrid_hash is not None:
                    same_content = hybrid_hash == current.get("candidate_sha256")
                    checks.append(check("same_candidate_content", same_content, expected=hybrid_hash, actual=current.get("candidate_sha256"), dimension="process"))
                    failed = failed or not same_content
            if current.get("executed_pipeline", pipeline) != pipeline:
                failed = True
                checks.append(check("requested_pipeline_executed", False, expected=pipeline, actual=current.get("executed_pipeline"), dimension="process"))
            sample = {"relevant": relevant, "ranked": current.get("ranked", []), "failed": failed}
            samples.append(sample)
            observations.append({**current, "failed": failed})
            scored = retrieval_sample(sample)
            checks.append(check("execution", not scored["failed"], actual=current.get("error_code"), dimension="process"))
            if scored["negative"]:
                checks.append(check("correct_empty", bool(scored["negative_correct"])))
            else:
                checks.append(check("has_relevant_hit", scored["recall_at_10"] > 0, actual=scored["recall_at_10"]))
            rows.append({**_row(gold, {**current, "failed": scored["failed"]}, checks, **scored), "pipeline": pipeline})
        metrics[pipeline] = retrieval_metrics(samples)
        metrics[pipeline]["efficiency"] = {**efficiency(observations),
            "latency_scope": "measured stage only; shared query embedding recorded once in top-level observations",
            "shared_embedding_samples": sum("shared_embedding_ms" in p for p in observations)}
    return {"pipelines": metrics}, rows


def score_extraction(golds, predictions, root, alignments, *, fixture=False, review_policy="human"):
    require(review_policy in {"human", "delegated_agent"}, "unknown alignment review policy")
    rows, tp, fp, fn, matched_fields, metadata_fields, all_metadata = [], 0, 0, 0, {}, {}, {}
    hallucinations, citations, bad_citations, exclusions = 0, 0, 0, []
    follow_tp, follow_fp, follow_fn, attribute_pending, metadata_pending = 0, 0, 0, 0, 0
    for gold, pred in zip(golds, predictions):
        result = pred.get("result", {})
        questions = result.get("questions", []) if not pred.get("failed") else []
        if not pred.get("failed"):
            require("questions" in result and "sessions" in result, "raw extraction result required; self-reported TP/FP/FN is unsupported")
        expected = {q["id"]: q for q in gold["questions"]}
        actual = {q["id"]: q for q in questions}
        require(len(actual) == len(questions), "duplicate prediction question ID")
        predicted_sessions = result.get("sessions", []) if not pred.get("failed") else []
        sessions = {s["id"]: s for s in predicted_sessions}
        require(len(sessions) == len(predicted_sessions), "duplicate prediction session ID")
        pairs, session_map = [], {}
        if expected and actual:
            alignment = alignments.get(gold["id"])
            require(isinstance(alignment, dict), "extraction predictions need human-reviewed alignment")
            if fixture and alignment.get("kind") == "synthetic_alignment":
                require(alignment.get("human_verified") is False, "synthetic alignment cannot claim human review")
            elif review_policy == "delegated_agent":
                require(alignment.get("agent_verified") is True and alignment.get("human_verified") is False,
                        "delegated alignment needs agent review without claiming human review")
                reviewed(alignment.get("review"))
                require(alignment["review"].get("reviewer_kind") == "agent" and bool(alignment["review"].get("basis")),
                        "delegated alignment review basis missing")
            else:
                require(alignment.get("human_verified") is True, "alignment needs human review")
                reviewed(alignment.get("review"))
            require(alignment.get("prediction_sha256") == digest(result), "alignment prediction hash mismatch")
            pairs = alignment.get("pairs", [])
            require(len({p["gold_id"] for p in pairs}) == len(pairs) and len({p["prediction_id"] for p in pairs}) == len(pairs), "alignment must be one-to-one")
            for pair in pairs:
                require(pair["gold_id"] in expected and pair["prediction_id"] in actual, "alignment references unknown question")
                g, p = expected[pair["gold_id"]], actual[pair["prediction_id"]]
                require(session_map.setdefault(g["session_id"], p["session_id"]) == p["session_id"], "alignment changes gold session")
            for pair in alignment.get("sessions", []):
                require(session_map.setdefault(pair["gold_id"], pair["prediction_id"]) == pair["prediction_id"], "inconsistent session alignment")
            require(set(session_map) <= {s["id"] for s in gold["sessions"]} and set(session_map.values()) <= set(sessions), "alignment references unknown session")
            require(len(set(session_map.values())) == len(session_map), "gold sessions merged by alignment")
        counts = prf(len(pairs), len(actual) - len(pairs), len(expected) - len(pairs))
        tp += counts["tp"]; fp += counts["fp"]; fn += counts["fn"]
        checks = [check("question_recall", counts["fn"] == 0, actual=counts["fn"]), check("question_precision", counts["fp"] == 0, actual=counts["fp"])]
        if gold["sample_kind"] == "negative":
            excluded = not pred.get("failed") and not actual and result.get("decision") == "EXCLUDED"
            exclusions.append(int(excluded))
            checks.append(check("correct_exclusion", excluded))
        from interview_intelligence.ingestion.snapshot import decode_source
        text = decode_source(inside(root, gold["source_path"]).read_bytes())
        def grounded(spans):
            return bool(spans) and all(type(s.get("start_char")) is int and type(s.get("end_char")) is int
                and 0 <= s["start_char"] < s["end_char"] <= len(text) and text[s["start_char"]:s["end_char"]] == s.get("quote") for s in spans)
        for question in actual.values():
            spans = question.get("source_spans", [])
            citations += 1
            valid = grounded(spans) and question.get("session_id") in sessions and question.get("raw_question") == "\n".join(s["quote"] for s in spans)
            bad_citations += int(not valid)
            checks.append(check(f"source.{question['id']}", valid, dimension="risk"))
        for pair in pairs:
            g, p = expected[pair["gold_id"]], actual[pair["prediction_id"]]
            for field in ("topic_l1", "topic_l2", "question_type"):
                if field in g:
                    if field in g.get("attributes_to_review", []):
                        attribute_pending += 1
                        checks.append(check(f"attribute.{g['id']}.{field}", None,
                                            reason="gold attribute awaits user review; excluded from accuracy"))
                        continue
                    matched_fields.setdefault(field, []).append(int(g[field] == p.get(field)))
                    checks.append(check(f"attribute.{g['id']}.{field}", g[field] == p.get(field), expected=g[field], actual=p.get(field)))
        for session in gold["sessions"]:
            metadata = sessions.get(session_map.get(session["id"]), {}).get("metadata", {})
            for field, value in session.get("metadata", {}).items():
                if field in session.get("metadata_to_review", []):
                    metadata_pending += 1
                    checks.append(check(f"metadata.{session['id']}.{field}", None,
                                        reason="gold metadata awaits user review; excluded from accuracy/hallucination count"))
                    continue
                correct = session["id"] in session_map and metadata.get(field) == value
                all_metadata.setdefault(field, []).append(int(correct))
                if value is not None:
                    metadata_fields.setdefault(field, []).append(int(correct))
                checks.append(check(f"metadata.{session['id']}.{field}", correct, expected=value, actual=metadata.get(field)))
                if value is None and metadata.get(field) is not None:
                    hallucinations += 1
        reverse = {p["prediction_id"]: p["gold_id"] for p in pairs}
        expected_links = {(p["source_id"], p["target_id"]) for p in gold.get("followups", [])}
        links = result.get("followups", []) if not pred.get("failed") else []
        require(len({(p["source_id"], p["target_id"]) for p in links}) == len(links), "duplicate prediction followup")
        predicted_links = {(reverse.get(p["source_id"], "prediction:" + p["source_id"]), reverse.get(p["target_id"], "prediction:" + p["target_id"])) for p in links}
        for index, link in enumerate(links):
            left, right = actual.get(link["source_id"]), actual.get(link["target_id"])
            valid = bool(left and right and left["session_id"] == right["session_id"] and grounded(link.get("evidence_spans", [])))
            citations += 1; bad_citations += int(not valid)
            checks.append(check(f"followup_evidence.{index}", valid, dimension="risk"))
        follow_tp += len(expected_links & predicted_links); follow_fp += len(predicted_links - expected_links); follow_fn += len(expected_links - predicted_links)
        checks.append(check("followup_links", expected_links == predicted_links, expected=sorted(expected_links), actual=sorted(predicted_links)))
        rows.append(_row(gold, pred, checks, **counts))
    metrics = {**prf(tp, fp, fn), "document_macro_f1": mean(r["scores"]["f1"] for r in rows if r["scores"]["f1"] is not None) if any(r["scores"]["f1"] is not None for r in rows) else None,
               "null_field_hallucination_count": hallucinations, "invalid_citation_count": bad_citations,
               "citation_valid_rate": (citations - bad_citations) / citations if citations else None,
               "negative_exclusion_accuracy": mean(exclusions) if exclusions else None,
               "followups": prf(follow_tp, follow_fp, follow_fn),
               "matched_attribute_accuracy": {k: mean(v) for k, v in matched_fields.items()},
               "matched_attribute_counts": {k: len(v) for k, v in matched_fields.items()},
               "matched_attribute_pending_review_count": attribute_pending,
               "metadata_accuracy": {k: mean(v) for k, v in metadata_fields.items()},
               "metadata_accuracy_including_null": {k: mean(v) for k, v in all_metadata.items()},
               "metadata_field_counts": {k: len(v) for k, v in metadata_fields.items()}}
    metrics["metadata_pending_review_count"] = metadata_pending
    return metrics, rows


def slices(rows):
    tags = sorted({tag for row in rows for tag in row["tags"]})
    return {tag: {"samples": len(items), "passed": sum(r["status"] == "PASS" for r in items),
                  "unknown": sum(r["status"] == "UNKNOWN" for r in items)}
            for tag in tags for items in [[r for r in rows if tag in r["tags"]]]}
