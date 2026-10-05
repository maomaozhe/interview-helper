"""Read-only dependency/GPU probe; avoid printing configuration or credentials."""
import json
import sys
import torch
import transformers
import safetensors
import uvicorn
import fastapi
print(json.dumps({"python":sys.version.split()[0], "base_python":sys._base_executable,
    "torch":torch.__version__, "transformers":transformers.__version__,
    "cuda":torch.cuda.is_available(), "gpu":torch.cuda.get_device_name() if torch.cuda.is_available() else None,
    "safetensors":safetensors.__version__, "uvicorn":uvicorn.__version__, "fastapi":fastapi.__version__}))
