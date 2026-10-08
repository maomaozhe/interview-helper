"""Aggregate immutable module reports; missing and failing gates block release."""
import argparse
from pathlib import Path

from eval.common import SECTIONS, digest, inside, read_json, write_json
from eval.report import gates
from eval.validate_gold import require, validate_gold


def check_bundle(bundle, root):
    entries=bundle.get("modules",{})
    require(set(entries)<=set(SECTIONS),"UNKNOWN_MODULE")
    modules={};reference=None

    def check_report(section, entry):
        nonlocal reference
        try:
            require(section in SECTIONS,"UNKNOWN_MODULE")
            require(isinstance(entry,dict),"REPORT_ENTRY_MUST_BE_A_MAPPING")
            folder=inside(root,entry["report"])
            metadata=read_json(folder/"manifest.json")
            require(metadata["section"]==section and metadata["split"]=="test" and metadata["kind"]!="fixture","NON_RELEASE_REPORT")
            policy=metadata.get("review_policy","human")
            gold=validate_gold(inside(root,entry["dataset"]),section,review_policy=policy)
            require(digest(gold)==metadata["dataset_sha256"],"DATASET_HASH_MISMATCH")
            require(digest((folder/"predictions.jsonl").read_bytes())==metadata["predictions_sha256"],"PREDICTIONS_HASH_MISMATCH")
            require(gold["snapshot"]==metadata["snapshot"],"REPORT_SNAPSHOT_MISMATCH")
            if reference is None:reference=metadata["snapshot"]
            require(metadata["snapshot"]==reference,"BUNDLE_SNAPSHOT_MISMATCH")
            selected=[g for g in gold[section] if g["split"]=="test"]
            flag="agent_verified" if policy=="delegated_agent" else "human_verified"
            require(len(selected)==metadata["sample_count"] and all(g.get(flag) is True for g in selected),"REVIEW_ELIGIBILITY_MISMATCH")
            config=read_json(folder/"config.json")
            stored=read_json(folder/"gates.json")
            threshold_version=config.get("threshold_version","quality_gate_v2")
            require(not bundle.get("threshold_version") or threshold_version==bundle["threshold_version"],
                    "BUNDLE_THRESHOLD_VERSION_MISMATCH")
            derived=gates(section,read_json(folder/"metrics.json"),eligible=True,
                default_pipeline=config.get("default_pipeline","HYBRID"), threshold_version=threshold_version)
            require(all(stored.get(k)==derived[k] for k in ("status","checks","threshold_version")),"GATE_REPORT_MISMATCH")
            return {"status":derived["status"],"checks":derived["checks"],"samples":len(selected),
                "review_policy":policy,"dataset":entry["dataset"],"report":entry["report"],
                "dataset_sha256":metadata["dataset_sha256"],"predictions_sha256":metadata["predictions_sha256"]}
        except (ValueError,KeyError,TypeError,OSError) as error:
            return {"status":"INVALID","error":str(error)}

    for section in SECTIONS:
        modules[section]=check_report(section,entries[section]) if section in entries else {"status":"MISSING"}
    required=bundle.get("required_regressions",{})
    require(isinstance(required,dict),"REQUIRED_REGRESSIONS_MUST_BE_A_MAPPING")
    regressions={name:check_report(entry.get("section") if isinstance(entry,dict) else None,entry)
                 for name,entry in required.items()}
    blockers=[section for section,value in modules.items() if value["status"]!="PASSED"]
    blockers += ["regression:"+name for name,value in regressions.items() if value["status"]!="PASSED"]
    return {"schema":"evaluation_release_gate_v1","status":"BLOCKED" if blockers else "PASSED",
        "snapshot":reference,"modules":modules,"required_regressions":regressions,"blockers":blockers,
        "scope":"Frozen offline module gates only. Passing does not certify unseen-traffic performance or production capacity."}


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--bundle",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True);p.add_argument("--root",type=Path,default=Path("."))
    args=p.parse_args();result=check_bundle(read_json(args.bundle),args.root)
    write_json(args.output,result);print({"status":result["status"],"blockers":result["blockers"]})
    raise SystemExit(0 if result["status"]=="PASSED" else 1)
