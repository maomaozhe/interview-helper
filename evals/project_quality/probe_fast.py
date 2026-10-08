"""Real, serial local-model typed probes. Does not enable the Fast Path."""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from eval.budget import EvaluationBudget
from eval.common import digest, provenance, read_json, write_json
from eval.snapshot import current_snapshot, assert_snapshot
from interview_intelligence.agent.jev import JevPlanner
from interview_intelligence.agent.query_contract import QueryRequest
from interview_intelligence.agent.query_service import QueryRun
from interview_intelligence.config import load_settings
from interview_intelligence.domain.models import create_database
from interview_intelligence.providers.gate import ModelCallGate
from interview_intelligence.providers.runtime import RequestLimits, current_limits


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--cases",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise SystemExit("PREDICTION_OUTPUT_ALREADY_EXISTS")
    seal=args.cases.parent/"freeze.json"
    if seal.is_file():
        hashes=read_json(seal)["file_hashes"]
        if hashes.get(args.cases.name)!=digest(args.cases.read_bytes()): raise SystemExit("FROZEN_CASES_HASH_MISMATCH")
    settings=load_settings();db=create_database(settings.database_url,create_tables=False)
    if settings.model_lock_path.resolve()!=Path("/app/runtime/model-call.lock"): raise SystemExit("SHARED_LINUX_MODEL_GATE_REQUIRED")
    snapshot=current_snapshot(db,settings,as_of="2026-10-05")
    cases=read_json(args.cases); cases=cases["cases"] if isinstance(cases,dict) else cases
    budget=EvaluationBudget(len(cases),len(cases)*65536); observed=[]
    planner=JevPlanner(settings,db,ModelCallGate(settings.model_lock_path,minimum_interval_seconds=settings.model_min_interval_seconds))
    stamp=provenance();rows=[]
    for case in cases:
        context={"message":case["message"],"today":"2026-10-05","explicit_filters":case.get("explicit_filters",{}),
            "default_page_size":20,"session":case.get("session",{})}
        payload,quantities=planner.payload(context)
        run=QueryRun("probe-"+case["id"],QueryRequest(message=case["message"],request_id="probe-"+case["id"]),
            "probe",0,case.get("session",{}),RequestLimits(time.monotonic()+30,max_tokens=65536),(293,293,30))
        calls=[];planner.on_call=calls.append;budget.reserve(calls=1,tokens=65536)
        token=current_limits.set(run.limits)
        started=time.perf_counter()
        try:
            planner.plan(run,context)
        finally: current_limits.reset(token)
        elapsed=(time.perf_counter()-started)*1000
        usage=calls[0] if calls else {}
        budget.reconcile(calls=len(calls),tokens=usage.get("input_tokens",0)+(usage.get("output_tokens") or 0) if type(usage.get("input_tokens")) is int else None)
        choices=run.decision.get("choices",{});raw=run.decision.get("raw_choices",choices)
        default={key:"NONE" for key in payload["questions"]};default.update(sort="frequency",group_by="question",time="NONE",top_n="NONE")
        expected={**default,**case["expected"]}
        truth=planner.decode(expected,quantities,context,run.state) if "expected_plan" not in case else None
        candidate=None
        if choices:
            try: candidate=planner.decode(choices,quantities,context,run.state)
            except (ValueError,TypeError,KeyError,StopIteration): pass
        truth_json=case["expected_plan"] if "expected_plan" in case else truth.model_dump(mode="json") if truth else None
        plan_json=candidate.model_dump(mode="json") if candidate else None
        rows.append({"id":case["id"],"message":case["message"],"choices":choices,"raw_choices":raw,
            "expected":case["expected"],"raw_critical_correct":bool(choices) and all(raw.get(k)==v for k,v in case["expected"].items()),
            "typed_correct":bool(choices) and truth_json==plan_json,"expected_plan":truth_json,"candidate_plan":plan_json,
            "expected_plan_origin":"authored" if "expected_plan" in case else "development_decoder",
            "minimum_confidence":run.decision.get("minimum_confidence"),"decision":run.decision,"calls":calls,"elapsed_ms":elapsed})
        observed.extend(calls)
        if len(rows)%10==0: print({"completed":len(rows),"typed_correct":sum(r["typed_correct"] for r in rows)},flush=True)
    assert_snapshot(snapshot,current_snapshot(db,settings,as_of="2026-10-05"))
    output={"schema":"fast_decision_probe_v1","provider":settings.jev_provider,"decision_version":planner.version,
        "snapshot":snapshot,"cases_sha256":digest(args.cases.read_bytes()),
        "source_provenance":stamp,"completed_provenance":provenance(),"samples":len(rows),
        "raw_critical_correct":sum(r["raw_critical_correct"] for r in rows),"typed_correct":sum(r["typed_correct"] for r in rows),
        "budget":budget.summary(),"rows":rows,"model_calls":observed,"production_enabled":False}
    write_json(args.output,output); print({k:output[k] for k in ("samples","raw_critical_correct","typed_correct","budget")})
