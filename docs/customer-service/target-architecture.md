# 目标架构（target-architecture.md）

> 依据：REFACTOR-TASK-SPEC.md 第 4~6 节 · 2026-09-17
> 原则：不推倒重写。现有 9 节点图骨架、确定性路由分层、状态机纯函数层、APISIX 认证链全部保留；只收敛失控部分。

---

## 1. 总体架构

```
┌─ 用户端 frontend/ ─────────┐   ┌─ 坐席端 frontend-admin/ ────────────┐
│ CSDrawer + CSConfirmCard   │   │ /cs/handoff 工作台 + WS/轮询降级     │
└──────────────┬─────────────┘   └──────────────────┬──────────────────┘
               ▼                                    ▼
┌────────────────────────── APISIX（唯一入口）──────────────────────────┐
│ JWT/API-Key 鉴权 · 身份头注入 · 限流 · 请求ID · /api/* · SSE · /ws/*   │
│ （禁止：业务路由、LLM 调用、Celery 调度、状态机进 Lua）                │
└──────────────────────────────┬───────────────────────────────────────┘
                               ▼
┌──────────────────────── FastAPI 应用层 ───────────────────────────────┐
│ api/          会话/消息/转人工/确认/坐席工作台/任务状态 端点           │
│               统一响应协议 + 统一异常(error-code) + 幂等键头          │
│ application/  conversation_service · message_service · handoff_service│
│               confirmation_service · task_service                    │
│ domain/       四个独立状态机（conversation/handoff/confirmation/action）│
│ graph/        现有 9 节点图收敛：builder/state/router/nodes           │
│ services/     knowledge(RAG) · order · logistics · action(沙盒)       │
│ repositories/ 唯一 DB 访问层（原子条件更新/唯一索引/事务）             │
│ tasks/        Celery：超时扫描 · 过期清理 · 事件补偿                  │
│ realtime/     Redis pub/sub 事件总线 + 序列号/去重 + WS Hub           │
│ security/     input/output guard · permission（已有）                 │
│ observability/ trace · audit 落库 · 指标                              │
└──────┬──────────────────┬──────────────────────┬─────────────────────┘
       ▼                  ▼                      ▼
  PostgreSQL          Redis                  Celery Worker
  （唯一事实源：      （缓存/broker/锁/      （后台任务：RAG索引/
   会话消息/工单/      事件pubsub/短期锁，    超时扫描/过期清理/
   确认/动作/审计/     非事实源）             通知/补偿）
   任务/checkpoint）
```

实际目录以现有项目为准：不机械搬迁 `backend/customer_service/` 既有结构，只在其内部按上述职责收敛。

## 2. LangGraph 收敛目标

现状（保留骨架，修正职责）：

```
START → cs_state_loader → cs_pending_handler ─┬(无 pending)→ cs_supervisor ─→ expert ─┐
                                              │                    ↑__________________│(≤5次)
                                              └(有 pending)→ 确认/取消/过期/追问 → cs_reporter → END
```

收敛动作：

1. **IntentRouter 收进图内语义**：supervisor 退化为「确定性派发 + 少量护栏」，route_path→expert 映射统一到 `graph_state.py` 一处；supervisor 不再直写 handoff_store（超时恢复移入 Celery）。
2. **确认流程单实现**：pending_handler 为唯一确认路径，ActionExpert 仅负责"生成 proposal + 按确认执行"，删除重复实现段。
3. **节点契约**：每个节点明确输入/输出/超时/失败行为；失败统一转 ExpertResult(status=failed)（机制已有，补超时）。
4. **无 interrupt 变更**：确认继续用「pending 持久化 + 每 turn 重进图」机制（已验证可用），但补幂等与 Celery 过期扫描。
5. **compat 适配器保留**：cs_prefilter→主图适配保留，逐步减少重复的 handoff 触发检测（收敛为 prefilter 一层）。

## 3. 状态模型（PostgreSQL 唯一事实源）

四个独立状态机，不混字段：

| 状态机 | 存储 | 关键约束 |
|---|---|---|
| conversation | conversations.status + handling_mode | 现状保留 |
| handoff | handoffs | 原子条件更新 `UPDATE ... WHERE handoff_state='waiting_human'`；单活跃唯一索引 `(user_id) WHERE handoff_state IN ('handoff_requested','waiting_human')` |
| confirmation | confirmations | `UPDATE ... WHERE state='pending'` 前置条件 + 影响行数判定；EXPIRED 独立终态（修正 cancelled 冒用）；单活跃唯一索引 |
| business_action | agent_actions（启用现存表） | action_id 幂等键唯一；EXECUTING 带补偿记录 |

内存 Store 降级为只读缓存：写路径 DB 权威 → 失败即报错（不再 cache-only 静默降级）；读路径可缓存 + 手动失效收口。

## 4. 事件与实时

统一事件格式（任务书第 8 节）：

```json
{"event_id": "...", "event_type": "message.created", "conversation_id": "...",
 "task_id": null, "trace_id": "...", "sequence": 1, "payload": {}}
```

- realtime.py Hub 保留，事件源改挂 Redis pub/sub：跨进程广播；sequence 按会话单调递增（取自 messages.id / 事件表）。
- 事件持久化（事件表或复用消息表游标）→ WS/SSE 断线后可按 since 补发；前端按 event_id 去重。
- WS 路由进 APISIX（/ws/*），管理端去掉 127.0.0.1 硬编码。

## 5. Celery 职责（客服域接入点）

| 任务 | 触发 | 幂等键 |
|---|---|---|
| handoff 超时扫描 | beat 每 30s | handoff_id + 期望状态 |
| confirmation 过期清理 | beat 每 60s | confirmation_id + state='pending' 条件更新 |
| 事件补偿（pub/sub 失败重发） | 失败入队 | event_id |
| RAG 索引 / 报表 | 既有 | 既有 |

复用现有 tasks 基建（tasks 表、退避重试、signals），不新建第二套编排。

## 6. 可观测性

- 全链关联：request_id（APISIX X-Trace-Id）→ conversation_id/message_id/trace_id/task_id/graph_run_id 写入 cs_context 与日志上下文。
- 路由结果/置信度/decision_layer 如实标注（修正 layer 失真）；RAG 检索/引用/拒答入 trace；审计落 audit_logs/agent_actions 表（启用现存表）。

## 7. 明确不做

- 不新增 Agent/Supervisor/Router/Store；不推倒现有图；不动 RAG/主图/认证/APISIX 认证链；不把 simulate 伪装成真实业务；不机械搬迁目录。
