# 私有 Qwen 模型服务

2026-10-09 已部署独立的 OpenAI 兼容 Qwen 端点，模型服务和协议验收通过，可用作租户默认模型。模型部署实测见[Qwen 验收记录](verification/2026-10-09-qwen-runtime.md)，正式应用接入和多租户状态见[应用验收记录](verification/2026-10-09-qwen-tenants.md)。目标服务由 SSH 用户管理，只监听 `127.0.0.1:18792`，不开放新的公网端口。应用后端保存默认模型凭据，浏览器不能取得该凭据；租户自带模型配置和试用次数在应用层管理。

## 模型与资源选择

部署前实际检查了目标服务器：Ubuntu 20.04、22 个逻辑 CPU、117 GiB RAM、NVIDIA L20 46,068 MiB 显存，当时可用显存约 17 GiB、磁盘剩余约 88 GiB。服务器已有其他推理和图像服务，以及独立实验 Qwen 路由；本部署增加一个用户服务。

选择官方 `Qwen/Qwen3-8B-GGUF` 的 `Qwen3-8B-Q4_K_M.gguf`。模型、llama.cpp 源码提交和 NVIDIA CUDA 12.8 包均固定在 [runtime-manifest.json](../services/qwen-openai/runtime-manifest.json) 中；权重和下载包核验官方 SHA-256。量化权重约 5 GB，24,576 token 上下文、Q8 KV cache、Flash Attention 和一个推理 slot 控制显存占用。本次实际 GPU 进程使用 6,760 MiB 显存。

llama.cpp 启用 Jinja 模板和 Qwen 工具调用，关闭 thinking，提供 `/v1/models`、普通和流式 `/v1/chat/completions`，以及 JSON Schema 约束输出。单个 slot 将请求排队执行；应用的租户配额和准入限制仍需独立启用。官方依据：[Qwen 模型](https://huggingface.co/Qwen/Qwen3-8B-GGUF)、[llama.cpp 服务参数](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)、[CUDA 编译](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md#cuda)、[NVIDIA Ubuntu 20.04 软件源](https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2004/x86_64/)。

## 部署

需要用户 systemd、`Linger=yes`、Python 3.10+、gcc/g++、git、`dpkg-deb`、curl 和 CMake 3.18+。有 aria2c 时采用 16 路分片下载并验证完整校验和。CUDA 包仅解压到私有部署目录，保留系统驱动和其他模型环境。

本次服务器已经有该 SSH 用户的 NVIDIA cuBLAS 13 运行库。为避免重复下载大型包，`--cublas-root` 将其复制到独立 CUDA 目录，并记录每个库文件的 SHA；不让服务依赖原 Python 环境中的可变文件。NVCC 12.8 使用对应 CUDA 12 开发包的五个头文件，私有的 `--cublas-headers` 归档按清单逐文件核验。本次 CUDA 12.8 编译器、CUDA 12 头文件和 cuBLAS 13 运行库的组合已完成编译、启动和实际 GPU 推理验收；其他机器仍须重新验收。

将以下文件上传至 `~/services/interview-qwen/`：

- `scripts/deploy-ssh-qwen.py`
- `services/qwen-openai/runtime-manifest.json`
- `services/qwen-openai/verify.py`
- `services/qwen-openai/download-artifacts.py`

本次使用已有 uv 和 Python 3.12，为构建工具创建单独环境：

```sh
SERVICE_DIR="$HOME/services/interview-qwen"
"$HOME/.local/bin/uv" venv --python "$HOME/.local/bin/python3.12" "$SERVICE_DIR/runtime/build-venv"
"$HOME/.local/bin/uv" pip install --python "$SERVICE_DIR/runtime/build-venv/bin/python" cmake==3.31.6
"$HOME/.local/bin/python3.12" "$SERVICE_DIR/deploy-ssh-qwen.py" \
  --cmake "$SERVICE_DIR/runtime/build-venv/bin/cmake"
"$HOME/.local/bin/python3.12" "$SERVICE_DIR/verify.py"
```

如果服务器对 Hugging Face 访问较慢，可从官方 ModelScope 镜像分片下载；最终仍强制核验清单中的相同 SHA：

```sh
"$HOME/.local/bin/python3.12" "$SERVICE_DIR/download-artifacts.py" \
  --output-dir "$SERVICE_DIR/models" --model-only --workers 4 --limit-rate 512K \
  --model-url https://modelscope.cn/models/Qwen/Qwen3-8B-GGUF/resolve/master/Qwen3-8B-Q4_K_M.gguf
```

也可在本地调用此脚本下载到被忽略的目录后上传，不能省略服务器端校验。`deploy-ssh-qwen.py --build-only` 可在权重独立下载时预先准备运行时。

脚本从固定提交构建 CUDA llama-server，创建 `interview-qwen-openai.service`，生成权限为 600 的 `api-keys.txt` 与 `provider.private.json`，并启动服务。`provider.private.json` 保存 `base_url`、`model`、`api_key` 和上下文容量；不要把它复制到源码、公开报告或前端。安装回执 `deployment-receipt.json` 不含 API key。下载、缓存、权重和私有运维记录放在服务器目录或本地被忽略的 `data/reports/` 中。

## 运行与验证

```sh
systemctl --user status interview-qwen-openai --no-pager
journalctl --user -u interview-qwen-openai -n 60 --no-pager
curl -fsS http://127.0.0.1:18792/health
"$HOME/.local/bin/python3.12" "$HOME/services/interview-qwen/verify.py"
```

验证脚本从私有文件读取凭据，不输出密钥。九项实测包含未认证访问被拒绝、模型列表、中文回答、必选工具调用、工具结果回传、严格 JSON Schema、SSE 多个增量及 usage、流式工具调用，以及大于 24 KB 的长上下文。保存的 `verification.json` 只包含公开验证结果和硬件读数。脚本同时核对 systemd 主进程的 NVIDIA 显存使用和启动参数。当前默认日志级别不输出底层逐层 offload 记录，报告中该字段为 null，不据此声明具体实际层数；如运维启动时显式启用 `-lv 5`，可用 `--require-offload-log` 核验该进程的完整 offload 日志。接入正式服务后仍要验证实际查询规划和租户隔离。

暂停此模型可运行 `systemctl --user disable --now interview-qwen-openai`。这会保留权重和凭据，以便恢复；如需重建运行时，先停止该独立服务，再执行部署脚本。应用配置回退需由应用发布流程处理。
