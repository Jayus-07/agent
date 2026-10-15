# FE-4 真实会话 Golden 执行记录

执行日期：2026-10-15
FE 分支基线：`codex/travel-v2-be4c`，BE-4C HEAD `d915bb4662e1da1fba6fc83ad8a1dd114562f6f3`。
真实模型请求：本轮未发送；没有获授权的付费模型预算。Fixture 测试结果只记录为工程回归证据，不替代真实会话 Golden。

## 运行配置观察

从运行中的 `agent-app-1` 容器检查到：`TRAVEL_LLM_INTENT_ENABLED=true`、`TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED=true`、`TRAVEL_CHECKPOINTER_ENABLED=true`。`TRAVEL_TURN_DECISION_LLM_ENABLED` 未显式设置，按当前配置由前两项推导为 `true`；`TRAVEL_LLM_REPORTER_ENABLED` 未显式设置，默认 `true`；`TRAVEL_USER_DECISION_INTERRUPT` 未显式设置，默认 `false`。

隔离 T3 应用容器 `agent-a-t3-b9bf62bc5c-app` 的六个开关均未显式设置，有效默认值为：intent、slot enrichment、turn decision、checkpointer、user decision interrupt 均关闭，Reporter 开启。两组运行配置不同；没有把其中一组配置推断成所有环境的实际状态。本轮没有调用模型，因此下表的模型决策和 Provider 调用均为未观测。

## 场景记录

| 场景 | 输入与前置上下文 | 入口与预期路由 | 本轮实际模型/规则路径 | Tool 调用与 Revision | 工程证据 | 真实 Golden |
| --- | --- | --- | --- | --- | --- | --- |
| 1 正常聊天 | “你好。”；无 Trip | `/travel/chat` → 旅游 SSE 对话 | 未运行；没有模型分类结果 | 未观测 | 普通聊天仍走现有 SSE；本轮无真实输入 | `NOT_RUN` |
| 2 出发地和目的地 | “我从上海出发，下周想去杭州玩三天。”；新会话 | `/travel/chat` → Requirement/Turn Decision | 未运行；未核实上海是否保留为 origin、杭州是否为 destination | 未观测；无 Revision 证据 | 无该输入的非 Fixture 结果 | `NOT_RUN` |
| 3 多轮补充条件 | 先给出上海至杭州三天，再说“两个人，预算两千，想轻松一点。” | 同一 `conversation_id` → 合并会话上下文 | 未运行；未核实条件是否增量合并 | 未观测 | `useTravelConversation` 复用服务器消息 API，并按会话 ID 隔离；本轮没有真实多轮输入 | `NOT_RUN` |
| 4 生成行程 | 已确认目的地/日期等约束后说“帮我规划一下。” | `/travel/chat` → 规划 SSE → 服务端确认后创建 V2 Trip | 未运行；规划模型、Validator 与实际行程结果未观测 | 未观测；没有真实 Trip/Revision 写入 | `TravelChatPage.test.tsx` 使用 Fixture 验证成功回执后才显示保存，并保留同键安全重试 | `NOT_RUN` |
| 5 只读问题 | 已有正式 Trip，问“第二天会不会太赶？” | Trip 对话 → `/ai-edits` 分类为 `read_only` → 现有 SSE | Fixture 验证了客户端按 `read_only` 走 SSE；真实分类未运行 | Fixture 中不调用 Trip 保存、不刷新 Revision；Provider Tool 状态由 SSE 渲染 | `TravelV2TripConversation.test.tsx` 覆盖只读路径、真实 SSE 事件形状、阶段和 Tool 降级展示 | `NOT_RUN` |
| 6 局部修改 | 已有正式 Trip，问“把西湖改到上午十点。” | Trip 对话 → `/ai-edits` → 按原 operation key 查询权威状态 | Fixture 验证 UI 协议；AI 编辑模型未运行 | Fixture 中只有 `saved` 回执、匹配 Trip/key/base Revision 且权威状态成功后才刷新 Trip 与版本历史 | `TravelV2TripConversation.test.tsx` 定向测试通过；本轮没有真实模型或数据库写入 | `NOT_RUN` |
| 7 真实冲突 | 已有正式 Trip，要求两项安排同一时间 | Trip 对话 → `/ai-edits` → Pending 查询 → 仅提交服务端允许的选项 | 未运行真实冲突分类；Fixture 覆盖服务端给选项及无允许选项两种 UI 状态 | Fixture 中无允许选项时保持阻塞，只允许取消；真实 Revision 未写入 | FE Pending 定向测试通过；BE-4C 隔离 PostgreSQL/API 证据见 PR #62 | `NOT_RUN` |
| 8 超时恢复 | AI 编辑超过 90 秒；原请求仍运行、状态被查询为 UNKNOWN，随后返回合法 Pending | 同一 operation key → 状态查询 → 迟到 Pending 条件写入 | 真实模型调用未运行；BE-4C 测试通过受控延迟模拟原请求 | 不换 key、不重复正式写入；隔离 PostgreSQL 回归覆盖迟到 Pending 与终态保护 | `backend/tests/travel_v2/test_trip_ai_edit_postgres.py::test_late_pending_recovers_after_same_key_marks_request_unknown` 与 `::test_late_pending_does_not_replace_terminal_request` 已在 Stage 1 T2 验证 | 后端迟到 Pending 回归 `PASS`；真实会话 Golden `NOT_RUN` |
| 9 刷新和切换行程 | Trip A 对话刷新后恢复，再切换 Trip B | 工作台/chat 路由携带 `trip_id` 与 `conversation_id`；Trip 及 Revision 从服务端读取 | 未运行真实浏览器 | 无真实 Revision；未观察 A/B 实际切换 | 会话 ID 映射、入口链接及工作台/规划页定向测试通过；真实浏览器 E2E 未运行 | `NOT_RUN` |
| 10 无法安全执行 | 已有正式 Trip，要求“三天行程全部重排，再换城市。” | Trip 对话 → `/ai-edits` → 服务端返回 `unsupported` | Fixture 验证了明确拒绝和规划 SSE 不启动；真实分类未运行 | 不调用 planner SSE，不提交 Trip 写入，不产生 Revision | `TravelV2TripConversation.test.tsx` 定向测试通过 | `NOT_RUN` |

### TRIP_RESTORE 工程路径

对话只将带明确版本号的“恢复/还原到 vN”识别为恢复请求，并提供打开对应历史确认项的入口；不向 `/ai-edits` 发送该请求，也不直接写入。历史面板核对指定 Trip 的 Revision 后，仍由用户确认并调用现有 V2 Restore API，服务端负责权限、版本 CAS、幂等和新增 Revision。缺少明确版本号的恢复请求继续由 `/ai-edits` 澄清。该接线有定向 Fixture 测试，尚未做真实会话 Golden。

## 本轮运行证据

- T0 类型：`npx tsc --noEmit` 通过。
- T1：`npx vitest run src/api/travelV2.test.ts src/components/travel/v2/TravelChatPage.test.tsx src/components/travel/v2/TravelV2TripWorkspace.test.tsx src/components/travel/v2/TravelV2TripConversation.test.tsx src/components/travel/v2/travelV2Conversation.test.ts src/components/travel/travelRuntime.test.ts --reporter=dot`：6 个文件、80 项通过。
- Next 页面开发服务对 `http://localhost:3100/travel/chat` 返回 HTTP 200；这不算真实浏览器或后端业务链路验收。
- `npx next lint --file ...` 被 Next 首次配置向导阻塞；仓库中没有 ESLint 配置，未生成无关配置文件。
- 真实浏览器 E2E：`NOT_RUN`。Edge 自动化未能安全确认当前 URL，Playwright Chromium 连接器报告本机 Chrome 不存在；按任务要求没有重复下载浏览器。
- 真实 Provider/付费 Golden：`NOT_RUN`，没有发起模型请求。
- BE-4D 分享/ICS：前端接入已合并到 main 的 #64 API 契约。#72 已同步到 main，PR 差异只包含 FE-4 集成文件；真实 HTTP BFF/API INT-1 结果见下节；真实浏览器 E2E 仍为 `NOT_RUN`。

## BE-4D 固定 Revision 分享与 ICS INT-1

本轮 FE 接线对应 PR #64 的既有 API，不另建 Trip/Revision 模型：版本历史逐条提供固定 Revision 的分享创建、幂等恢复、复制、撤销和 ICS 下载；匿名分享页从 URL fragment 一次读取令牌后清除地址栏副本，后续只经同源 BFF 请求头发送令牌。

### 实际运行验证

- 隔离 PostgreSQL：使用独立临时容器与 `travel_v2_test_fe58c13accd8` 数据库；按顺序执行 `084`、`085`、`086`、`094`、`095`、`096` 旅游迁移，未连接共享开发数据库。
- Next.js BFF：本地 `http://127.0.0.1:3210`，代理到临时 FastAPI 服务 `http://127.0.0.1:3211`；前端服务从当前分支源码临时镜像启动，复用已有 node_modules，绕过 C: worktree 到 D: 依赖 junction 的 Next 开发态解析问题，没有重新安装依赖。FastAPI 挂载本分支真实 Travel V2 router，临时中间件只接受合成 `X-API-Key`，并拒绝匿名分享请求携带 Bearer/Cookie。
- `/travel/share/{share_id}` 服务端页面：`200`，HTML 带 `noindex` 与 `no-referrer` 元数据；浏览器客户端 hydration 未运行。
- `GET /api/travel/v2/shares/{share_id}`：`200`；BFF 实际转发 `X-Travel-Share-Token` 与服务端 `X-API-Key`，没有用户 Bearer/Cookie；固定 Revision 和公开字段白名单符合预期。
- 分享响应：`Cache-Control: no-store, private`、`Referrer-Policy: no-referrer`、`X-Robots-Tag: noindex, nofollow` 均透传；`Vary` 保留 `X-Travel-Share-Token`（Next dev 额外附加 RSC 变体字段）。
- `GET /api/travel/v2/shares/{share_id}/export.ics`：`200`；日历按 `Asia/Shanghai` 本地日期时刻输出，`Content-Disposition` 的 RFC 6266 UTF-8 文件名包含中文“杭州”。
- 以同源 BFF 请求私有 `/api/travel/v2/trips/{trip_id}` 且不提供 Bearer：`401`。分享令牌没有扩展到私有 Trip 读取权限。
- 此 INT-1 运行使用真实 Next BFF 与真实 Travel V2 router / 隔离 PostgreSQL；后端走本地直连 router，没有经过 APISIX。未执行真实浏览器 E2E 或真实模型调用。

### 本轮定向验证

- T0：`npx tsc --noEmit`：通过。
- T1：`npx vitest run src/api/client.test.ts src/api/travelV2.test.ts src/components/travel/v2/TravelTripRevisionActions.test.tsx src/components/travel/v2/TravelV2PublicShare.test.tsx src/components/travel/v2/TravelV2TripWorkspace.test.tsx src/components/travel/v2/TravelV2TripConversation.test.tsx`：6 个文件、92 项通过；覆盖匿名 BFF 客户端不发送 Cookie/Bearer、固定 Revision 路由、分享幂等重试、撤销、ICS 中文文件名及 Strict Mode 下 fragment 令牌恢复。
- 真实浏览器 E2E：`NOT_RUN`；需在有可用浏览器的环境打开 `/travel/share/{share_id}#token=...` 验证客户端读取 fragment、自动清除地址栏及完整交互。

## 2026-10-15 同步 main 后复验

- #64 已 squash 合并至 main（`c5707b320bb2b182038a94ec38e8832e2e1fad09`）。为避免 #72 改 base 后重复显示 BE-4D，保留旧分支快照，并将原 PR 相对 stacked base 的 21 个 FE 文件差异重放到最新 main `d02c80c83a2249fa5ba7fd6899bda65a74fb31f0`；无冲突，未带入 BE-4D 后端文件。
- T0：`npx tsc --noEmit`、`git diff --check`：通过。
- T1：9 个定向 Vitest 文件、128 项通过；覆盖会话集成、分享、BFF 客户端与 Trip 工作台。
- 真实浏览器 E2E：`NOT_RUN`；HTTP INT-1 结果仍见上节，不等同于浏览器交互验收。
