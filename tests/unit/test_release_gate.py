from eval.common import digest,write_json
from eval.release import check_bundle


def report(tmp_path,*,success=1,snapshot=None):
    folder=tmp_path/"report";folder.mkdir()
    raw=b"{\"id\":\"sql-one\"}\n";(folder/"predictions.jsonl").write_bytes(raw)
    snapshot=snapshot or {"corpus_revision":1}
    gold={"snapshot":snapshot,"sql":[{"split":"test","human_verified":True}]}
    write_json(folder/"manifest.json",{"section":"sql","split":"test","kind":"corpus","review_policy":"human",
        "dataset_sha256":digest(gold),"predictions_sha256":digest(raw),"snapshot":snapshot,"sample_count":1})
    write_json(folder/"config.json",{})
    metrics={"task_success_rate":success,"unknown_assertions":0};write_json(folder/"metrics.json",metrics)
    from eval.report import gates
    write_json(folder/"gates.json",gates("sql",metrics,eligible=True))
    return gold,{"modules":{"sql":{"dataset":"fake-gold","report":"report"}}}


def test_missing_modules_and_failed_gate_never_pass(monkeypatch,tmp_path):
    gold,bundle=report(tmp_path,success=.9)
    monkeypatch.setattr("eval.release.validate_gold",lambda *a,**kw:gold)
    result=check_bundle(bundle,tmp_path)
    assert result["status"]=="BLOCKED" and result["modules"]["sql"]["status"]=="BELOW_GATE"
    assert result["modules"]["retrieval"]["status"]=="MISSING"


def test_forged_pass_cannot_override_derived_threshold(monkeypatch,tmp_path):
    gold,bundle=report(tmp_path,success=.9)
    monkeypatch.setattr("eval.release.validate_gold",lambda *a,**kw:gold)
    write_json(tmp_path/"report/gates.json",{"status":"PASSED","checks":[],"threshold_version":"quality_gate_v2"})
    result=check_bundle(bundle,tmp_path)
    assert result["modules"]["sql"]["status"]=="INVALID"
    assert result["modules"]["sql"]["error"]=="GATE_REPORT_MISMATCH"


def test_modified_observations_invalidate_previously_passed_report(monkeypatch,tmp_path):
    gold,bundle=report(tmp_path)
    monkeypatch.setattr("eval.release.validate_gold",lambda *a,**kw:gold)
    (tmp_path/"report/predictions.jsonl").write_text("{}\n",encoding="utf-8")
    result=check_bundle(bundle,tmp_path)
    assert result["modules"]["sql"]["status"]=="INVALID"
    assert result["modules"]["sql"]["error"]=="PREDICTIONS_HASH_MISMATCH"


def test_required_regression_blocks_even_when_all_primary_modules_pass(monkeypatch,tmp_path):
    import shutil
    from eval.report import gates
    gold,bundle=report(tmp_path)
    monkeypatch.setattr("eval.release.validate_gold",lambda *a,**kw:gold)
    monkeypatch.setattr("eval.release.SECTIONS",("sql",))
    shutil.copytree(tmp_path/"report",tmp_path/"regression")
    metrics={"task_success_rate":.9,"unknown_assertions":0}
    write_json(tmp_path/"regression/metrics.json",metrics)
    write_json(tmp_path/"regression/gates.json",gates("sql",metrics,eligible=True))
    bundle["required_regressions"]={"old-sql-scope":{"section":"sql","dataset":"fake-gold","report":"regression"}}
    result=check_bundle(bundle,tmp_path)
    assert result["modules"]["sql"]["status"]=="PASSED"
    assert result["required_regressions"]["old-sql-scope"]["status"]=="BELOW_GATE"
    assert result["blockers"]==["regression:old-sql-scope"]
    assert result["status"]=="BLOCKED"
