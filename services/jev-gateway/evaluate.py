"""Local-domain probe, not a general-purpose benchmark or calibrated accuracy claim."""
import json
from pathlib import Path
import statistics
import math
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    token = dict(line.split("=", 1) for line in (ROOT / "service.env").read_text().splitlines()
                 if "=" in line and not line.startswith("#"))["JEV_PROXY_TOKEN"]
    cases = json.loads((ROOT / "decision-eval-payloads.json").read_text())
    rows, timings, field_correct, field_count, high_conf_wrong = [], [], 0, 0, 0
    for case in cases:
        req = urllib.request.Request("http://127.0.0.1:18788/v1/systemone",
            data=json.dumps(case["payload"], ensure_ascii=False).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            data = json.load(error)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        actual, wrong = {}, []
        for key, expected in case["expected"].items():
            answer = data.get("answers", {}).get(key, {})
            actual[key] = answer.get("choice")
            field_count += 1
            if actual[key] == expected:
                field_correct += 1
            else:
                wrong.append(key)
                if answer.get("confidence", 0) >= .85:
                    high_conf_wrong += 1
        timings.append(data.get("timings", {}).get("provider_ms", elapsed))
        rows.append({"id": case["id"], "message": case["message"], "expected": case["expected"],
            "actual": actual, "wrong_fields": wrong, "response": data, "http_ms": elapsed})
        print(json.dumps({"id": case["id"], "correct": not wrong, "provider_ms": timings[-1]}, ensure_ascii=False), flush=True)
    ordered = sorted(timings)
    result = {"model": "convaiinnovations/laya-multilingual", "revision": "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67",
        "hardware": "NVIDIA L20", "code_revision": "2e4d9c87e8b1621deb344eac7de5c7258f32f849",
        "cases": len(rows), "exact_cases": sum(not row["wrong_fields"] for row in rows),
        "field_correct": field_correct, "field_count": field_count, "high_confidence_wrong_fields": high_conf_wrong,
        "provider_p50_ms": statistics.median(timings), "provider_p95_ms": ordered[math.ceil(len(ordered)*.95)-1],
        "provider_p95_method": "nearest_rank", "provider_max_ms": max(timings),
        "domain_calibrated": False, "production_decision": "shadow_only", "results": rows}
    (ROOT / "decision-eval-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False), flush=True)
