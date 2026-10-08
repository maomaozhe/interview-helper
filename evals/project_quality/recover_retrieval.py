"""Recollect budget-blocked rows without erasing the original failed experiment.

Only unchanged relevance/query/index inputs and unchanged reranker source may
be reused. The output states which observations came from each collection.
"""
import argparse
from pathlib import Path

from eval.budget import EvaluationBudget
from eval.collect import RetrievalCollector, capped_client, collect_rows
from eval.common import digest, provenance, read_json, read_jsonl, write_json, write_jsonl
from eval.snapshot import assert_snapshot, current_snapshot
from eval.validate_gold import require, validate_gold
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.dedup.provider import ArkMultimodalEncoder
from interview_intelligence.search.reranker import LLMReranker
from interview_intelligence.providers.gate import ModelCallGate


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    for key in ("dataset","original-dataset","predictions","output"): parser.add_argument("--"+key,type=Path,required=True)
    parser.add_argument("--max-calls",type=int,required=True); parser.add_argument("--max-tokens",type=int,required=True)
    args=parser.parse_args(); require(not args.output.exists(),"PREDICTION_OUTPUT_ALREADY_EXISTS")
    manifest=validate_gold(args.dataset,"retrieval",review_policy="delegated_agent")
    original_manifest=validate_gold(args.original_dataset,"retrieval",review_policy="delegated_agent")
    def labels(data): return [{key:row.get(key) for key in ("id","query","filters","relevance")} for row in data["retrieval"] if row["split"]=="test"]
    require(labels(manifest)==labels(original_manifest),"RECOVERY_GOLD_INPUT_CHANGED")
    original=read_jsonl(args.predictions); receipt=read_json(args.predictions.with_suffix(".collection.json"))
    require(digest(args.predictions.read_bytes())==receipt["predictions_sha256"],"RECOVERY_PREDICTION_HASH_MISMATCH")
    current=provenance(); reranker_path="src/interview_intelligence/search/reranker.py"
    require(current["file_hashes"][reranker_path]==receipt["provenance"]["file_hashes"][reranker_path],"RECOVERY_RERANKER_CHANGED")
    retry_ids={row["id"] for row in original if any(p.get("failed") for p in row["pipelines"].values())}
    require(all(p.get("error_code")=="EVALUATION_BUDGET_EXCEEDED" for row in original for p in row["pipelines"].values() if p.get("failed")),"RECOVERY_ONLY_BUDGET_FAILURES")
    settings=load_settings(); require(settings.model_lock_path.resolve()==Path("/app/runtime/model-call.lock"),"SHARED_LINUX_MODEL_GATE_REQUIRED")
    db=create_database(settings.database_url,create_tables=False)
    snapshot=current_snapshot(db,settings,as_of=manifest["snapshot"]["as_of"]); assert_snapshot(manifest["snapshot"],snapshot)
    budget=EvaluationBudget(args.max_calls,args.max_tokens)
    gate=ModelCallGate(settings.model_lock_path,minimum_interval_seconds=settings.model_min_interval_seconds)
    collector=RetrievalCollector(db,settings,manifest,
        ArkMultimodalEncoder(model=settings.embedding_model,dimension=settings.embedding_dimension,api_key=settings.model_api_key,
            base_url=settings.model_base_url,call_gate=gate,budget=budget),
        LLMReranker(model=settings.reranker_model,api_key=settings.model_api_key,base_url=settings.model_base_url,
            client=capped_client(settings),call_gate=gate,budget=budget))
    def call(gold,calls):
        collector.encoder.on_call=collector.reranker.on_call=calls.append
        return collector(gold,calls)
    new=collect_rows([g for g in manifest["retrieval"] if g["id"] in retry_ids],call,snapshot,
        lambda:current_snapshot(db,settings,as_of=snapshot["as_of"]))
    by_id={row["id"]:row for row in new}; result=[]
    for previous in original:
        row=by_id.get(previous["id"],previous)
        row["failed"]=any(p.get("failed") for p in row["pipelines"].values())
        row["collection_attempt"]="budget_fix_recollection" if row["id"] in by_id else "original_reused"
        result.append(row)
    write_jsonl(args.output,result)
    write_json(args.output.with_suffix(".collection.json"),{"adapter":"retrieval_budget_recovery","snapshot":snapshot,
        "review_policy":"delegated_agent","samples":len(result),"failed":sum(row["failed"] for row in result),
        "original_predictions_sha256":digest(args.predictions.read_bytes()),"original_receipt":str(args.predictions.with_suffix('.collection.json')),
        "original_failed_rows":len(retry_ids),"recollected_ids":sorted(retry_ids),"budget":budget.summary(),
        "all_attempt_charged_calls":receipt["budget"]["charged_calls"]+budget.used_calls,
        "provenance":current,"completed_provenance":provenance(),"predictions_sha256":digest(args.output.read_bytes()),
        "latency_protocol":"Uncontended sequential observations from two batches; no failed samples removed from final quality denominator"})
    print({"samples":len(result),"recollected":len(new),"failed":sum(row["failed"] for row in result),"budget":budget.summary()})
