"""Select acceptance on calibration only, then evaluate a frozen test set."""
import argparse
import hashlib
import inspect
import math
from pathlib import Path

from eval.common import digest,read_json,write_json
from eval.validate_gold import require
from interview_intelligence.agent.jev import JevPlanner


THRESHOLDS=(.5,.7,.8,.85,.9,.95,.97,.98,.985,.99,.9925,.995,.9975,.999,.9995,1.)


def percentile(values,fraction):
    ordered=sorted(values)
    return ordered[max(0,math.ceil(len(ordered)*fraction)-1)] if ordered else None


def operating_point(rows,threshold):
    accepted=[r for r in rows if threshold is not None and r.get("candidate_plan") is not None
        and type(r.get("minimum_confidence")) in (int,float) and math.isfinite(r["minimum_confidence"])
        and r["minimum_confidence"]>=threshold]
    errors=sum(r["typed_correct"] is not True for r in accepted)
    return {"threshold":threshold,"samples":len(rows),"accepted_samples":len(accepted),"accepted_errors":errors,
        "accepted_coverage":len(accepted)/len(rows),"accepted_precision":(len(accepted)-errors)/len(accepted) if accepted else None}


def fit(rows):
    curve=[operating_point(rows,t) for t in THRESHOLDS]
    eligible=[r for r in curve if r["accepted_samples"]>=20 and r["accepted_errors"]==0]
    best=max(eligible,key=lambda r:(r["accepted_samples"],-r["threshold"])) if eligible else operating_point(rows,None)
    return best,curve


def load_probe(path,cases_path):
    probe=read_json(path);cases=read_json(cases_path)["cases"]
    require(probe["cases_sha256"]==digest(cases_path.read_bytes()),"CASE_HASH_MISMATCH")
    require({r["id"] for r in probe["rows"]}=={c["id"] for c in cases} and len(probe["rows"])==len(cases),"CASE_IDS_MISMATCH")
    by_id={c["id"]:c for c in cases}
    require(all(r["expected_plan_origin"]=="authored" and r["expected_plan"]==by_id[r["id"]]["expected_plan"]
                for r in probe["rows"]),"EXPECTED_PLAN_NOT_INDEPENDENT")
    return probe


def summarize(probe,threshold):
    rows=probe["rows"]
    latency=[r["elapsed_ms"] for r in rows]
    return {**operating_point(rows,threshold),"typed_correct":sum(r["typed_correct"] for r in rows),
        "typed_accuracy":sum(r["typed_correct"] for r in rows)/len(rows),
        "provider_failures":sum(any(c["status"]=="FAILED" for c in r["calls"]) for r in rows),
        "router_p50_ms":percentile(latency,.5),"router_p95_ms":percentile(latency,.95),
        "router_calls":len(probe["model_calls"]),"budget":probe["budget"],
        "frontier_call_savings_verified":False,"end_to_end_status":"NOT_RUN",
        "note":"Router timings include the shared gate and network. They are not complete query timings."}


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--calibration",type=Path,required=True);parser.add_argument("--test",type=Path)
    parser.add_argument("--artifact",type=Path,required=True);parser.add_argument("--report",type=Path,required=True)
    args=parser.parse_args();seal=read_json(args.dataset/"freeze.json")
    require(all(digest((args.dataset/name).read_bytes())==sha for name,sha in seal["file_hashes"].items()),"GOLD_HASH_MISMATCH")
    calibration=load_probe(args.calibration,args.dataset/"calibration.json")
    best,curve=fit(calibration["rows"])
    bindings={key:{r["decision"].get(key) for r in calibration["rows"] if r["decision"].get(key)}
              for key in ("provider_protocol","service_sha256","grammar_sha256")}
    revisions={c["model_revision"] for c in calibration["model_calls"] if c.get("model_revision")}
    require(len(revisions)==1 and all(len(values)==1 for values in bindings.values()),"MIXED_OR_MISSING_MODEL_CONTRACT")
    artifact={"schema":"jev_domain_calibration_v1","status":"PASSED" if best["threshold"] is not None else "NOT_ELIGIBLE",
        "provider":calibration["provider"],"model_revision":next(iter(revisions)),"decision_version":calibration["decision_version"],
        **{key:next(iter(values)) for key,values in bindings.items()},"independent_calibration":True,
        "decision_contract_sha256":hashlib.sha256(Path(inspect.getfile(JevPlanner)).read_bytes()).hexdigest(),
        "calibration_cases_sha256":calibration["cases_sha256"],"calibration_predictions_sha256":digest(args.calibration.read_bytes()),
        **best,"production_enabled":False,"rule":"At least 20 accepted with zero observed errors; no guarantee of zero future errors."}
    if args.artifact.exists():
        old=read_json(args.artifact)
        require(old==artifact,"DO_NOT_REFIT_FROZEN_CALIBRATION_ARTIFACT")
    else:write_json(args.artifact,artifact)
    report={"calibration":summarize(calibration,best["threshold"]),"calibration_curve":curve,"artifact":artifact,
        "independent_test":None,"release_decision":"disabled until calibration and independent test qualify"}
    if args.test:
        test=load_probe(args.test,args.dataset/"test.json")
        require(test["decision_version"]==calibration["decision_version"] and test["provider"]==calibration["provider"],"TEST_CONTRACT_CHANGED")
        report["independent_test"]=summarize(test,best["threshold"])
    write_json(args.report,report);print({"status":artifact["status"],"accepted":best["accepted_samples"],"errors":best["accepted_errors"],"threshold":best["threshold"]})
