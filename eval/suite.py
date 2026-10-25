"""One reproducible run plan, with resumable collection and sealed scoring."""
from __future__ import annotations

import html
import os
from contextlib import contextmanager
from pathlib import Path

from eval.common import digest, read_json, write_json
from eval.preflight import load_suite, preflight
from eval.validate_gold import load_dataset, require


@contextmanager
def run_lock(output: Path):
    """OS-owned lock: a crashed process releases it without deleting evidence."""
    handle = (output / ".run.lock").open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("SUITE_ALREADY_RUNNING") from error
        yield
    finally:
        handle.close()


def inputs_identity(suite):
    from eval.run import scorer_identity

    modules = []
    for module in suite["modules"]:
        entry = {"id": module["id"], "dataset": digest(load_dataset(module["dataset"]))}
        for field in ("predictions", "alignments", "answer_reviews"):
            if module.get(field):
                if field == "alignments" and "collect" in module:
                    # The reviewer fills this predeclared slot after collection.
                    # Its hash is frozen after successful, verified scoring.
                    continue
                entry[field] = digest(Path(module[field]).read_bytes())
        if module.get("predictions"):
            receipt = Path(module["predictions"]).with_suffix(".collection.json")
            entry["receipt"] = digest(receipt.read_bytes()) if receipt.is_file() else None
        collection = module.get("collect", {})
        export = collection.get("options", {}).get("snapshot_export")
        if export:
            entry["snapshot_export"] = {
                name: digest((Path(export) / name).read_bytes()) for name in ("manifest.json", "facts.json")}
        modules.append(entry)
    files = {filename: digest(Path(filename).read_bytes()) for component in suite["candidate"]["components"].values()
             for filename in component.get("files", {})}
    return {"suite": digest(suite), "inputs": modules, "scorer": scorer_identity(), "candidate_files": files}


def collection_arguments(module, output, candidate_path, *, resume):
    args = ["--dataset", module["dataset"], "--section", module["section"],
            "--split", module["split"], "--review-policy", module["review_policy"],
            "--adapter", module["collect"]["adapter"], "--output", str(output),
            "--candidate-binding", str(candidate_path)]
    for name, value in module["collect"].get("options", {}).items():
        flag = "--" + name.replace("_", "-")
        if isinstance(value, bool):
            if value:
                args.append(flag)
            elif name == "dedup_verify_equivalence":
                args.append("--no-dedup-verify-equivalence")
        elif value is not None:
            args.extend([flag, str(value)])
    if resume:
        args.append("--resume")
    return args


def _scope(module):
    if "predictions" in module:
        return "stored_predictions"
    return "http_end_to_end" if module["collect"]["adapter"] == "http" else "component"


def write_summary(output, suite, state):
    modules = []
    for module in suite["modules"]:
        entry = state["modules"].get(module["id"], {})
        report = (output / entry["report"]).resolve() if entry.get("report") else None
        report_link = entry.get("report")
        try:
            require(report is None or report.is_relative_to(output), "SUITE_REPORT_PATH_ESCAPE")
            gate = read_json(report / "gates.json") if report else {}
        except (ValueError, OSError, TypeError):
            gate, report_link = {"status": "INVALID"}, None
        modules.append({"id": module["id"], "section": module["section"],
                        "state": entry.get("state", "PENDING"), "report": report_link,
                        "scope": _scope(module), "gate": gate.get("status", "NOT_RUN")})
    gates = [m["gate"] for m in modules]
    quality = "NOT_ELIGIBLE" if "NOT_ELIGIBLE" in gates else "BLOCKED" if any(
        gate != "PASSED" for gate in gates) else "MODULE_GATES_PASS"
    if state["status"] != "COMPLETED":
        quality = "NOT_EVALUATED"
    summary = {"schema_version": "evaluation_suite_report_v1", "suite_id": suite["id"],
               "execution_status": state["status"], "evidence_status": "VERIFIED" if state["status"] == "COMPLETED" else "INCOMPLETE",
               "quality_status": quality, "release_status": "NOT_CHECKED", "candidate": suite["candidate"],
               "user_task_quality": "NOT_ESTABLISHED", "modules": modules,
               "limitations": ["离线流程通过不代表真实服务或模型质量通过。",
                               "已验证范围是数据集中的断言；真实用户任务是否达成仍需独立案例。",
                               "候选声明、观测到的模型版本与部署身份是不同证据；正式发布须另行严格门禁。"]}
    write_json(output / "report.json", summary)
    links = []
    for module in modules:
        label = html.escape(f"{module['id']} · {module['state']} · {module['gate']}")
        if module["report"]:
            label = f'<a href="{html.escape(module["report"] + "/summary.html", quote=True)}">{label}</a>'
        links.append(f"<li>{label}</li>")
    document = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>评测运行</title>'
                '<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 24px;line-height:1.7}</style>'
                f'<h1>{html.escape(suite["id"])}</h1><p>执行：{state["status"]}；质量：{quality}；发布门禁：未检查。</p>'
                '<p>流程可用与真实回答有效分别验收。下面的通过仅覆盖给定数据和断言。</p><ul>'
                + "".join(links) + '</ul><p><a href="report.json">运行摘要</a> · <a href="preflight.json">环境预检</a></p></html>')
    (output / "index.html").write_text(document, encoding="utf-8")
    return summary


def write_bundle(output, suite, state):
    """Only reference sealed gold; no copy of a legacy release is silently promoted."""
    modules = {module["id"]: module for module in suite["modules"]}
    release = suite.get("release", {})
    primary = release.get("modules", {})
    if not primary:
        for module in suite["modules"]:
            primary.setdefault(module["section"], module["id"])
    def entry(module_id):
        module = modules[module_id]
        report = state["modules"][module_id]["report"]
        return {"section": module["section"], "report": report, "dataset": report + "/gold",
                "review_policy": module["review_policy"]}
    bundle = {"schema": "project_quality_bundle_v1", "candidate": suite["candidate"],
              "modules": {section: entry(module_id) for section, module_id in primary.items()},
              "required_regressions": {name: entry(module_id) for name, module_id in
                                       release.get("required_regressions", {}).items()},
              "scope": "Dataset assertion coverage only; user task quality is not established."}
    write_json(output / "release-bundle.json", bundle)


def compare_suites(before, after):
    from eval.compare import compare
    left, right = read_json(Path(before) / "report.json"), read_json(Path(after) / "report.json")
    require(left["execution_status"] == right["execution_status"] == "COMPLETED", "SUITE_NOT_COMPLETED")
    old, new = ({m["id"]: m for m in report["modules"]} for report in (left, right))
    require(set(old) == set(new), "SUITE_MODULE_SET_CHANGED")
    comparisons = {key: compare(Path(before) / old[key]["report"], Path(after) / new[key]["report"]) for key in old}
    return {"compatible": all(value["compatible"] for value in comparisons.values()),
            "quality_comparable": all(value["quality_comparable"] for value in comparisons.values()), "modules": comparisons,
            "scope": "Changes in dataset assertions; not a claim about unseen user questions."}


def run_suite(suite_path: Path, output: Path, *, resume=False, allow_live=False):
    suite = load_suite(suite_path)
    checked = preflight(suite, allow_live=allow_live)
    require(checked["status"] == "PASS", "PREFLIGHT_BLOCKED: run python -m eval preflight for details")
    from eval.release import verify_report
    from eval.run import run_section
    identity = inputs_identity(suite)
    output = Path(output).resolve()
    if not resume:
        output.mkdir(parents=True, exist_ok=False)
    require(output.is_dir(), "SUITE_OUTPUT_MISSING")
    with run_lock(output):
        checkpoint = output / "checkpoint.json"
        if resume:
            require(checkpoint.is_file(), "SUITE_CHECKPOINT_MISSING")
            state = read_json(checkpoint)
            require(state.get("schema_version") == "evaluation_suite_checkpoint_v1", "SUITE_CHECKPOINT_SCHEMA_MISMATCH")
            require(state.get("identity") == identity, "SUITE_INPUT_OR_VERSION_CHANGED")
            require(read_json(output / "candidate.json") == suite["candidate"], "SUITE_CANDIDATE_FILE_CHANGED")
        else:
            state = {"schema_version": "evaluation_suite_checkpoint_v1", "identity": identity,
                     "status": "RUNNING", "modules": {}}
            write_json(output / "suite.json", suite)
            write_json(output / "candidate.json", suite["candidate"])
            write_json(checkpoint, state)
        write_json(output / "preflight.json", checked)
        try:
            for module in suite["modules"]:
                entry = state["modules"].setdefault(module["id"], {"state": "PENDING"})
                module_root = (output / "modules" / module["id"]).resolve()
                require(module_root.is_relative_to(output), "SUITE_MODULE_PATH_ESCAPE")
                prediction = Path(module["predictions"]) if module.get("predictions") else module_root / "predictions.jsonl"
                if entry.get("alignment_sha256"):
                    require(Path(module["alignments"]).is_file()
                            and digest(Path(module["alignments"]).read_bytes()) == entry["alignment_sha256"],
                            "APPROVED_ALIGNMENT_CHANGED")
                if entry["state"] == "COMPLETED":
                    report = (output / entry["report"]).resolve()
                    require(report.is_relative_to(output), "SUITE_REPORT_PATH_ESCAPE")
                    verified = verify_report(report, expected_candidate=suite["candidate"], strict=True)
                    require(verified["status"] == "VERIFIED", "COMPLETED_REPORT_INVALID")
                    require(verified["metadata"].get("section") == module["section"]
                            and verified["config"].get("suite") == {"id": suite["id"], "sha256": identity["suite"]}
                            and verified["config"].get("protocol", {}).get("module_id") == module["id"],
                            "SUITE_REPORT_BINDING_MISMATCH")
                    frozen = next(item for item in identity["inputs"] if item["id"] == module["id"])
                    require(verified["metadata"].get("dataset_sha256") == frozen["dataset"]
                            and verified["metadata"].get("predictions_sha256") == digest(prediction.read_bytes())
                            and verified["metadata"].get("alignments_sha256") == entry.get("alignment_sha256")
                            and verified["metadata"].get("answer_reviews_sha256") == frozen.get("answer_reviews"),
                            "SUITE_REPORT_INPUT_MISMATCH")
                    receipt = prediction.with_suffix(".collection.json")
                    require((not receipt.exists() and "collection" not in verified["config"])
                            or (receipt.is_file() and verified["config"].get("collection") == read_json(receipt)),
                            "SUITE_REPORT_COLLECTION_MISMATCH")
                    continue
                module_root.mkdir(parents=True, exist_ok=True)
                if "collect" in module:
                    from eval.collect import main as collect_main
                    entry["state"] = "COLLECTING"
                    write_json(checkpoint, state)
                    collection_checkpoint = prediction.with_suffix(".checkpoint.json")
                    collect_main(collection_arguments(module, prediction, output / "candidate.json",
                                                       resume=collection_checkpoint.exists()))
                    if module["section"] == "extraction" and not Path(module["alignments"]).is_file():
                        from eval.prepare import propose_alignments
                        draft = module_root / "alignments.draft.jsonl"
                        if not draft.exists():
                            propose_alignments(prediction, draft)
                        entry.update(state="WAITING_ALIGNMENT", alignment_draft=draft.relative_to(output).as_posix(),
                                     alignment_target=module["alignments"])
                        write_json(checkpoint, state)
                        continue
                alignment_hash = digest(Path(module["alignments"]).read_bytes()) if module.get("alignments") else None
                entry["state"] = "SCORING"
                write_json(checkpoint, state)
                report = run_section(Path(module["dataset"]), module["section"], prediction, module_root / "reports",
                                     split=module["split"], review_policy=module["review_policy"],
                                     threshold_version=module["threshold_version"], default_pipeline=module["default_pipeline"],
                                     alignments=Path(module["alignments"]) if module.get("alignments") else None,
                                     answer_reviews=Path(module["answer_reviews"]) if module.get("answer_reviews") else None,
                                     candidate=suite["candidate"],
                                     protocol={"suite_schema": suite["schema_version"], "module_id": module["id"],
                                               "observation_scope": _scope(module)},
                                     suite={"id": suite["id"], "sha256": identity["suite"]})
                verified = verify_report(report, expected_candidate=suite["candidate"], strict=True)
                require(verified["status"] == "VERIFIED", "NEW_REPORT_INVALID: " + ",".join(verified.get("errors", [])))
                if alignment_hash:
                    require(digest(Path(module["alignments"]).read_bytes()) == alignment_hash, "APPROVED_ALIGNMENT_CHANGED")
                    entry["alignment_sha256"] = alignment_hash
                entry.update(state="COMPLETED", report=report.relative_to(output).as_posix())
                write_json(checkpoint, state)
            require(inputs_identity(suite) == identity, "SUITE_INPUT_OR_VERSION_CHANGED_DURING_RUN")
            state["status"] = "COMPLETED" if all(item["state"] == "COMPLETED" for item in state["modules"].values()) else "NEEDS_INPUT"
            state.pop("error", None)
        except BaseException as error:
            # Store exception type only: provider messages can contain private payloads.
            state.update(status="INTERRUPTED", error=type(error).__name__)
            write_json(checkpoint, state)
            write_summary(output, suite, state)
            raise
        write_json(checkpoint, state)
        if state["status"] == "COMPLETED":
            write_bundle(output, suite, state)
        return write_summary(output, suite, state)
