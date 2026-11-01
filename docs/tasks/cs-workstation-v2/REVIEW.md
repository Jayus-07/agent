# REVIEW — cs-workstation-v2 客服双端重建（P0–P8）独立审查报告

- 审查会话：独立审查（只审不修），2026-11-01
- 审查基线：**origin/main@e2bc1dc**（PR #184 合并点，`git fetch` 后核对一致）
- 审查代码位置：执行会话 worktree `D:\Program Files\workplace\agent-cs-v2`（分支 `docs/cs-v2-p8-progress` @ 0132754，`git diff e2bc1dc HEAD` 为空，内容与基线完全一致）
- 事实源：TASK.md（第 3.3/4/5/8/11 节）、PROGRESS.md（日志一～二十）、原型两份
- 结论先行：**九期交付与总计划符合，七条验收全部 PASS（其中资金/审核闭环为"引用 + 抽验"），未发现阻塞级问题**。问题清单：0 阻塞 / 1 建议 / 2 记录。

---

## 1. 总计划符合性

| 计划要素 | 实况 | 结论 |
|---|---|---|
| 九期分期（P0 脚手架→P1 会话→P2 交易→P3 转人工运营→P4 快捷+Copilot→P5 退款审核→P6 质检→P7 看板知识→P8 切换清理） | 九期均按 TASK 第 4 节主题交付，每期独立分支+PR+CI+合并+PROGRESS 回写 | 符合 |
| 不复用旧 CS 业务组件（TASK §2/§6） | 新组件树 `frontend/src/cs2|mall`、`frontend-cs/src/cs2`；旧组件物理删除；双端守卫测试双断言（见 §4.4） | 符合 |
| 后端领域服务只增不改坏 | P8 后端仅 3 文件（cs_admin.py / cs_agent_offers.py / shared/idempotency.py），`git` 证实未触碰 confirmation_flow / dispatch / MallService / Mock 资金路径 | 符合 |
| 事件契约只追加 | cross-cutting-contracts.md 增量记录 agent.status_changed / transfer_* / sla.* / assist.suggestion，既有封套未动 | 符合 |
| 迁移 100+ 顺延 | `backend/sql/migrations/` 最新 **128_cs_qa_scores.sql**，125/126/127/128 为本任务新增且已登记 init_db + 迁移守卫 | 符合 |
| 顺延项处理（P8 用户决策） | 接单开关 UI + messages 幂等键已补齐（本审查 REST 实测 13/13）；更多菜单三功能维持降级（TASK §5 P4 回写③，无后端能力不做假按钮）；站内通知/FAQ 审核 UI 复核为已交付 | 符合 |

## 2. 逐期对账表

| 期 | PR（台账） | merge commit | git 核验 | 关键证据（PROGRESS 日志） |
|---|---|---|---|---|
| P0 | #147 | 4b80207 | ✅ 存在 | （五）新 API T2 14 passed + 双端壳路由 + 迁移基线回写 099 |
| P1 | #151 | b236a62 | ✅ 存在 | （六）双端对话链路 + WS 封装 + 全量 T0 |
| P2 | #155+#156 | 99c62ea / 2945f5b | ✅ 均存在 | （七）商城三页 + Mock 账本 + 资金闭环 REST 实测 + CI 失误修复记录 |
| P3 | #164/#165 | f600be6 | ✅ 存在 | （九）（十）五项实测 + SLA 广播缺陷修复 + CI 二轮全绿 |
| P4 | #166/#167 | 2677af9 | ✅ 存在（#166 合并点；#167 为文档回填 PR） | （十一）（十二）canned T2 20 passed + WS 帧实测 |
| P5 | #169/#170 | 6232023 | ✅ 存在 | （十三）（十四）审核全链实测（回款 4642→5000）+ 审计行 |
| P6 | #173/#175 | 15706c3 | ✅ 存在 | （十六）（十七）QA 全链 REST 10 项 + Celery 抽样实测 |
| P7 | #177/#178 | bbc2d32 | ✅ 存在 | （十八）（十九）看板数值逐值比对 + FAQ 草稿幂等 |
| P8 | #183/#184 | fd989c3 / e2bc1dc | ✅ 均存在；PR #183 state=MERGED、mergeState 核验 | （二十）路由切换 + 删旧 + 顺延补齐 + REST 11/11 + 浏览器级终验 |

对账结论：交付台账与 git 实况一致，无虚报期次；P2 的 PR #155「未确认全绿即合并」失误已在日志（七）如实自记并有 #156 修复闭环，符合"如实记录"要求。

## 3. 七条验收逐条结论

| # | 验收标准 | 结论 | 依据 |
|---|---|---|---|
| 1 | 矩阵 3.1/3.2 逐行打勾 | **PASS（引用）** | TASK §3.3 终验记录逐行给出证据口径（P1–P7 代码/链路级 + P8 浏览器级）；「输入区·更多」行明确标注降级交付。本审查复核了矩阵行对应的代码实况（七路由、AgentTopBar 开关/通知、提案卡、快捷回复/AI 面板、退款/质检/看板/知识页均存在且挂正确路由）。浏览器级逐行渲染本审查未独立重跑（见 §5 未跑范围） |
| 2 | 零旧组件引用 grep 断言入 PR 守卫 | **PASS（实测）** | 双端 `no-legacy-imports.test.ts` 均为「命名空间零引用扫描 + 旧文件物理不存在」双断言；扫描根与 TASK §5 P8 回写③一致（frontend: `src/cs2`+`src/app/customer-service`；frontend-cs: `src/cs2`+`src/app/cs`）。本审查独立全仓 grep 复核：旧资产引用仅剩守卫测试自身与注释（见 §4.4） |
| 3 | backend T2 + 双端 vitest 全量绿 + PR #183 CI | **PASS（实测）** | 本审查复跑：T2 16 passed、定向回归 64 passed、frontend 76 files/654 passed、frontend-cs 45 files/323 passed、tsc 前端 16 错=存量基线（bff-proxy 14 + store/chat 2，零新增）、frontend-cs 0 错、双端 build 成功。`gh pr view 183`：MERGED，checks = Detect RAG changes/scan/RAG Smoke SUCCESS + preflight/Evaluation SKIPPED（非 RAG PR 预期），与 TASK §3.3 声称逐项一致 |
| 4 | 资金闭环实测（买→付→取消→退款→回款） | **PASS（引用+抽验）** | 写路径证据=PROGRESS 日志（七）（数字链 5000→4602 支付→取消回款）与（十三）（审核回款 4642→5000，kind=refund）；P8 对资金链路零代码改动（PR #183 文件清单证实）。本审查抽验：agent_business 库 `finance.wallet_balances/wallet_ledger` 存在、NUMERIC(14,2) DECIMAL 语义、99001 种子 ¥5000 与 P8 浏览器实测读数一致（实测流水已按台账清理回种子态） |
| 5 | 审核闭环（提交→supervisor 通过/驳回→用户联动+审计） | **PASS（引用）** | PROGRESS 日志（十三）：http 全链实测通过/驳回双路径、终态锁 409、意见必填 422、折叠卡实时联动、audit_logs `refund_review|human|…|success` 真库行 |
| 6 | 文档四件同步 | **PASS（实测互证）** | customer-service.md 重写且与代码互证（双端路由/七视图/接单开关/幂等键/WS 层逐一对应实况）；frontend-guide.md 补命名空间与守卫现状段；cross-cutting-contracts.md 追加客服实时事件契约与 messages 幂等语义；commands.md 经 grep 复核仅通用目录名引用、无旧路由残留，「无需改」结论成立 |
| 7 | 旧组件删除 + 路由切换 + 主链路零回归 | **PASS（实测）** | 六处旧资产物理不存在（`ls` 逐一证伪）；frontend-cs 构建产物恰七条 `/cs/*` 路由零 cs2 残留；frontend 构建产物 `/customer-service`、`/mall`、`/my-orders` 零 v2 残留；主链路冒烟 app /health、frontend /、/customer-service、frontend-admin /login、apisix :9080 全 200；PR #183 改动清单不含 /agent、travel、RAG 链路文件 |

## 4. 问题清单

### 4.1 阻塞级

无。

### 4.2 建议级（1 项）

**R1｜幂等回调内 `run_sync` 10s 超时与幂等 lease 60s 的语义窗口**（建议 P9 候选池评估）
- 位置：`backend/customer_service/_db_loop.py:27-30`（`run_sync` timeout=10）× `backend/app/api/routes/cs_admin.py:1032-1071`（回调把落库桥进幂等边界）× `backend/shared/idempotency.py:347`（lease 60s）
- 场景：DB 慢查询使 `_async_save_my_message` 超过 10s 时，`run_sync` 抛 TimeoutError → executor 按 `operation_succeeded=False` 把该键标记 FAILED——但**协程仍在 cs-db loop 上继续执行并最终提交插入**。此后同键重试：PG 结果仓储读到 FAILED（`get` 返回 None，idempotency.py:591-592）→ Redis claim 允许重执 → 消息重复插入；首个协程提交后同样落一行。
- 缓解事实：T2 与本审查 REST 实测均验证正常时延路径（首发送/重放/冲突/无键）语义正确；触发需 DB 插入 >10s（健康库几乎不可能），且属 lease 型幂等的固有边界、非 P8 逻辑错误（executor 为既有基建）。
- 复现思路（未实际复现，推理自代码路径）：向 5433 注入长事务锁后携新 Idempotency-Key 调 `POST /cs/conversations/my/{id}/messages`，观察 10s 时 503 而消息稍后落库、再以同键重试产生第二行。
- 建议：三选一——①把 `run_sync` 超时提至接近 lease（如 30-55s）并保持 <60s；②TimeoutError 路径改判 UNCERTAIN（阻断自动重试，语义与 crash window 一致）；③messages 行增加幂等键列做行级兜底去重。决策留用户。

### 4.3 记录级（2 项）

**R2｜`frontend/src/app/layout.tsx:19-20` 注释提及已删除的 `/customer-service-v2`**
- 上下文为 "旧过渡路由 /customer-service-v2 与商城两页（/mall、/my-orders）为独立页"——路由已物理删除，该注释易被误读为仍存在独立页。仅注释，无行为影响。复现：`grep -n "customer-service-v2" frontend/src/app/layout.tsx`。

**R3｜frontend-cs root layout 以 `pathname.startsWith('/cs')` 判定工作台布局**（`frontend-cs/src/app/layout.tsx:37`）
- 前缀判定较宽：当前顶层路由仅 `/cs`、`/login`、`/`，无影响；未来若新增以 `/cs` 开头的非工作台路由（如 `/csadmin`）会误入无侧栏布局。建议后续新路由命名避让或改为段级匹配。复现：`grep -n "startsWith('/cs')" frontend-cs/src/app/layout.tsx`。

### 4.4 已核实为合格的关键面（对应审查范围 P8-1～P8-6）

1. **幂等收口**（`cs_admin.py:977-1102`）：闸序正确——归属闸 `_ensure_my_conversation`（user_id+tenant 双因子、404 不泄露存在性）与 handoff 状态闸均在幂等边界外（注释明示"安全语义优先"）；跨 loop 安全（persist 经 `_db_loop.run_sync` 桥至专属后台 loop，`backend/memory/database.py:69-116` 按 loop 注册引擎、含 2026-10-21 连接泄漏事故的防御注释）；广播为真 fire-and-forget（`realtime.py:296-326` 内部全捕获、loop 未绑静默返回，不存在"广播抛异常→标记失败→重试重复插入"窗口）；重放语义完整（PG 结果仓储 request_hash 指纹校验，同键不同内容 `ValueError→409`，缺键 400，Redis 不可用 fail-closed 503）；异常映射齐全（HTTPException 透传 / 其余 503）；回调内含 handoff.user_id 二次归属校验（纵深防御）。
2. **接单开关**（`cs_agent_offers.py:400-487`）：`AgentStatusBody` 双字段可选，只传 status 完全兼容 P3 调用；空 body 422（:453）；事件语义正确（仅 status 变更发瞬态 `agent.status_changed`，accepting 单独更新不发事件——派单过滤实时查库，语义自洽）。
3. **connect_timeout 修复**（`backend/shared/idempotency.py:1354-1366`）：与同文件 `_default_memory_ledger_connection`（:1369-1379）完全对齐（同 `DB_CONNECT_TIMEOUT` 来源，`backend/config/database.py:115` 默认 5s）；修复动机（Windows ::1 解析拖满 130s TCP 窗口→lease 60s 必过期→503）成立；生产容器单 A 记录环境零影响，属纯防御性修复。
4. **删除完整性**：全仓 grep `components/cs`、`useCSChat|useCSHandoffSync|store/csChat`、`customer-service-v2`、`app/cs2`（路由目录）、`AgentAssistPanel`、`csAgentWs`——命中仅守卫测试规则定义与 `frontend/src/app/customer-service/page.tsx`、`frontend/src/app/layout.tsx` 注释；六处旧文件 `ls` 证伪（frontend: components/cs、app/customer-service-v2、api/cs.ts；frontend-cs: app/cs2、AgentAssistPanel.tsx、lib/csAgentWs.ts）；e2e/scripts/.github 零引用。组件命名空间 `src/cs2` 保留为有意决策（TASK §5 P8 回写④）。
5. **前端切换一致性**：frontend-cs 七路由目录齐备（app/cs/{workspace,conversations,tickets,refunds,qa,dashboard,knowledge}）；`cs2/nav.ts` 七 href 全部 `/cs/*` 且 supervisorOnly 标注与 TASK §2.1 角色列一致；旧 `navConfig.NAV` 已更新为新七视图（含 minCsRole）；root layout `/cs` 前缀排除旧 Sidebar；frontend mall 三处跳转（MallPage.tsx:189、MyOrdersPage.tsx:77、SiteNav.tsx:10）均已指 `/customer-service`。
6. **文档一致性**：四件互证通过（见 §3 第 6 条）；TASK §3.3 终验记录与 PROGRESS 日志（二十）逐项对应，无夸大表述（失误与降级均如实记录）。

### 4.5 已知边界（按审查任务书要求，不计缺陷）

- 「输入区·更多」三功能（订单卡片/内部备注/转工单）为有意降级交付（无后端能力不做假按钮，TASK §5 P4 回写③）。
- 浏览器登录用户（872）与演示客户 99001 的订单映射不生效——演示沙盒数据面属 P2 范畴，TASK/PROGRESS 已如实记录。
- frontend-cs `Sidebar/NavGroup/TaskSidebar/components/chat` 为 /agent 任务模式遗留孤儿群（无渲染场景但牵动 layout.test），已记 TASK §11 P9 候选池，P8 有意不删。
- 主栈 apisix/cs(3300) 曾长期未运行属共享栈历史现状（P8 起 apisix 已按主干 compose 修复拉起，本审查 docker ps 复核 healthy）。
- 商城资金为 Mock 账本（DB `finance.wallet_*` + http 模式 Mock 内存钱包），生产支付对接属商城域另立项（TASK §2.4）。

## 5. 测试执行记录

环境：worktree `agent-cs-v2` @ e2bc1dc 等价内容；真库 5433（docker agent-postgres-1）；主栈 16 容器 healthy；测试数据 p8r-/p3t- 前缀用后即清（REST 实测 leftover=0；临时脚本与 API Key 均已清理/未回显）。

| # | 命令 | 结果 | 预期 |
|---|---|---|---|
| 1 | `python -m pytest backend/tests/api/test_cs_agent_status_transfer.py -q --no-cov` | **16 passed**（4 warning 为既有 teardown 噪音，233s） | 16 passed ✅ |
| 2 | `pytest backend/tests/api/test_cs_agent_offers_api.py test_cs_pending_api.py test_cs_admin_idor_gate.py test_cs_confirm_api.py test_cs_customer_context.py backend/tests/customer_service/test_idempotency.py -q --no-cov` | **64 passed** | 定向回归绿 ✅ |
| 3 | frontend `npx vitest run` | **76 files / 654 passed**（55.6s） | 654 passed ✅ |
| 4 | frontend-cs `npx vitest run` | **45 files / 323 passed**（27.9s） | 323 passed ✅ |
| 5 | frontend `npx tsc --noEmit` | 16 错 = bff-proxy.test.ts 14 + store/chat.test.ts 2（存量基线，零新增） | 16 存量 ✅ |
| 6 | frontend-cs `npx tsc --noEmit` | 0 错误 | 0 ✅ |
| 7 | 双端 `npm run build` | 成功；frontend-cs 恰七条 `/cs/*` 路由零 cs2 残留；frontend 含 /customer-service、/mall、/my-orders 零 v2 残留 | 构建通过 ✅ |
| 8 | REST 实测（真栈 :8000，X-API-Key + X-Auth-Type: jwt 模拟网关，Python http.client UTF-8） | **13/13 PASS**：accepting=false 单独更新 200 且 status 保持 online；DB 持久 (False,'online')；空 body 422；组合更新 200；GET 回读一致；首发送 200；同键同内容重放同 message_id；messages 仅 1 行；同键不同内容 409；无键 400；中文无损；跨用户 403 | P8 语义逐项符合 ✅ |
| 9 | 账本抽验（agent_business，只读 SELECT） | finance.wallet_balances/wallet_ledger 存在，NUMERIC(14,2)，99001=5000.00 + topup 种子行（实测流水已按台账清理） | 与 P8 浏览器读数一致 ✅ |
| 10 | 主链路冒烟 | :8000/health、:3100/、:3100/customer-service、:3200/login、:9080 全 200 | 零回归 ✅ |
| 11 | `gh pr view 183`（CI） | MERGED @ fd989c3；3 SUCCESS（Detect RAG changes / scan / RAG Smoke）+ 2 SKIPPED（preflight / Evaluation，非 RAG 预期） | 与声称一致 ✅ |
| 12 | 迁移核对 | `backend/sql/migrations/` 最新 128_cs_qa_scores.sql | 迁移 128 ✅ |
| 13 | 九期 merge commit 存在性 | 4b80207/b236a62/99c62ea/2945f5b/f600be6/2677af9/6232023/15706c3/bbc2d32/fd989c3/e2bc1dc 逐一 `git log` 核验 | 台账一致 ✅ |

**未跑范围（如实声明）**：
- 浏览器级矩阵逐行复核未独立重跑——引用 TASK §3.3 与 PROGRESS 日志（二十）的 browser-use 证据（本审查对矩阵行的结论基于代码实况 + REST 实测 + 该引用）。
- 资金闭环/审核闭环**写路径**全链未复跑——P8 零代码改动（PR #183 文件清单证实）+ 账本基建抽验 + 引用日志（七）（十三）。
- 全量 backend pytest 未跑（遵循仓库"不默认全量回归"纪律）；travel/RAG/frontend-admin 功能级回归未跑（P8 改动清单不含这些链路，仅健康冒烟）。
- 原型 HTML 未做逐像素/逐交互比对（矩阵核验以 TASK §3 为契约、以各期日志与 P8 浏览器证据为凭）。

## 6. 审查结论

cs-workstation-v2 九期（P0–P8）交付与 TASK 总计划相符，交付台账与 git/CI/库表实况对账一致，七条总验收全部成立（2 条实测、3 条实测+引用混合、2 条引用+抽验，口径见 §3）。P8 新引入面（幂等收口/接单开关/connect_timeout 修复/删除/切换/文档）逐项审查未发现阻塞缺陷；1 项建议（R1 幂等超时窗口，建议入 P9 候选池评估）与 2 项记录（R2/R3 注释与前缀匹配卫生）留用户决策。**本审查未修改任何代码，REVIEW.md 为唯一产出，未提交。**

---

## 7. 修复回写（2026-11-01，用户指令「结合生产情况优化修复」）

分支 `fix/cs-v2-review-followups`（自 e2bc1dc 建支后 rebase 至 origin/main@262e839），未提交，待用户确认。修复口径与验证：

### R1（建议）→ 已修复：超时映射 UNCERTAIN + 取消在途协程
- **方案取舍**（结合生产：单机 4C8G、DB 本机容器、连接池可能饱和）：①全局调大 `run_sync` 超时会拖长用户请求阻塞，否决；②messages 表加幂等键列属 schema 变更，成本远超收益，否决；③**把桥超时映射为既有原语 `SideEffectOutcomeUnknown`**（executor 落 UNCERTAIN，Redis `_CLAIM_LUA` 对 UNCERTAIN 返回 conflict → 同键重试 503 保守阻断）+ **`cancel_on_timeout` 取消在途协程**（`_chain_future` 把 cancel 传播到 cs-db loop，`Session begin()` 回滚、连接尽快归还池）——复用 Phase3 STOP C 既有语义，零 schema 变更、既有调用方默认行为不变（opt-in 参数）。
- 改动：`_db_loop.py` `run_sync` 增 `*, cancel_on_timeout: bool = False`（超时后 `future.cancel()`）；`cs_admin.py` `_persist_and_notify` 传 `cancel_on_timeout=True` 并把 `concurrent.futures.TimeoutError` 映射为 `SideEffectOutcomeUnknown`（`except` 仅包住 `run_sync` 调用，协程自身异常/HTTPException 透传语义不变）。
- 回归测试：新增 `backend/tests/customer_service/test_db_loop_bridge.py` 3 例（结果透传/默认超时协程继续完成/cancel_on_timeout 传播 CancelledError）；`test_cs_agent_status_transfer.py` 新增判别性用例（模拟「已落库但调用方超时」→ 首发旧行为会放行同键重执产生第 2 行消息，修复后同键 503 阻断且 messages 恒 1 行）。

### R2（记录）→ 已修复：layout.tsx 注释更正（frontend `src/app/layout.tsx:19-20`，仅注释）。

### R3（记录）→ 已修复：frontend-cs 工作台判定改段级匹配（`pathname === '/cs' || pathname.startsWith('/cs/')`），layout.test 增源码断言守卫（与该文件既有源码断言风格一致）。

### 文档同步
- `docs/development/cross-cutting-contracts.md` 第 1 节 messages 幂等语义句补「落库桥超时按终态未知保守阻断同键重执并取消在途协程」。

### 验证记录（均实际执行，rebase 至 262e839 后复跑）
- 后端：`test_db_loop_bridge.py` **3 passed**；`test_cs_agent_status_transfer.py` **17 passed**（16 既有 + 1 新增）；cs API 定向回归 **64 passed**。
- 前端：frontend-cs vitest **324 passed**（+1 新守卫）/ tsc **0 错**；frontend vitest **654 passed** / tsc 16 错=存量基线；双端 `npm run build` 通过。
- 部署：worktree（rebase 后）`docker compose build + up -d` 重建全部 backend 服务 + frontend/frontend-admin（`devctl.bat rebuild` 在 Git Bash 管道下因确认提示读不到输入而静默退出——零输出零效果，本次以等价 compose 原命令复刻；该坑已在本节留档）；容器内 grep 验证修复标记在位（app/worker ×3）；db-migrate rc=0（含上游新增 129 迁移）；app /health、frontend /、frontend-admin /login、mock /health、网关 :9080 全 200，全部容器 healthy（business-mock 探针曾在宿主高负载下间歇超时，服务本体始终 200，随后自愈）。
- 环境备注：验证期间并行会话在跑 20 文件 pytest 批次并从主仓库重建过共享栈（容器一度为其构建产物、不含本修复）——已按冻结纪律避让其窗口、将本分支 rebase 至其后的 origin/main 再重建，未发生相互覆盖。
