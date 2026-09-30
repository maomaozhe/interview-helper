"""Create an annotation queue from real files; never mark items as gold."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path


REQUIRED_NAMES = [
    "百度秋招一面二面三面面经(三面挂)-炒肉多.md",
    "pdd服务端提前批1-4面-蓝莓蛋挞.md",
    "Agent开发高频面试题（八股版）-三只松鼠.md",
    "得物-一面（回忆版）- 2026.9.22-明日香.md",
    "小红书-Java后端开发-1面面经-06.17-不知道起什么名字好～.md",
]


def prepare(corpus_root: Path, output: Path, *, count: int = 30, seed: int = 20260930) -> list[dict]:
    files = sorted(corpus_root.rglob("*.md"))
    by_name = {path.name: path for path in files}
    missing = [name for name in REQUIRED_NAMES if name not in by_name]
    if missing:
        raise ValueError(f"required fixture files missing: {missing}")
    selected = [by_name[name] for name in REQUIRED_NAMES]
    rest = [path for path in files if path not in selected]
    random.Random(seed).shuffle(rest)
    selected.extend(rest[:max(0, count - len(selected))])
    items = [{
        "id": f"candidate-{index:03d}",
        "relative_path": path.relative_to(corpus_root).as_posix(),
        "source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "human_verified": False,
        "split": "pending",
        "annotation_status": "PENDING",
    } for index, path in enumerate(selected, 1)]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"seed": seed, "candidate_count": len(items), "items": items},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    return items


if __name__ == "__main__":
    print(len(prepare(Path("md"), Path("data/gold/v1/extraction_candidates.json"))))
