"""Seal question-disjoint challenge candidates, without assigning task labels."""
import random
import re
from pathlib import Path

from eval.common import digest, read_json, read_jsonl, write_json, write_jsonl
from interview_intelligence.agent.task_context import compile_task_context
from interview_intelligence.ingestion.snapshot import decode_source


def prepare(root=Path("data/reports/quality-refinement-20261006")):
    snapshot = Path("data/reports/resume-quality-snapshot-20261005-v1")
    facts = read_json(snapshot / "facts.json")
    assert facts["snapshot"]["corpus_revision"] == 293
    previous = read_jsonl(Path("data/gold/evaluation-agent-reviewed-20261005-v5/task_labels.jsonl"))
    previous += read_jsonl(Path("data/gold/task-generalization-agent-20261006-v2/task_labels.jsonl"))
    excluded = {x["occurrence_id"] for x in previous}
    canonical = {q["canonical_question_id"] for q in facts["questions"] if q["id"] in excluded}
    bare = lambda x: re.sub(r"\W", "", re.sub(r"^\s*\d+[.、)）]?", "", x)).casefold()
    texts = {bare(x["raw_question"]) for x in previous}
    questions = [q for q in facts["questions"] if q["id"] not in excluded
                 and q["canonical_question_id"] not in canonical and bare(q["raw_question"]) not in texts]
    random.Random(202610061).shuffle(questions)
    target = re.compile(r"手写|手撕|编码|写.{0,8}(SQL|代码)|实现.{0,12}(类|线程|队列|锁|缓存|函数|Promise)|阻塞队列|生产者.{0,5}消费者", re.I)
    boundary = re.compile(r"实现|设计|适配|优化|管理|架构|模块|组件")
    chosen, seen_ids, seen_texts = [], set(), set()
    for name, accept, count in (
        ("construct_or_code_cue", lambda q: bool(target.search(q["raw_question"])), 48),
        ("mechanism_or_design_cue", lambda q: bool(boundary.search(q["raw_question"])), 112),
        ("control", lambda q: True, 40),
    ):
        selected = 0
        for q in questions:
            if q["canonical_question_id"] in seen_ids or bare(q["raw_question"]) in seen_texts or not accept(q):
                continue
            chosen.append((name, q)); seen_ids.add(q["canonical_question_id"]); seen_texts.add(bare(q["raw_question"]))
            selected += 1
            if selected == count:
                break
        assert selected == count, (name, selected)
    documents = {d["revision_id"]: d for d in facts["documents"]}
    rows = []
    for i, (name, q) in enumerate(chosen):
        document = documents[q["source_spans"][0]["revision_id"]]
        raw = (snapshot / document["source_path"]).read_bytes()
        assert digest(raw) == document["source_hash"]
        rows.append({"review_index": i, "selection": name, "occurrence_id": q["id"],
                     "canonical_question_id": q["canonical_question_id"], "raw_question": q["raw_question"],
                     "source_hash": document["source_hash"], "source_path": document["source_path"],
                     "source_spans": q["source_spans"], **compile_task_context(decode_source(raw), q["source_spans"])})
    queue = root / "engineering-challenge.queue.jsonl"
    assert not queue.exists()
    write_jsonl(queue, rows)
    write_json(root / "engineering-challenge.queue.protocol.json", {
        "queue_sha256": digest(queue.read_bytes()), "source_snapshot": str(snapshot), "snapshot": facts["snapshot"],
        "samples": 200, "seed": 202610061, "buckets": {"code_cue": 48, "design_cue": 112, "control": 40},
        "prior_task_occurrence_canonical_and_raw_disjoint": True, "source_disjoint": False,
        "review_policy": "pending_delegated_agent", "prediction_calls": 0,
    })
    print({"queued": len(rows), "queue_sha256": digest(queue.read_bytes())})


if __name__ == "__main__":
    prepare()
