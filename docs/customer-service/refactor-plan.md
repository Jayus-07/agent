# 重构计划（refactor-plan.md）

> 依据：REFACTOR-TASK-SPEC.md 第 11 节 + audit-report.md 结论 · 2026-09-17
> 执行纪律：每阶段结束汇报「改了什么/哪些文件/为什么/验证了什么/测试结果/遗留问题/是否影响主图-RAG-认证/下一步」；阶段完成立即按路径限定提交（防并行会话收编）。

---

## P0：审查与基线（已完成 ✅）

- ✅ audit-report.md（10 项全交付，证据带 文件:行号）
- ✅ 固定任务书：REFACTOR-TASK-SPEC.md 持久化
- ⏳ 待办：确认 docker 环境（postgres/redis/apisix/backend）真实运行状态；建立冒烟清单；向用户确认后进入 P1

**P0 阻断问题清单**（audit-report §9）：FAQ 拒答标尺错位、确认双实现+卡片死代码、确认无幂等、坐席双认领、Store 静默降级、IDOR、审计空转、退款重复检查降级不安全、WS 绕网关。

## P1：基础设施与状态统一（下一阶段）

| # | 改动 | 对应 P0/P1 | 影响面 | 回滚 |
|---|---|---|---|---|
| 1.1 | 确认幂等：confirmation_repo.update_state 加 `WHERE state='pending'` + 影响行数判定 + confirmations 单活跃唯一索引（Alembic 迁移） | P0-3 | confirmations 表 + pending_handler/action | 迁移 downgrade |
| 1.2 | 坐席认领原子化：`UPDATE handoffs SET ... WHERE id=? AND handoff_state='waiting_human'` 判影响行数；agent_id 改为服务端从身份头解析（坐席身份校验） | P0-4 / P0-6 | cs_admin.claim | 单文件回退 |
| 1.3 | Store 写路径修复：DB 失败不再 cache-only 静默，改为抛错+告警日志；内存 Store 降级只读缓存 | P0-5 | handoff_store/confirmation_store | 单文件回退 |
| 1.4 | 归属校验：messages/{id}/rating/{id}/agent-messages 端点补 conversation.user_id 归属校验；管理端列表/详情/统计加 require_admin_user 闸 | P0-6 | cs_admin.py | 单文件回退 |
| 1.5 | 审计落库：cs_audit_entries 写入 audit_logs/agent_actions（启用现存表）；_has_existing_refund 失败改为 fail-safe 拦截 | P0-7/8 | experts/action + cs_graph_node | 单文件回退 |
| 1.6 | 状态写权收口：complaint/handoff expert、supervisor 直写 handoff_store 改走 StateTransitionService（事件恢复） | P1-10 | 3 个文件 | 单文件回退 |
| 1.7 | 状态机修正：EXPIRED 独立终态；Supervisor/pending 收敛 confirmation 双实现（pending_handler 为唯一路径） | P1-14/P0-2 | confirmation_repo + action/pending_handler | 单文件回退 |

验证：backend/tests/customer_service 补 test_idempotency.py（重复确认/重复认领并发）、test_handoff_state.py、test_confirmation_state.py；跑既有 58 个客服测试确认无回归。

## P2：LangGraph 与 Celery 收敛

| # | 改动 | 对应 |
|---|---|---|
| 2.1 | 置信度标尺修复：fine 兜底 conf 与 supervisor 门槛对齐（0.6），低置信但意图明确时放行 Knowledge 而非 finish；decision_layer 如实标注 | P0-1/P1-18 |
| 2.2 | 映射统一：route_path→expert 三套映射合并到 graph_state 一处；域关键词两套合一；转人工触发检测收敛 prefilter 一层 | P2-22 |
| 2.3 | 专家超时：knowledge/action expert 补显式超时（复用 run_expert_safely 扩展） | — |
| 2.4 | Celery 接入：handoff 超时扫描 beat 任务 + confirmation 过期清理 beat 任务（复用 tasks 基建：tasks 表/退避/幂等键） | P1-12 |
| 2.5 | 关键词误判修复：确认意图检测改精确词表+顺序（确认优先于取消判断否定式） | P1-16 |

验证：test_router.py、test_cs_supervisor.py、test_celery_tasks.py、test_graph_e2e.py。

## P3：前后端业务闭环（剧本 A~E）（实施中 ✅ 3.1-3.4 完成 / 3.5 待真实环境验证）

| # | 改动 | 剧本 | 状态 |
|---|---|---|---|
| 3.1 | CSConfirmCard 接通：SSE done 帧下发 pending_action 结构 → 前端渲染确认卡 → POST /cs/confirm（幂等键）→ 后端执行；保留文本确认作降级 | C | ✅ done 帧封套 pending_action → csChat.pendingProposal → CSDrawer 渲染 CSConfirmCard → POST /cs/confirm（409 幂等兜底）；confirm 端点 7 测试 |
| 3.2 | 事件统一格式 + Redis pub/sub 进 realtime + sequence/event_id 去重 + 断线补发（messages 游标已有，事件表补齐） | D/E | ✅ 封套 {type,event_id,seq,ts,**payload}；customer_service.events 表（0002 迁移）+ EventRepository；AgentHub 落库+Redis pub/sub（cs:events channel，专用订阅线程，无订阅者/不可用双降级）；GET /cs/conversations/{id}/events?after_seq= 补发端点；AgentHub 10 测试 |
| 3.3 | WS 过网关：APISIX 加 /ws/* 路由；管理端 CS_WS_URL 配置化去掉 127.0.0.1 硬编码 | D | ✅ apisix.yaml 加 cs-ws 路由（app_chat upstream 600s + limit-conn 20/IP，ticket 鉴权不挂 gateway-auth）；csAgentWs.ts WS_BASE 缺省同源推导（NEXT_PUBLIC_CS_WS_URL 可覆盖）；frontend-admin tsc 过 |
| 3.4 | 用户端 SSE 断线重连 + 轮询退避；用户/坐席消息端点按角色拆分 | — | ✅ 用户端专用 GET /cs/conversations/my/{id}/messages（登录强制 401/本人会话 403/其余透传坐席端实现，4 测试）；useCSHandoffSync 固定 2s → 空闲 4 拍退到 5s、有变化回 2s；SSE 中断已有 catch 分支落错误提示（深重连属 P4 观察项） |
| 3.5 | 五剧本逐一手工+自动化验证（演示账号种子数据核对：订单归属/多订单/无数据反馈） | A~E | ⏳ 待 docker 环境（需与并行会话协调全量 pytest 约 20 分钟窗口） |

## P4：验收与生产化

- 测试矩阵补齐：test_api.py / test_realtime.py / test_failure_recovery.py（Redis 不可用、DB 不可用、Worker 失败、重复提交、刷新恢复、多进程并发）
- 真实 E2E：前端→APISIX→FastAPI→LangGraph→RAG/Service→PG→SSE/WS→前端（至少一次全链记录）
- 故障演练：Redis 停止 / Worker kill / PG 重启各一次，验证无静默数据丢失
- 交付：api-contract.md、state-machine.md、runbook.md、部署手册+回滚方案、已知限制清单

## 风险与回滚总策略

- 全部 Alembic 迁移可 downgrade；代码改动逐文件可回退；每阶段完成后 `git commit -- <pathspec>` 路径限定提交。
- 不碰：主图非客服分支、RAG 索引链路、统一认证 gateway-auth.lua 验签逻辑、旅游/选品等其他域。
- 并行会话纪律：动 backend 前先 git status；跑全量 pytest 前与并行会话协调（约 20 分钟，期间不改 backend）。
