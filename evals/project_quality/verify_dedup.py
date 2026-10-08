"""Add the production equivalence guard to every prior SAME observation.

First-stage calls stay in the ledger. This is a versioned regression replay,
not a new independent holdout or an end-to-end candidate-recall experiment.
"""
import argparse
from pathlib import Path
import time

from eval.budget import EvaluationBudget
from eval.collect import capped_client, error_code, phases
from eval.common import digest, provenance, read_json, read_jsonl, write_json, write_jsonl
from eval.snapshot import assert_snapshot,current_snapshot
from eval.validate_gold import validate_gold,require
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.dedup.provider import OpenAICompatibleJudge,JudgeDecision
from interview_intelligence.providers.gate import ModelCallGate

if __name__=="__main__":
    p=argparse.ArgumentParser()
    for name in ("dataset","predictions","output"):p.add_argument("--"+name,type=Path,required=True)
    args=p.parse_args();require(not args.output.exists(),"PREDICTION_OUTPUT_ALREADY_EXISTS")
    manifest=validate_gold(args.dataset,"dedup",review_policy="delegated_agent")
    rows=read_jsonl(args.predictions);prior=read_json(args.predictions.with_suffix('.collection.json'))
    require(digest(args.predictions.read_bytes())==prior["predictions_sha256"],"PREDICTION_HASH_MISMATCH")
    golds={g["id"]:g for g in manifest["dedup"] if g["split"]=="test"}
    require(set(golds)=={row["id"] for row in rows},"PREDICTION_IDS_MISMATCH")
    settings=load_settings();require(settings.model_lock_path.resolve()==Path('/app/runtime/model-call.lock'),"SHARED_LINUX_MODEL_GATE_REQUIRED")
    db=create_database(settings.database_url,create_tables=False)
    actual=current_snapshot(db,settings,as_of=manifest["snapshot"]["as_of"]);assert_snapshot(manifest["snapshot"],actual)
    budget=EvaluationBudget(160,400000);stamp=provenance()
    judge=OpenAICompatibleJudge(model=settings.judge_model,base_url=settings.model_base_url,client=capped_client(settings),
        call_gate=ModelCallGate(settings.model_lock_path,minimum_interval_seconds=settings.model_min_interval_seconds),
        budget=budget,verify_equivalence=True,max_attempts=1)
    count=0
    for row in rows:
        if row.get('failed') or row['result']['label']!='SAME': continue
        calls=[];judge.on_call=calls.append;g=golds[row['id']];started=time.perf_counter()
        old=row['result'];row['first_stage_result']=dict(old)
        try:
            guarded=judge.guard_same(g['left']['text'],g['right']['text'],JudgeDecision(
                decision=old['label'],reason_code=old['reason_code'],confidence=old['confidence']))
            row['result']={"label":guarded.decision,"reason_code":guarded.reason_code,"confidence":guarded.confidence}
        except Exception as e: row.update(failed=True,error_code=error_code(e))
        row['model_calls'].extend(calls);row['timings']=phases(row['model_calls'])
        row['elapsed_ms']+=(time.perf_counter()-started)*1000
        row['collection_protocol']='first_stage_v6_plus_'+judge.version
        count+=1
        if count%20==0:print({'verified':count},flush=True)
    assert_snapshot(actual,current_snapshot(db,settings,as_of=actual["as_of"]))
    write_jsonl(args.output,rows)
    write_json(args.output.with_suffix('.collection.json'),{"adapter":"dedup_equivalence_guard_regression", "samples":len(rows),
        "snapshot":actual,"review_policy":"delegated_agent","first_stage_predictions_sha256":digest(args.predictions.read_bytes()),
        "verified_same_rows":count,"budget":budget.summary(),"all_attempt_calls":prior['budget']['charged_calls']+budget.used_calls,
        "failed":sum(bool(row.get('failed')) for row in rows),"provenance":stamp,"completed_provenance":provenance(),
        "predictions_sha256":digest(args.output.read_bytes()),"latency_protocol":"Sum of first-stage and guard observations from two serial batches"})
    print({'samples':len(rows),'verified':count,'calls':budget.used_calls})
