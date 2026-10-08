"""Fresh-input embedding + actual resolver/Judge shadow replay, always rolled back.

The designated pairs are seeded, previously reviewed development regressions.
Unreviewed other candidate decisions cannot establish overall SAME precision.
"""
from __future__ import annotations

import argparse
import copy
import math
import os
import random
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import func, select

from eval.budget import EvaluationBudget
from eval.collect import error_code, phases
from eval.common import digest, provenance, read_json, write_json, write_jsonl
from eval.snapshot import assert_snapshot, current_snapshot
from eval.validate_gold import require, validate_gold
from interview_intelligence.config import load_settings
from interview_intelligence.dedup.candidates import ElasticsearchCandidateHead, text_hash
from interview_intelligence.dedup.provider import ArkMultimodalEncoder, OpenAICompatibleEncoder, OpenAICompatibleJudge
from interview_intelligence.dedup.service import DedupService
from interview_intelligence.domain.models import CanonicalQuestion, EmbeddingCache, create_database
from interview_intelligence.providers.gate import ModelCallGate


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else None


def prepare(dataset, protocol):
    require(not protocol.exists(), "PROTOCOL_ALREADY_SEALED")
    gold = validate_gold(dataset, "dedup", review_policy="delegated_agent")
    rng, selected, used = random.Random(20261006), [], set()
    for label, count in (("SAME", 12), ("RELATED", 5), ("DIFFERENT", 3)):
        candidates = sorted((r for r in gold["dedup"] if r["split"] == "test" and r["label"] == label),
                            key=lambda r: r["id"])
        rng.shuffle(candidates)
        chosen = []
        for row in candidates:
            if row["left"]["id"] not in used:
                chosen.append(row)
                used.add(row["left"]["id"])
            if len(chosen) == count:
                break
        require(len(chosen) == count, "INSUFFICIENT_DISTINCT_INCOMING_IDENTITIES")
        selected.extend(chosen)
    write_json(protocol, {"version": "dedup_chain_dev_v1", "status": "sealed", "split": "dev",
        "eligible_for_release": False, "seed": 20261006, "parent_dataset": dataset.name,
        "parent_dataset_sha256": digest(gold), "snapshot": gold["snapshot"], "cases": selected,
        "selection": "Deterministic class-balanced existing source-pair regressions selected before this run.",
        "classes": dict(Counter(r["label"] for r in selected)), "candidate_limit": 10,
        "incoming_policy": "Fresh embedding of the reviewed canonical text; remove its existing canonical from the transaction-visible bank to avoid self-match leakage.",
        "comparison": "Share one freshly generated incoming vector, then run exact and forced HNSW plus exact delta through DedupService.resolve and batch Judge.",
        "limitations": ["Previously reviewed development pairs, not unseen natural incoming questions.",
            "Only designated pair truth is known; other candidate decisions do not prove global SAME precision.",
            "Exact designated-target merging is narrower than semantic group correctness; multiple valid existing canonicals can trigger NEW/needs_review.",
            "Bank is the actual active corpus minus the incoming canonical; candidate embeddings are pre-existing caches."]})
    write_json(protocol.with_suffix(".freeze.json"), {"protocol_sha256": digest(protocol.read_bytes())})
    print({"protocol": str(protocol), "cases": len(selected), "classes": dict(Counter(r["label"] for r in selected))})


def prepare_source_pairs(parent, snapshot, protocol):
    """Resolve occurrence identity without changing any reviewed pair or label."""
    require(not protocol.exists(), "PROTOCOL_ALREADY_SEALED")
    require(read_json(parent.with_suffix(".freeze.json"))["protocol_sha256"] == digest(parent.read_bytes()),
            "PARENT_PROTOCOL_CHANGED")
    plan, facts = copy.deepcopy(read_json(parent)), read_json(snapshot / "facts.json")
    require(read_json(snapshot / "manifest.json")["facts_sha256"] == digest(facts), "SNAPSHOT_HASH_MISMATCH")
    assert_snapshot(plan["snapshot"], facts["snapshot"])
    questions = {q["id"]: q for q in facts["questions"]}
    canonicals = {c["id"]: c for c in facts["canonicals"]}
    for case in plan["cases"]:
        for side in ("left", "right"):
            occurrence = questions[case[side]["id"]]
            require(occurrence["normalized_question"] == case[side]["text"], "SOURCE_PAIR_TEXT_CHANGED")
            canonical = canonicals[occurrence["canonical_question_id"]]
            case[side + "_canonical"] = {"id": canonical["id"], "original_text": canonical["canonical_text"]}
        case["incoming_topic_id"] = questions[case["left"]["id"]]["topic_id"]
        case["incoming_question_type"] = questions[case["left"]["id"]]["question_type"]
    plan.update(version="dedup_source_pair_shadow_dev_v2", parent_protocol_sha256=digest(parent.read_bytes()),
        facts_sha256=digest(facts), incoming_policy="Fresh incoming source-occurrence text. If its canonical differs from the target canonical, temporarily deactivate it. The target canonical is temporarily represented by the reviewed right occurrence text; both changes roll back.",
        target_policy="Bind actual Judge input to reviewed right-side text. Reuse its cached vector, or prepare one fresh shared target vector before both paths and stage it only in their rolled-back transactions.")
    plan["limitations"] += ["Occurrence IDs are explicitly resolved to current canonical IDs; existing RELATED pairs can already share an incorrectly merged canonical.",
        "The target representative is shadow-replaced, not an untouched production-bank replay; shared target preparation is counted separately from incoming resolution latency."]
    write_json(protocol, plan)
    write_json(protocol.with_suffix(".freeze.json"), {"protocol_sha256": digest(protocol.read_bytes())})
    print({"protocol": str(protocol), "cases": len(plan["cases"]), "labels_changed": 0})


def apply_source_pair_shadow(session, case, embedding_version, target_vector=None):
    incoming = session.get(CanonicalQuestion, case["left_canonical"]["id"])
    target = session.get(CanonicalQuestion, case["right_canonical"]["id"])
    require(incoming is not None and incoming.lifecycle == "ACTIVE", "INCOMING_BANK_ID_CHANGED")
    require(target is not None and target.lifecycle == "ACTIVE", "TARGET_BANK_ID_CHANGED")
    if incoming.id != target.id:
        incoming.lifecycle = "INACTIVE"
    target.canonical_text = case["right"]["text"]
    if target_vector is not None:
        key = text_hash(case["right"]["text"])
        cached = session.scalar(select(EmbeddingCache).where(EmbeddingCache.text_hash == key,
                                EmbeddingCache.embedding_version == embedding_version))
        if cached is None:
            session.add(EmbeddingCache(text_hash=key, embedding_version=embedding_version,
                        dimension=len(target_vector), vector=target_vector))
    session.flush()


class FreshIncomingDeduper(DedupService):
    """Inject the just-measured vector; all candidate selection/Judge code is production."""
    def __init__(self, *, incoming, vector, **kwargs):
        super().__init__(**kwargs)
        self.incoming, self.vector = incoming, vector

    def _embedding(self, session, text):
        return self.vector if text == self.incoming else super()._embedding(session, text)


def collect(protocol, output, *, max_calls, max_tokens):
    partial = output.with_suffix(".partial.jsonl")
    require(not output.exists() and not partial.exists(), "OBSERVATIONS_ALREADY_EXIST")
    require(read_json(protocol.with_suffix(".freeze.json"))["protocol_sha256"] == digest(protocol.read_bytes()),
            "SEALED_PROTOCOL_CHANGED")
    plan, settings = read_json(protocol), load_settings()
    require(plan["version"] == "dedup_source_pair_shadow_dev_v2", "SOURCE_PAIR_IDENTITY_PROTOCOL_REQUIRED")
    require(os.name != "nt" and settings.model_lock_path.resolve() == Path("/app/runtime/model-call.lock"),
            "SHARED_LINUX_MODEL_GATE_REQUIRED")
    database = create_database(settings.database_url, create_tables=False)
    expected = plan["snapshot"]
    observe = lambda: current_snapshot(database, settings, as_of=expected["as_of"])
    assert_snapshot(expected, observe())
    gate = ModelCallGate(settings.model_lock_path, minimum_interval_seconds=settings.model_min_interval_seconds)
    budget, calls, rows = EvaluationBudget(max_calls, max_tokens), [], []
    encoder_class = ArkMultimodalEncoder if settings.embedding_model == "doubao-embedding-vision" else OpenAICompatibleEncoder
    common = {"api_key": settings.model_api_key, "base_url": settings.model_base_url,
        "budget": budget, "call_gate": gate, "on_call": calls.append,
        "timeout_seconds": settings.model_request_timeout_seconds}
    encoder = encoder_class(model=settings.embedding_model, dimension=settings.embedding_dimension, **common)
    judge = OpenAICompatibleJudge(model=settings.judge_model, verify_equivalence=False, **common)
    head = ElasticsearchCandidateHead(settings.elasticsearch_url)
    start = time.perf_counter()
    head.refresh()
    cold_head_ms = (time.perf_counter() - start) * 1000
    with database.session() as session:
        bank = list(session.scalars(select(CanonicalQuestion).where(CanonicalQuestion.lifecycle == "ACTIVE")))
        bank_size = len(bank)
        original_texts = {item.id: item.canonical_text for item in bank}
        hashes = {text_hash(item.canonical_text) for item in bank}
        cached = set(session.scalars(select(EmbeddingCache.text_hash).where(
            EmbeddingCache.embedding_version == encoder.version, EmbeddingCache.text_hash.in_(hashes))))
        require(cached == hashes, "INCOMPLETE_CANDIDATE_EMBEDDING_CACHE")
        before_counts = {"canonicals": session.scalar(select(func.count()).select_from(CanonicalQuestion)),
                         "embeddings": session.scalar(select(func.count()).select_from(EmbeddingCache))}
    for case in plan["cases"]:
        for side in ("left", "right"):
            identity = case[side + "_canonical"]
            require(original_texts.get(identity["id"]) == identity["original_text"], "PAIR_ID_OR_TEXT_CHANGED")
    started, source = time.perf_counter(), provenance()
    for case in plan["cases"]:
        assert_snapshot(expected, observe())
        incoming, incoming_id, target = case["left"]["text"], case["left_canonical"]["id"], case["right_canonical"]["id"]
        target_start, target_call_start, target_vector, target_error = time.perf_counter(), len(calls), None, None
        try:
            if text_hash(case["right"]["text"]) not in cached:
                target_vector = encoder.embed(case["right"]["text"])
        except Exception as error:
            target_error = error_code(error)
        target_preparation_ms = (time.perf_counter() - target_start) * 1000
        target_calls = calls[target_call_start:]
        call_start, start = len(calls), time.perf_counter()
        vector, failure = None, target_error
        try:
            vector = encoder.embed(incoming)
        except Exception as error:
            failure = error_code(error)
        embedding_ms, embedding_calls = (time.perf_counter() - start) * 1000, calls[call_start:]
        paths = {}
        for backend in ("exact", "hnsw"):
            call_start, start = len(calls), time.perf_counter()
            row = {"failed": failure is not None, "error_code": failure, "candidate_ids": [],
                   "target_in_candidates": False, "decision": None, "needs_review": None}
            try:
                if vector is None or failure is not None:
                    raise ValueError("SHARED_VECTOR_PREPARATION_FAILED")
                with database.session() as session:
                    session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                    try:
                        apply_source_pair_shadow(session, case, encoder.version, target_vector)
                        resolver = FreshIncomingDeduper(incoming=incoming, vector=vector, encoder=encoder,
                            judge=judge, candidate_limit=plan["candidate_limit"], candidate_head=head,
                            candidate_backend=backend)
                        resolved = resolver.resolve(session, incoming, case["incoming_topic_id"], case["incoming_question_type"])
                        judgements = resolved.evidence["judgements"]
                        target_judgement = next((item for item in judgements if item["candidate_id"] == target), None)
                        row.update(failed=False, error_code=None, decision=resolved.decision,
                            canonical_id=resolved.canonical.id, candidate_ids=resolved.candidate_ids,
                            target_in_candidates=target in resolved.candidate_ids,
                            target_judgement=target_judgement, needs_review=resolved.needs_review,
                            judgements=judgements, candidate_search=resolved.evidence["candidate_search"],
                            same_candidate_count=sum(item["decision"] == "SAME" for item in judgements),
                            designated_target_merged=resolved.decision == "SAME" and resolved.canonical.id == target)
                    finally:
                        session.rollback()
            except Exception as error:
                row.update(failed=True, error_code=error_code(error))
            resolution_ms = (time.perf_counter() - start) * 1000
            row.update(resolution_ms=resolution_ms, complete_ms=embedding_ms + resolution_ms,
                model_calls=calls[call_start:], timings=phases(calls[call_start:]),
                transaction="rolled_back", effective_bank_size=bank_size - int(incoming_id != target))
            paths[backend] = row
        rows.append({"id": case["id"], "split": "dev", "snapshot": expected, "known_pair_label": case["label"],
            "incoming": case["left"], "target": case["right"], "embedding_ms": embedding_ms,
            "incoming_canonical_id": incoming_id, "target_canonical_id": target,
            "target_preparation_ms": target_preparation_ms, "target_preparation_calls": target_calls,
            "target_representative": "reviewed_source_pair_shadow",
            "embedding_calls": embedding_calls, "fresh_input_vector_sha256": digest(vector) if vector else None,
            "paths": paths, "failed": any(p["failed"] for p in paths.values())})
        assert_snapshot(expected, observe())
        write_jsonl(partial, rows)
        print({"completed": len(rows), "failed": sum(r["failed"] for r in rows), "calls": budget.used_calls}, flush=True)
    assert_snapshot(expected, observe())
    with database.session() as session:
        after_counts = {"canonicals": session.scalar(select(func.count()).select_from(CanonicalQuestion)),
                        "embeddings": session.scalar(select(func.count()).select_from(EmbeddingCache))}
        require(before_counts == after_counts, "SHADOW_REPLAY_PERSISTED_DATA")
        require(all(session.get(CanonicalQuestion, c["left_canonical"]["id"]).lifecycle == "ACTIVE" for c in plan["cases"]),
                "SHADOW_REPLAY_CHANGED_BANK")
        after_texts = {item.id: item.canonical_text for item in session.scalars(select(CanonicalQuestion).where(
            CanonicalQuestion.lifecycle == "ACTIVE"))}
        require(original_texts == after_texts, "SHADOW_REPLAY_CHANGED_TEXT")
    metrics = {}
    for backend in ("exact", "hnsw"):
        positive = [r for r in rows if r["known_pair_label"] == "SAME"]
        paths = [r["paths"][backend] for r in rows]
        metrics[backend] = {"cases": len(rows), "failed": sum(p["failed"] for p in paths),
            "designated_same_targets": len(positive),
            "designated_same_target_recalled": sum(r["paths"][backend]["target_in_candidates"] for r in positive),
            "designated_same_target_judged_same": sum((r["paths"][backend].get("target_judgement") or {}).get("decision") == "SAME" for r in positive),
            "designated_same_target_merged": sum(r["paths"][backend].get("designated_target_merged", False) for r in positive),
            "ambiguous_new_needs_review": sum(p.get("needs_review") is True for p in paths),
            "complete_p50_ms": percentile([p["complete_ms"] for p in paths], .5),
            "complete_p95_ms": percentile([p["complete_ms"] for p in paths], .95),
            "resolution_p95_ms": percentile([p["resolution_ms"] for p in paths], .95),
            "overall_same_precision": None, "overall_semantic_success": None}
    write_jsonl(output, rows)
    receipt = {"kind": "dedup_complete_chain_shadow_dev", "eligible_for_release": False,
        "snapshot": expected, "protocol_sha256": digest(protocol.read_bytes()),
        "predictions_sha256": digest(output.read_bytes()), "cases": len(rows), "bank_size": bank_size,
        "embedding_version": encoder.version, "judge_version": judge.version, "ann_index": head.physical,
        "cold_head_refresh_ms": cold_head_ms, "metrics": metrics, "budget": budget.summary(),
        "job_elapsed_ms": (time.perf_counter() - started) * 1000, "provenance": source,
        "model_calls": calls, "before_counts": before_counts, "after_counts": after_counts,
        "before_bank_sha256": digest(original_texts), "after_bank_sha256": digest(after_texts),
        "published_canonicals": 0, "published_embeddings": 0, "limitations": plan["limitations"],
        "latency_scope": "One freshly generated incoming embedding is shared across both paths and included in each complete latency. Resolution includes real DB queries, cached candidate vectors, shadow target staging, candidate search, batch Judge, transaction flush and rollback. Head refresh and any shared target-vector preparation are separate; fixed path order can affect interval wait."}
    write_json(output.with_suffix(".collection.json"), receipt)
    print({"metrics": metrics, "budget": budget.summary(), "published_canonicals": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "prepare-source-pairs", "collect"))
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--parent-protocol", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-calls", type=int, default=90)
    parser.add_argument("--max-tokens", type=int, default=2000000)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.dataset, args.protocol)
    elif args.action == "prepare-source-pairs":
        prepare_source_pairs(args.parent_protocol, args.snapshot, args.protocol)
    else:
        collect(args.protocol, args.output, max_calls=args.max_calls, max_tokens=args.max_tokens)
