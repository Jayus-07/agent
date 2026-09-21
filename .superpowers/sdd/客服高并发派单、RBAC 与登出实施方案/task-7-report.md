# Task 7 report — P7 接单、拒绝、重派与坐席工作台

## 交付范围

| 项 | 内容 |
| --- | --- |
| Offer 生命周期 | `dispatch/offers.py`：accept / decline / reassign；`agent_offered → human_active | waiting_human` |
| 坐席 API | `GET /cs/agents/me/offers`、`POST .../accept`、`POST .../decline`、`POST /cs/handoffs/{id}/reassign` |
| reaper | `dispatch/reaper.py`：每 1s tick（reaper → dispatch → relay 顺序），30s 过期 offer 回队列；attempt≥5 或超 600s 终态关闭并恢复 AI |
| 冷却期 | 拒单/超时坐席在本工单上 60s 内不再被派（`agent_in_offer_cooldown_expr`）；attempt 不回滚 |
| 状态机修复 | `agent_offered` 自 P6 起已写库但枚举缺失（会抛 ValueError）；补齐 WAITING_HUMAN ⇄ AGENT_OFFERED → HUMAN_ACTIVE/CLOSED |
| Outbox 写侧 | 全部生命周期事件改为事务内 `outbox.append_event(pending)`，提交后由 relay 投递（P8 承接） |
| 身份收口 | offer 端点一律 JWT 反查 `cs_agents.auth_user_id`，API-Key 通道 403；claim/agent-messages/close 的 body.agent_id 变为可选并忽略 |
| 前端 | 手工 agent ID 输入移除；新增「待接单」面板（倒计时 + 接单/拒单）；队列区改名「处理中 / 排队」；WS 处理 offered/offer_expired/offer_declined/reassigned/handoff_closed |

## 关键设计决定

1. **加锁顺序 `conversations → handoffs → cs_agents` 全链路一致**（P4 入池、P6 派单、P7 accept/decline/reassign、P7 reaper 同序）——accept/reassign 从 handoff_id 入手时先无锁预读 conversation_id 定序，与 dispatcher 完全同构。
2. **重派把自动重试预算重新计满**（attempt_count=0、total_deadline 顺延 600s）：5 次上限是自动重试兜底，不是主管意图；但 assignment_version 仍 +1，在途旧 accept 立即失效（409）。
3. **拒单消耗一次 attempt**：否则「拒单-重派」无限循环架空 5 次上限。
4. **accept 幂等语义**：同坐席重复点接单 → 409（消息「工单已由本坐席接单」）；assignment 已被 reaper/主管解除但状态未刷新时以 assignment 为准拒绝，避免「接单成功但无人负责」。

## 验收对照（方案 §六 P7 完成标准）

| 标准 | 结果 |
| --- | --- |
| 过期后 2 秒内释放并重派 | tick=1s、reaper 排在 dispatch 前，同 tick 即可重派（单测锁定顺序）；真实时钟验收依赖容器启动（P9 门槛） |
| 旧版本 accept 返回 409 | `test_accept_with_stale_version_is_rejected` 等覆盖 |
| 非被分配客服 accept/decline 403 | `test_accept_by_another_agent_is_forbidden`、API 层 `test_accept_maps_service_errors` |
| 去掉手工 agent ID | 前端输入移除 + 后端忽略 body.agent_id（JWT 通道服务端反查） |

## 测试证据

- P6+P7 聚焦：`94 passed, 1 skipped`
  - `test_dispatch_offers.py` 15 例（accept/decline/reassign/list 语义 + outbox 事件）
  - `test_dispatch_reaper.py` 9 例（释放/关闭/竞争/顺序 + outbox 事件）
  - `test_cs_agent_offers_api.py` 7 例（403/404/409 映射 + 身份闸）
  - `test_cs_dispatcher.py` tick 顺序与 reaper/relay 开关
- compileall / Ruff / `git diff --check` 通过。

## 未验证（登记为门槛，不宣称通过）

- 真实时钟下「过期后 2 秒内重派」的端到端观测（需容器环境）。
- 坐席工作台浏览器端到端验收（需 P7 前端构建 + 网关）。
