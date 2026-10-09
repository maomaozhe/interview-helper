# Qwen 决策网关实验

这些脚本是有限选项路由与 JSON 约束生成实验，尚未完成完整 GPU 运行、质量及校准验收，未启用在正式面经服务中。模型置信度不能视为正确率。

- `download_qwen.py` 下载官方 `Qwen/Qwen3-1.7B` 资源，将可选的 `--revision` 分支或标签解析为不可变提交 SHA。仅下载配置、tokenizer 与 safetensors 文件，不加载远端 Python 代码。
- `inspect_qwen_download.py` 查看同目录下载脚本的进程；`--stop` 仅停止该脚本的进程。
- `qwen_server.py` 与 `qwen_heads_server.py` 是独立实验入口，需要 Python、PyTorch、Transformers、Hugging Face Hub 及兼容的 CUDA 环境。先生成 `qwen-model.json`，再设置 `JEV_PROXY_TOKEN`。服务默认仅监听回环地址，不自动接入正式服务。
- 模型缓存、下载清单及本机模型路径文件均已加入 `.gitignore`，不提交权重或本机运行信息。

WSL 隧道使用显式环境变量，保留 SSH 主机密钥校验，默认本机转发端口为 18791，远端回环服务端口为 18790：

```sh
export QWEN_SSH_HOST='user@your-server'
export QWEN_SSH_IDENTITY_FILE='/path/to/id_ed25519'
export QWEN_SSH_KNOWN_HOSTS_FILE="$HOME/.ssh/known_hosts"
sh services/jev-gateway/start-qwen-tunnel-wsl.sh
```

可用 `QWEN_SSH_CONTROL_SOCKET` 指定独立控制 socket；不同目标服务器应使用不同 socket。`QWEN_SSH_FORWARD` 可显式覆盖转发定义，默认 `127.0.0.1:18791:127.0.0.1:18790`。本次提交仅检查 Python 编译与 shell 语法，不将其视为实际下载、GPU 推理或网关质量验证。
