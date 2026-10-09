# 账号与个人模型配置

启用 `MULTI_TENANT_ENABLED=true` 后，用户在侧栏“账号与模型”注册或登录。一个账号对应一个租户：面经题库与原文共享，对话、历史请求、复习记录、偏好和个人模型配置按账号隔离。共享题库的导入、索引和分类发布需要管理员身份。

系统默认模型每个账号累计可用 **10 次**。每次新的 AI 提问准入计一次；准入后调用失败仍计次。相同请求重试、SSE 重连、查看历史和普通题库浏览不会重复扣次数。额度用尽后可以继续浏览题库，并在设置中填写自己的 API Key、Base URL 和模型名称。

保存个人模型配置后，对话与相关性核验使用个人提供方，不扣系统默认次数。修改模型时留空 Key 可保留已保存的密钥；选择“恢复系统默认”删除个人配置，但不重置试用次数。个人提供方调用失败时会返回错误，不会自动改用系统模型。Key 在服务端加密保存，页面只显示配置状态和掩码；不在浏览器存储中保存。

个人 Base URL 需要使用可公开访问的 HTTPS 地址和 443 端口，例如 `https://api.example.com/v1`。提供方需支持 OpenAI 兼容的对话、工具调用和结构化输出。系统部署的私有 Qwen 由管理员通过单独配置接入。题库向量继续使用原有 embedding 提供方，避免改变已有索引维度；个人模型配置作用于对话和相关性核验。

## 服务端配置

```dotenv
MULTI_TENANT_ENABLED=true
TENANT_TRIAL_LIMIT=10
TENANT_DEFAULT_BASE_URL=http://127.0.0.1:18792/v1
TENANT_DEFAULT_API_KEY=填写私有模型密钥
TENANT_DEFAULT_QUERY_MODEL=qwen3-8b-interview
TENANT_DEFAULT_RERANKER_MODEL=qwen3-8b-interview
MODEL_CREDENTIAL_KEY=填写独立的Fernet密钥
```

使用下面的命令生成 `MODEL_CREDENTIAL_KEY`，写入权限为 600 的私有环境文件，并随数据库备份保存。服务重启和迁移机器时应保留该值；丢失或更换会导致已有个人模型密钥无法解密。

```sh
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

修改 `TENANT_TRIAL_LIMIT` 后重启 API 即生效；已有账号的累计用量保持不变。原有 `MODEL_*` 配置继续服务于共享语料抽取和 embedding。执行 `alembic upgrade head` 增加账号、会话、模型配置和准入记录表；回退旧 API 时保留新增表及迁移，避免删除已生成的账号数据。

网页原有访问密码与管理员登录独立保留。管理员访问 `/admin`；普通账号使用“账号与模型”。私有 Qwen 的安装与运行见 [Qwen 部署说明](deployment-qwen.md)，SSH 服务管理见 [SSH 部署说明](deployment-ssh.md)。
