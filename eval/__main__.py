"""Repository entry point: python -m eval (no editable install required)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="统一评测入口；真实采集须显式授权预算，默认只离线。")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight", help="检查环境、数据、版本与预算，不调用服务或模型")
    check.add_argument("suite", type=Path)
    check.add_argument("--allow-live", action="store_true")
    for name in ("run", "resume"):
        command = commands.add_parser(name)
        command.add_argument("suite", type=Path)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--allow-live", action="store_true")
    demo = commands.add_parser("demo", help="完整六模块合成离线流程")
    demo.add_argument("--output", type=Path, required=True)
    reference = commands.add_parser("freeze-queries", help="从独立原文审核冻结私有查题参考；零服务/模型调用")
    reference.add_argument("--snapshot", type=Path, required=True)
    reference.add_argument("--reviews", type=Path, nargs="+", required=True)
    reference.add_argument("--recipe", type=Path, required=True)
    reference.add_argument("--authorization", type=Path, required=True)
    reference.add_argument("--output", type=Path, required=True)
    comparison = commands.add_parser("compare")
    comparison.add_argument("before", type=Path)
    comparison.add_argument("after", type=Path)
    comparison.add_argument("--output", type=Path)
    profile = commands.add_parser("profile-latency", help="离线拆分已有预测的阶段耗时与调用代价；不重跑服务")
    profile.add_argument("before", type=Path)
    profile.add_argument("after", type=Path)
    profile.add_argument("--output", type=Path)
    gate = commands.add_parser("check", help="严格复算报告或发布bundle；--legacy仅作历史核查")
    gate.add_argument("target", type=Path)
    gate.add_argument("--legacy", action="store_true")
    gate.add_argument("--root", type=Path, default=Path("."), help="bundle相对路径的根；历史bundle通常为仓库根")
    gate.add_argument("--output", type=Path, help="另存机器可读检查结果，文件须不存在")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            from eval.preflight import load_suite, preflight
            result = preflight(load_suite(args.suite), allow_live=args.allow_live)
        elif args.command in {"run", "resume"}:
            from eval.suite import run_suite
            result = run_suite(args.suite, args.output, resume=args.command == "resume", allow_live=args.allow_live)
        elif args.command == "demo":
            from eval.demo import suite_demo
            result = suite_demo(args.output)
        elif args.command == "freeze-queries":
            from eval.common import read_json
            from eval.query_results import build_query_results
            result = build_query_results(args.snapshot, args.reviews, args.recipe, args.output,
                                         authorization=read_json(args.authorization))
        elif args.command == "profile-latency":
            from eval.latency import compare_latency
            result = compare_latency(args.before, args.after)
        elif args.command == "compare":
            from eval.compare import compare
            if (args.before / "report.json").is_file() and (args.after / "report.json").is_file():
                from eval.suite import compare_suites
                result = compare_suites(args.before, args.after)
            else:
                result = compare(args.before, args.after)
        else:
            from eval.common import read_json
            from eval.release import check_bundle, verify_report
            if args.target.is_dir():
                verified = verify_report(args.target, strict=not args.legacy)
                result = {key: verified[key] for key in ("status", "errors", "strict_verified", "candidate_verification",
                          "candidate_deployment_verified", "scope") if key in verified}
                result.update(report=str(args.target.resolve()), section=verified.get("metadata", {}).get("section"),
                              quality_gate=verified.get("gate", {}).get("status"))
            else:
                result = check_bundle(read_json(args.target), args.root, strict=not args.legacy)
        if getattr(args, "output", None) and args.command in {"compare", "check", "profile-latency"}:
            from eval.common import write_json
            from eval.validate_gold import require
            require(not args.output.exists(), "OUTPUT_ALREADY_EXISTS")
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") in {"BLOCKED", "INVALID"} else 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        # Local input validation is useful; never echo arbitrary provider exception text.
        from eval.validate_gold import GoldValidationError
        from eval.preflight import SuiteValidationError
        detail = str(error) if isinstance(error, (GoldValidationError, SuiteValidationError)) else type(error).__name__
        print(json.dumps({"status": "BLOCKED", "error": detail}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
