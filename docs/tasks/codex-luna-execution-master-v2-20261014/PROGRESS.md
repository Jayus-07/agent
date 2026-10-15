# PROGRESS

更新时间：2026-10-15（Asia/Shanghai）

## 当前状态

- 阶段：A / Conversation-first 主助手，正在执行 UNBLOCK-A-T3。
- PR：[ #69 ](https://github.com/Jayus-07/agent/pull/69) 保持 Draft / BLOCKED，不是 MERGE_READY。
- 最新 main/base：`b823e93ccd121666e064a53959828acd980838fc`。
- 分支：`codex/main-assistant-conversation-first-20261014`。
- 本轮代码修复提交：`247c387b1252d61352c9cbbed6ed709b50f7dae6`。
- 当前没有共享/生产数据库访问；没有旅游或管理端源码修改；没有合并 main。

## A 实现摘要

- 主助手、客服和公共聊天入口身份由服务端注册路径固定，忽略 body 自报用户/产品/域提示。
- Session、stream、memory user 与幂等范围按产品隔离；Resume/Abort 校验可信用户/租户；Tool 候选与执行点双重校验产品权限。
- 主助手一般概念、地点、日常对话和无活跃域短追问使用 general_chat 快路径；客服及业务语义保留原有路由。
- 普通 `/chat/*` 公共入口不写持久记忆；主助手与客服前端使用产品专属 Chat/Memory API。

## 本轮发现并修复

- 内置浏览器 T3 发现 general_chat 在 LLM 连接失败时把固定静态话术写成 `final_answer`，导致 SSE `done`、错误 Trace 记为 success，并可能保存错误 Memory。
- 修复提交 `247c387`：移除静态成功 fallback，让异常进入 GraphRunner 错误终态；HTTPX 网络错误映射为 `UPSTREAM_UNAVAILABLE`，错误日志仅记异常类型。
- `D:/Python/python.exe -m py_compile` 检查本轮 4 个 Python 文件通过。
- `D:/Python/python.exe -m pytest backend/tests/orchestration/context/test_continuation_routing.py backend/tests/test_error_protocol.py -q --no-cov`：55 passed。
- `git diff --check`：通过。

## 隔离环境与浏览器证据

- Compose namespace：`agent-pr69t3-42baf1bfbd`。独立 PG/Redis/Auth-Redis、API `127.0.0.1:18050`、APISIX `127.0.0.1:19050`、Next `127.0.0.1:13150`。PG 无宿主发布端口；独立测试卷、网络和容器。用户的 `:3200` 管理端标签未操作。
- 新建的合成 viewer 经内置浏览器实际登录，路径为 Next BFF → APISIX → FastAPI；`/auth/login`、预算与记忆受保护请求成功。
- 一般问答在无 Provider 凭据条件下返回 `UPSTREAM_UNAVAILABLE`；浏览器显示安全错误和 Trace ID，没有静态成功答案。Trace `303914f3bc13` 在隔离 PG 的 `trace_store` 状态为 `error`，耗时 14,007 ms。
- 刷新后仍为已登录状态且回到新对话；这不等于 SSE 断点续传通过。
- 隔离 Registry 显示 7 个 provider、8 个模型、0 份凭据；本轮无本地 Ollama 推理、无真实远程模型生成、无付费模型调用。provider 的真实 `done.answer` 仍未验证。

## 未通过或未验证的 T3

- 真实 Provider 推理、`done.answer` 及 usage/cost 归因：BLOCKED（隔离 Registry 无凭据）。
- RAG 权限内引用 / 缺证据拒答、隔离业务库合法只读 SQL 与写拒绝、跨用户/租户越权、跨产品业务执行拒绝、取消与 SSE 中断恢复：本轮未完成 T3。
- 旧提交上已有 A T1/T2 测试与 GitHub CI 证据（111 个后端定向测试、主前端 6 项、客服前端 3 项）；它们不替代本轮真实模型与上述 T3。

## GitHub CI / Review

- 当前代码 HEAD `247c387b1252d61352c9cbbed6ed709b50f7dae6`：Secret Scan run `38723338672` SUCCESS；Contract Quality Check run `38723338659` 和 RAG PR Smoke run `38723338617` 在本文件更新时仍运行中；真实 API 评测 job 按门禁 skipped。
- Review threads 0、submitted reviews 0；需在 CI 完成后复核本次新增 Diff 和最新状态。

## 阻断与队列

- A 仍 BLOCKED：要在隔离 Next/API 栈验证真实模型，需要安全地向隔离 Provider Registry 注入仅供测试的远程凭据；不能读取/写入共享生产数据库或把密钥发到聊天。
- B 的 PR #61/#70 已由用户授权合并。C1 PR #66 仍由原会话负责；C2 PR #52 的严格质量/付费额度门禁仍未解除。旅游、管理端维持其他会话所有权。
- 继续完成不依赖远程模型凭据的 T3；外部阻断项解决后继续 A，再按协议推进 C/D 的无冲突任务。
