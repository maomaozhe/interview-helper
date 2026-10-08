"""Independent frequency oracle over exported facts, not production SQL helpers."""
from collections import Counter
from eval.validate_gold import require


def frequency_list(facts, request):
    require(request.get("sort", "frequency") == "frequency", "frequency oracle only; score oracles require separately reviewed expectations")
    sessions = {s["id"]: s for s in facts["interviews"]}
    aliases = {"company": "company_normalized", "round": "round", "job_family": "job_family"}
    counts = Counter()
    for question in facts["questions"]:
        session = sessions[question["interview_id"]]
        if any(request.get(k) is not None and session.get(v) != request[k] for k, v in aliases.items()):
            continue
        position = request.get("position")
        if position:
            if any(word in position for word in ("后端", "服务端", "后台")):
                if session.get("job_family") != "BACKEND" or "java" in position.casefold() and "JAVA" not in session.get("language_tags", []):
                    continue
            elif session.get("position_normalized") != position:
                continue
        if request.get("language") and request["language"].upper() not in session.get("language_tags", []):
            continue
        if any(request.get(k) is not None and question.get(k) != request[k] for k in ("topic_l1", "topic_l2", "question_type")):
            continue
        if request.get("start_date") or request.get("end_date"):
            basis = request.get("date_basis", "BEST_AVAILABLE")
            observed = session.get("interview_date") if basis == "INTERVIEW" else session.get("publish_date") if basis == "PUBLISH" else session.get("interview_date") or session.get("publish_date")
            if not observed or request.get("start_date") and observed < request["start_date"] or request.get("end_date") and observed >= request["end_date"]:
                continue
        annotation = question.get("task_annotation")
        if request.get("coding_focus") or request.get("response_form") or request.get("annotation_status"):
            policy = request.get("annotation_status") or facts["snapshot"].get("task_annotation_policy", "VERIFIED")
            if not annotation:
                if policy != "UNKNOWN" or request.get("coding_focus") or request.get("response_form"):
                    continue
                counts[question["canonical_question_id"]] += 1
                continue
            if policy == "KNOWN" and (annotation["coding_focus"] == "UNKNOWN" or annotation["response_form"] == "UNKNOWN"):
                continue
            if policy in {"VERIFIED", "NEEDS_REVIEW"} and annotation["classification_status"] != policy:
                continue
            if policy == "UNKNOWN" and annotation["classification_status"] != "UNKNOWN" and annotation["coding_focus"] != "UNKNOWN" and annotation["response_form"] != "UNKNOWN":
                continue
            accepted_focus = {request.get("coding_focus")}
            if request.get("coding_focus") in {"ALGORITHM", "ENGINEERING"}:
                accepted_focus.add("MIXED")
            if request.get("coding_focus") and annotation["coding_focus"] not in accepted_focus:
                continue
            if request.get("response_form") and annotation["response_form"] != request["response_form"]:
                continue
        counts[question["canonical_question_id"]] += 1
    ordered = sorted(counts, key=lambda key: (-counts[key], key))
    # Oracle workspaces use a fresh eval actor with all questions UNSEEN.
    review_statuses = request.get("review_statuses", [])
    require(not review_statuses or review_statuses == ["UNSEEN"], "review oracle needs explicit actor state fixtures")
    top_n = request.get("top_n")
    result = ordered[:top_n] if top_n else ordered
    return {"ids": result, "counts": [counts[key] for key in result], "total": len(ordered), "result_total": len(result)}
