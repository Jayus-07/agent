# PROGRESS

更新时间：2026-10-15（Asia/Shanghai）

## 当前状态

- 阶段：A 的真实模型成功轮次仍因隔离 Provider 凭据缺失而 BLOCKED；继续完成无模型费用的 API/SQL/SSE 验证，C3 SQL/Memory 集成已完成此前的无费用范围。
- PR：[ #69 ](https://github.com/Jayus-07/agent/pull/69) 保持 Draft / BLOCKED，不是 MERGE_READY。
- 最新 main：`d02c80c83a2249fa5ba7fd6899bda65a74fb31f0`；本分支经 `9953c6e` 合入 #64/#46 main 增量，PR 目标仍为 `main`。本 PR 未修改 Travel/Admin 文件。
- 分支：`codex/main-assistant-conversation-first-20261014`。
- 2026-10-15 本次追加：仅针对 PR #69 所需主助手模型，对数据库配置的 `custom-doubao-seed-2-0-mini / doubao-seed-2.0-mini` 做过一次真实 Provider Probe，L0+L2 通过；密钥未读取/回显。Probe 不等于主助手真实 `done.answer`、Trace/History/Memory 或 usage/cost 对账。
- 最新 main 的 BFF `X-API-Key` 代理改动验证：在 `frontend/` 运行 `npm test -- src/api/bff-proxy.test.ts src/api/chat.test.ts src/api/memory.test.ts`，3 files / 12 tests passed；隔离 `:13150` 浏览器中的已登录合成测试会话也成功加载历史列表。
- Next→APISIX→API 隔离 SQL 只读链路：在隔离 Auth Redis 签发并随后清理一次性 editor 测试会话，经 `:13150/api/sql/tables` 返回 200 和 11 项允许表元数据；未读取业务行。请求日志确认到达隔离 API。隔离 PG 以 `SET ROLE agent_readonly` 对 `UPDATE product.products SET id=id WHERE false` 实测被 PostgreSQL 权限拒绝，未改动行。
- SSE 恢复和可信产品范围定向验证：`D:/Python/python.exe -m pytest backend/tests/api/test_sse_resume.py backend/tests/orchestration/router/test_product_scope.py -q --no-cov`，17 passed。修正过时测试使其按产品/用户命名空间构造流 ID；测试夹具改为只挂聊天路由，避免无关全局反馈路由触碰本机默认 PostgreSQL。
- Docker API 镜像未安装 pytest；没有为运行单测而改动隔离容器依赖。上述后端回归已由宿主 Python 在无数据库连接的最小测试夹具运行通过。
- Provider Probe 由用户指定的管理端入口按已有实现执行，会更新其配置库中的 `last_probe_*` 摘要；未读取/回显或复制密钥、未修改 Provider/Model/Role 绑定。没有通过真实主助手聊天写入共享/生产业务数据库。隔离 Registry 仍无远程 Provider 凭据，因此真实模型成功轮次及费用账本归因继续 BLOCKED。
- 本轮补测依据代码提交：`247c387b1252d61352c9cbbed6ed709b50f7dae6`；补测启动时 PR HEAD `a046278ac4eead1b637cee0db94e7d461ef9b5c8` 仅额外包含先前进度文档更新。
- 本轮代码修复提交：`247c387b1252d61352c9cbbed6ed709b50f7dae6`。
- 未查询共享/生产业务表；没有旅游或管理端源码修改；没有合并 main。

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

- Compose namespace：`agent-pr69t3-42baf1bfbd`。独立 PG/Redis/Auth-Redis、API `127.0.0.1:18050`、APISIX `127.0.0.1:19050`、Next `127.0.0.1:13150`。PG 无宿主发布端口；独立测试卷、网络和容器。`:3200` 管理端仅用于读取配置与用户授权的 Provider Probe；未改绑定。
- 新建的合成 viewer 经内置浏览器实际登录，路径为 Next BFF → APISIX → FastAPI；`/auth/login`、预算与记忆受保护请求成功。
- 一般问答在无 Provider 凭据条件下返回 `UPSTREAM_UNAVAILABLE`；浏览器显示安全错误和 Trace ID，没有静态成功答案。Trace `303914f3bc13` 在隔离 PG 的 `trace_store` 状态为 `error`，耗时 14,007 ms。
- 刷新后仍为已登录状态且回到新对话；这不等于 SSE 断点续传通过。
- 隔离 Registry 显示 7 个 provider、8 个模型、0 份凭据；未使用本地 Ollama。隔离 Provider 的真实 `done.answer` 仍未验证；单独管理端 Provider Probe 不构成主助手生成/计费验证。

## 2026-10-15 本轮补充验收

- 内置浏览器隔离标签 `http://127.0.0.1:13150/agent` 可实际观察到登录后的主助手错误态：`UPSTREAM_UNAVAILABLE`、Trace `1d061e6eac3c`；没有静态成功答案。用户原有管理端 `:3200` 标签未导航、未交互。
- 隔离 PG 容器 `agent-pr69t3-42baf1bfbd-postgres` 内确认独立 `agent_business` 中 `product.products` 有 5 行、`finance.expenses` 有 3 行；读操作只发生在该隔离库。
- 在隔离 API 容器用合成 editor Principal 实际请求 SQL 路由（无 LLM）：`GET /sql/tables` → 200；商品表分页 → 200、5/5 行，日志确认使用 `agent_readonly` 连接 `agent_business`；editor 浏览 finance 表 → 403；恶意排序列 → 400。请求不回显业务行内容。
- RAG 隔离 API 路由拒绝 viewer 请求；测试桩的 `retrieve_documents` 调用数为 0，未启动真实检索/嵌入调用。该项只证明拒绝路径，不证明 RAG 有权限检索或返回引用。
- 容器 `GIT_COMMIT` 环境标签仍是旧值 `0fffa95`，未用该标签认定代码版本；已把 `origin/main...HEAD` 涉及的 32 个 backend 文件逐一 SHA-256 对比工作树和隔离 API 容器，全部匹配，确认以上运行时路径执行的是 PR #69 当前源码。
- C3 Memory 实际 PG 隔离：在同一隔离 `agent_memory` 以合成身份写入两条会话消息和一条已验证 L3 profile；本人读取 200 并看到两条消息，伪造 query 参数不改变可信身份；同租户跨用户、同用户跨租户、customer-service 产品路径均 404。L3 profile 仅本人/租户/产品命名空间可见；清理后隔离 DB 核对测试 `chat_sessions` 和对应 `memory_records` 均为 0。
- 内置浏览器真实点按停止：Next→APISIX→API 的 `POST /chat/agent/stream` 与 `/chat/agent/abort` 均 200；界面停止流并显示空助手轮次，无成功伪答。Trace `8e371d30518c` 状态 `cancelled`、时长 827 ms，隔离 DB 没有写入该测试问题的 `chat_messages`。服务端 stream HTTP 请求总时长 13.8 s（停止接口约 55 ms 后可用）；这验证取消终态/不污染记忆，不验证 SSE 断线续传，也不代表真实模型调用。
- 当前 HEAD 定向离线回归：`D:/Python/python.exe -m pytest backend/tests/api/test_memory_routes.py backend/tests/rag/test_rag_authz.py backend/tests/orchestration/router/test_product_scope.py -q --no-cov`：51 passed。
- 本机 Windows pytest 直接导入应用时读取默认 `localhost:5432/agent_memory` 配置且未提供密码，反馈 DB 初始化失败（19 个用例 setup error、1 passed）；未成功建立数据库连接或写入。随后改在隔离 API 容器用 TestClient + 隔离 PG 实测 SQL 路由，未把上述宿主测试错误计为功能通过。
- 本轮复核逻辑取消尾延迟：`backend/core/request_context.py` 明确规定取消为逻辑取消，已开始的同步 Provider 调用允许自然结束、结果丢弃；LLMProxy 对取消中的用量 reservation 标记 `call_cancelled_possibly_billed`。没有改动取消/账本语义，也不把底层请求自然结束时间归咎于 Clash。
- 最新 main 对齐提交：`20b0fef1fa43a7cd835a129773587c52deb77465`；上游仅增加 Travel #62 相关文件，本分支未编辑这些文件。`origin/main...HEAD` PR 差异仍为 49 个原有文件。
- 在最新 main 对齐后的 HEAD 重跑：`D:/Python/python.exe -m pytest backend/tests/orchestration/context/test_continuation_routing.py backend/tests/test_error_protocol.py backend/tests/api/test_memory_routes.py backend/tests/rag/test_rag_authz.py backend/tests/orchestration/router/test_product_scope.py -q --no-cov`：106 passed。
- 内置浏览器当前仍显示隔离 `/agent` 的模型上游错误态和先前取消留下的空助手轮次，没有静态假答案；未发起新的模型请求，也未触碰 `:3200` 标签。
- 源码核实 `POST /sys/providers/{id}/verify` 会调用 `record_probe` 并提交 `llm_providers.last_probe_at/last_probe_ok/last_probe_worst_grade`；所以 Provider Probe 仅作为有界的模型可达性证据，不作为无副作用数据库操作或隔离主助手验收。

## 未通过或未验证的 T3

- 隔离环境中的真实 Provider 推理、`done.answer` 及 usage/cost 归因：BLOCKED（隔离 Registry 无凭据；现有 Probe 只证明目标模型可连通）。
- RAG 权限内真实引用 / 缺证据拒答、通过 Next→APISIX→API 的授权 SQL 闭环及实际写语句拒绝、主助手真实模型成功轮次及相应 History/Memory/Trace/费用对账、跨产品业务执行拒绝、SSE 断线续传：本轮仍未完成 T3。SQL/Memory 的隔离 PG 与 API 路由、浏览器取消路径已通过，但不替代真实模型闭环。
- 旧提交上已有 A T1/T2 测试与 GitHub CI 证据（111 个后端定向测试、主前端 6 项、客服前端 3 项）；它们不替代本轮真实模型与上述 T3。

## GitHub CI / Review

- 补充验收 HEAD `bf0bf7bf9a070d41ed5ebf2c334f33f8eec8b0ca`：Secret Scan run `38728759414`、Contract Quality Check run `38728759521`、RAG PR Smoke run `38728759524` 均 SUCCESS；真实 API 评测 job 按门禁 skipped。
- Review threads 0、submitted reviews 0；本轮没有新增代码 Diff，最终就绪仍需评估未完成的 P0 T3，不以 CI 绿灯替代验收。

## 阻断与队列

- A 的真实模型/RAG 门禁仍 BLOCKED。B1 PR #61 与 B2 PR #70 已合并；merge commits `3a12dabc56c9cd8dfd80c401944f2924e36b3339` / `5df8b0603355566e7fa1eb87906129c11a8211ab` 都是最新 `origin/main` 的祖先，PR #70 合并前 CI/Review 和退款隔离 PG 证据已留在原 PR。
- C1 PR #66 仍由独立 RAG 会话负责；C2 PR #52 仍开放且有当前分支/提交活动，不接管或重复修改。C3 当前完成了 SQL/Memory 无费用隔离 DB 与 API 路由部分；D 等 A/B/C 达到可集成条件后执行。旅游、管理端继续由各自会话负责。
- 下一步：完成尚未覆盖的零费用 T3 边界并收集隔离浏览器证据；真实主助手成功轮次、依赖真实检索的引用和 usage/cost 仍需隔离 Provider 凭据。不能把管理端 Probe 的密钥或共享数据复制进隔离环境。
