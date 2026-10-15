# PROGRESS

更新时间：2026-10-15（Asia/Shanghai）

## 当前状态

- 阶段：A / Conversation-first 主助手，正在执行 UNBLOCK-A-T3。
- PR：[ #69 ](https://github.com/Jayus-07/agent/pull/69) 保持 Draft / BLOCKED，不是 MERGE_READY。
- 最新 main/base：`b823e93ccd121666e064a53959828acd980838fc`。
- 分支：`codex/main-assistant-conversation-first-20261014`。
- 本轮补测依据代码提交：`247c387b1252d61352c9cbbed6ed709b50f7dae6`；当前 PR HEAD `a046278ac4eead1b637cee0db94e7d461ef9b5c8` 仅额外包含先前进度文档更新。
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

## 2026-10-15 本轮补充验收

- 内置浏览器隔离标签 `http://127.0.0.1:13150/agent` 可实际观察到登录后的主助手错误态：`UPSTREAM_UNAVAILABLE`、Trace `1d061e6eac3c`；没有静态成功答案。用户原有管理端 `:3200` 标签未导航、未交互。
- 隔离 PG 容器 `agent-pr69t3-42baf1bfbd-postgres` 内确认独立 `agent_business` 中 `product.products` 有 5 行、`finance.expenses` 有 3 行；读操作只发生在该隔离库。
- 在隔离 API 容器用合成 editor Principal 实际请求 SQL 路由（无 LLM）：`GET /sql/tables` → 200；商品表分页 → 200、5/5 行，日志确认使用 `agent_readonly` 连接 `agent_business`；editor 浏览 finance 表 → 403；恶意排序列 → 400。请求不回显业务行内容。
- RAG 隔离 API 路由拒绝 viewer 请求；测试桩的 `retrieve_documents` 调用数为 0，未启动真实检索/嵌入调用。该项只证明拒绝路径，不证明 RAG 有权限检索或返回引用。
- 容器 `GIT_COMMIT` 环境标签仍是旧值 `0fffa95`，未用该标签认定代码版本；已把 `origin/main...HEAD` 涉及的 32 个 backend 文件逐一 SHA-256 对比工作树和隔离 API 容器，全部匹配，确认以上运行时路径执行的是 PR #69 当前源码。
- 本机 Windows pytest 直接导入应用时读取默认 `localhost:5432/agent_memory` 配置且未提供密码，反馈 DB 初始化失败（19 个用例 setup error、1 passed）；未成功建立数据库连接或写入。随后改在隔离 API 容器用 TestClient + 隔离 PG 实测 SQL 路由，未把上述宿主测试错误计为功能通过。

## 未通过或未验证的 T3

- 真实 Provider 推理、`done.answer` 及 usage/cost 归因：BLOCKED（隔离 Registry 无凭据）。
- RAG 权限内真实引用 / 缺证据拒答、通过 Next→APISIX→API 的授权 SQL 闭环及实际写语句拒绝、跨用户/租户越权、跨产品业务执行拒绝、取消与 SSE 中断恢复：本轮仍未完成 T3。
- 旧提交上已有 A T1/T2 测试与 GitHub CI 证据（111 个后端定向测试、主前端 6 项、客服前端 3 项）；它们不替代本轮真实模型与上述 T3。

## GitHub CI / Review

- 当前 PR HEAD `a046278ac4eead1b637cee0db94e7d461ef9b5c8`：Secret Scan run `38723991759`、Contract Quality Check run `38723991533`、RAG PR Smoke run `38723991640` 均 SUCCESS；真实 API 评测 job 按门禁 skipped。
- Review threads 0、submitted reviews 0；本轮没有新增代码 Diff，最终就绪仍需评估未完成的 P0 T3，不以 CI 绿灯替代验收。

## 阻断与队列

- A 仍 BLOCKED：真实模型成功路径及依赖真实检索的 RAG 证据，需要为隔离 Provider Registry 安全注入仅供测试的远程凭据；不能读取/写入共享生产数据库或把密钥发到聊天。另有 SSE 取消/恢复及跨用户/租户浏览器 T3 未完成。
- B 的 PR #61/#70 已由用户授权合并。C1 PR #66 仍由原会话负责；C2 PR #52 的严格质量/付费额度门禁仍未解除。旅游、管理端维持其他会话所有权。
- 继续完成不依赖远程模型凭据的 T3；外部阻断项解决后继续 A，再按协议推进 C/D 的无冲突任务。
