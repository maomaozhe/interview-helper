# 面经 Web 操作台 Implementation Plan

**Goal:** 让用户在当前本机服务里直接体验题库、检索、问答、导入和反馈闭环。

**Architecture:** FastAPI 提供页面与静态资源，界面调用既有 API。新增操作台概况、文件清单、近期任务和本地问题记录接口，反馈逐条保存在共享数据目录。

**Tech Stack:** Python/FastAPI、原生 HTML/CSS/JavaScript、pytest、浏览器交互验证。

1. 在 `tests/integration/test_web.py` 先验证首页、静态资源、代理前缀、真实概况、文件来源边界、任务列表、反馈持久化及非法反馈；运行并确认因缺少接口而失败。
2. 新建 `src/interview_intelligence/web.py`，注册上述接口，在 `api.py` 接入；页面资源放在 `src/interview_intelligence/web/`。运行新增与现有 API 测试。
3. 实现四个界面及共享详情/原文/反馈对话框。所有动态内容转义，保留请求编号与真实频次，避免过期请求覆盖界面。
4. 用浏览器检查真实数据与主要流程，复习/反馈写入测试使用隔离数据，修复交互与布局问题。代码审查重点检查请求竞争、来源安全、持久化和导入状态。
5. 运行完整测试、JavaScript 语法和安装包资源检查；更新 README 和演示说明。重建 API 服务，验证真实首页和接口，打开可体验页面。
