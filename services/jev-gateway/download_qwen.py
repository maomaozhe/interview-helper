"""Pin official Qwen assets; no remote Python code, optimizer or pickle weights."""
import json
import argparse
import shutil
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

ROOT = Path(__file__).resolve().parent
MODEL = "Qwen/Qwen3-1.7B"

if __name__ == "__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--revision"); args=parser.parse_args()
    cache = ROOT / "qwen-model-cache"
    if shutil.disk_usage(ROOT).free < 7_000_000_000:
        raise SystemExit("INSUFFICIENT_FREE_DISK_FOR_MODEL")
    revision = HfApi().model_info(MODEL, revision=args.revision or "main").sha
    if not revision:
        raise SystemExit("MODEL_REVISION_COULD_NOT_BE_RESOLVED")
    (ROOT/"qwen-download-intent.json").write_text(json.dumps({"model":MODEL,"revision":revision}),encoding="utf-8")
    path = snapshot_download(MODEL, revision=revision, cache_dir=cache,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.tiktoken"],max_workers=1)
    (ROOT / "qwen-model.json").write_text(json.dumps({"model": MODEL, "revision": revision,
        "path": str(Path(path).resolve()), "source": "official_qwen_huggingface", "trust_remote_code": False}), encoding="utf-8")
    print(json.dumps({"model": MODEL, "revision": revision, "downloaded": True}))
