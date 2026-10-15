# PROGRESS

- 更新时间：2026-10-15。
- 当前阶段：A / Conversation-first 主助手；按 EXECUTION MASTER V2 继续跟踪 C、D 阻断。
- 最新基线：main `5df8b0603355566e7fa1eb87906129c11a8211ab`（已含 PR #61、#70）；分支：`codex/main-assistant-conversation-first-20261014`；HEAD：`f11c48a1451e723b69316be134f725760d6923bd`。
- 状态：A 的 T1/T2 与必需 GitHub CI 已通过，PR #69 仍 Draft/BLOCKED。真实 Provider 生成、实际 APISIX/浏览器 T3 未验证；未使用额外付费额度。

## A 实现

- `/chat/agent*`、`/chat/customer-service*` 和对应 `/memory/*` 路由由服务端注册路径绑定产品身份；不信任 body 的用户、产品、域提示；产品入口要求可信用户与租户。
- Session、stream、memory user、幂等键均按产品隔离；Resume 与 Abort 校验同一可信用户/租户；能力候选和执行点继续双重检查产品范围。
- 主助手现有 general_chat 路径接管明确的一般概念、地点、日常对话与无活跃域的短追问；避免进入分类/embedding 路由；客服及内部业务语义仍保留原路由。
- 普通 `/chat/*` 共享入口按公共产品处理并禁止持久记忆；主助手与客服前端改用产品专属 Chat/Memory API。
- 未修改旅游实现、`frontend-admin/`、退款业务和生产/共享数据库。

## 最新 HEAD 验证

- `git fetch origin main` 获取 `5df8b0603355566e7fa1eb87906129c11a8211ab`；`git rebase origin/main` 无冲突，PR #69 以精确 `--force-with-lease` 更新到 `f11c48a1451e723b69316be134f725760d6923bd`。与已合并 #70 的变更文件无重叠。
- 后端八个定向测试文件命令：`D:/Python/python.exe -m pytest backend/tests/api/test_chat_memory_persistence.py backend/tests/api/test_chat_stream_hardening.py backend/tests/api/test_memory_routes.py backend/tests/orchestration/graph/test_main_conversation_fast_path.py backend/tests/orchestration/graph/test_stream_events_flow.py backend/tests/orchestration/router/test_product_scope.py backend/tests/orchestration/router/test_routing_engine.py backend/tests/orchestration/test_request_context.py -q --no-cov -m "not pg"`；第一次默认误连 `localhost:5432`（无密码）为 98 passed / 13 setup errors；随后改用一次性 PostgreSQL `agent_a_test` 隔离库复跑，111 passed，容器已自动清理。
- 主前端 Chat/Memory/SSE：`npm run test -- --run src/api/chat.test.ts src/api/memory.test.ts src/hooks/useSSE.test.tsx`，6 passed；`npx tsc --noEmit` 通过。
- 客服前端 Chat/Memory：`npm run test -- --run src/api/chat.test.ts src/api/memory.test.ts`，3 passed；`npx tsc --noEmit` 通过。
- 修改后端模块 `compileall`、`git diff --check origin/main...HEAD` 通过。
- GitHub CI：Secret Scan run `38687262256`、Contract Quality Check run `38687262252`、RAG PR Smoke run `38687262263` 均 success；真实 API job 按门禁 skipped。当前 Review thread 0，submitted review 0。

## C3 SQL / Memory 已有证据

- PR #69 较早 HEAD 上完成了 235 个 SQL/Memory 定向测试；隔离 SQL PostgreSQL 16 passed，确认只读身份 SELECT=true / INSERT=false；L3 pgvector 检索与删除屏障 17 passed；SQL HTTP 授权隔离测试 10 passed。
- 这些是 A worktree 的 T2 证据，未在本次 rebase 后重跑，也不代表最新 main 集成或真实 APISIX/浏览器 T3。

## Clash / 真实 Provider 阻断

- Windows shell 未导出 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`；HTTPX 直连无认证 OpenAI `/v1/models` 为 `ConnectError`，临时指向 `http://127.0.0.1:7897` 得到 HTTP 401。401 只证明 API host/TLS 可达，不是模型请求成功。
- 当前 Docker `agent-app-1` 未配置 proxy 环境变量；历史无认证探测在默认路径及 `host.docker.internal:7897` 路径均得到 HTTP 401。未触发模型生成或计费，也未持久修改代理配置。
- A 的真实 Provider、APISIX/浏览器成功/失败路径尚未验证；真实 Provider 调用需先获测试额度授权。Admin 专属前端迁移由 Admin 会话处理，本任务未修改 `frontend-admin/`。

## 队列与下一步

- B2 PR #70 已由用户授权合并，merge commit `5df8b0603355566e7fa1eb87906129c11a8211ab`；sandbox 退款申请及隔离 PG/UI 证据留在 PR #70。
- C1 PR #66 由原 owner 继续，PDF Worker SIGKILL 仍阻断最终 IndexRun/Search；不接管。
- C2 PR #52 的严格质量 Gate Top-1 为 0.7143（门槛 0.80），费用/预算归因未验证；现有付费额度耗尽，保持 BLOCKED，不降低门槛、不触发新调用。
- A 真实 Provider T3、C1 PDF 终态、C2 质量及费用门禁解除后，再推进 D 的统一真实 API/SSE/PG/Trace/浏览器验收；没有凭据或授权额度时保持 BLOCKED。
