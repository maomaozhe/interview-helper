# Qwen 私有模型服务验收

2026-10-09 16:44（北京时间）在目标服务器完成实际验收。`interview-qwen-openai.service` 已启动并启用开机运行；端点为 `http://127.0.0.1:18792/v1`，模型别名为 `qwen3-8b-interview`。只监听 loopback，未新增公网端口。凭据位于服务器 `~/services/interview-qwen/provider.private.json`，权限 600，报告和源码不包含密钥。

| 实测项目 | 结果 |
| --- | --- |
| 无认证 `/v1/models` | HTTP 401 |
| 已认证模型列表 | 返回指定模型别名 |
| 中文普通请求 | 返回“连接成功”，无 thinking 内容 |
| `tool_choice=required` | 正确调用 `add_numbers(17, 23)` |
| 工具结果回传 | 理解返回值 40 |
| 严格 JSON Schema | 返回 `{"ok": true}` |
| 普通 SSE | 多个内容增量、稳定响应 ID、stop、usage、DONE |
| 必选工具 SSE | 有工具 ID，参数正确，tool_calls 结束原因 |
| 长上下文 | 27,379 字节、3,925 prompt tokens，返回 CONTEXT_OK |

服务器是 NVIDIA L20（46,068 MiB）。验收时本服务 systemd 主进程 PID 3721723，对应 NVIDIA compute process 和私有 `llama-server` 路径，实际使用 6,760 MiB 显存，启动参数为 `--n-gpu-layers 99`。该记录说明实际使用 GPU；默认日志级别抑制底层逐层 offload 信息，因此未记录实际 offloaded 层数，不声明 37/37。日志中的短回复生成约 120 tokens/s，仅作此硬件上的观测值。

权重为官方 `Qwen/Qwen3-8B-GGUF` 的 Q4_K_M 版本，5,027,783,488 字节，SHA-256 为 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`。从官方 ModelScope 镜像获取，安装时完整核验此固定 SHA。llama.cpp 固定在 `3d65c90d04d337e88f2b1f7f0061f40a5324e662`（b11515）。

运行时在服务器本机编译：Ubuntu 20.04/glibc 2.31、GNU 9.4、NVCC 12.8.93，CUDA 架构 89。私有 CUDA 环境使用对应 CUDA 12 开发包头文件，以及从该 SSH 用户既有环境只读复制的 cuBLAS 13 库；复制和头文件均核验 SHA，现有环境未被修改。上述组合的可用性由本机编译和实际 GPU 请求验证，不能直接推定适用于其他服务器。

配置为 24,576 token context、一个推理 slot、Q8 KV cache、Flash Attention、关闭 Qwen thinking。九项协议检查通过；完整程序输出保存在远端 `~/services/interview-qwen/verification.json`，无密钥部署回执为同目录 `deployment-receipt.json`。本地留档在被忽略的 `data/reports/qwen-deployment-20261009/`。

正式应用的 Pi 工具调用、查询规划、租户试用额度和用户自带 key 的集成结果另见[应用验收记录](2026-10-09-qwen-tenants.md)。
