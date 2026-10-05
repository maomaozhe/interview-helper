"""Download immutable, non-executable model assets into this service's own cache."""
import json
import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
from huggingface_hub import snapshot_download

MODEL = "convaiinnovations/laya-multilingual"
REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"
ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    path = snapshot_download(MODEL, revision=REVISION, cache_dir=ROOT / "model-cache",
                             allow_patterns=["rl_agent_config.json", "model.safetensors",
                                             "tokenizer/*", "encoder/*"], max_workers=2)
    (ROOT / "model-path.txt").write_text(str(Path(path).resolve()), encoding="utf-8")
    print(json.dumps({"model": MODEL, "revision": REVISION, "downloaded": True}))
