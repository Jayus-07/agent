# Domain Runtime Production Baseline & Freeze（STOP F）

> 日期：2026-09-24 ｜ 前置：STOP A-E 全 PASS
> 冻结点：main @ 912df8e（本轮提交链 9d1c9e5 → 6628fa8 → 6cc3f82 → 093d173 → cfc8389 → 912df8e）
> 冻结点回归：契约四门 + 域隔离 37 用例 + 选品门禁 + 角色 RBAC = **100 passed**

---

## F1 Domain Registry Freeze（最终域矩阵）

| Domain | Router Key | Graph | State（私有） | Async | Side Effect |
|---|---|---|---|---|---|
| Customer Service | route_mode="customer_service"（prefilter ≥2 规则组 / domain_hint 锁域） | cs_graph_node（域图，自动发现） | CSGraphState → cs_context / cs_action_result / cs_audit_entries / cs_pending_action | ❌ 同步图内 | 工单/handoff/确认 CAS/审计幂等落库/outbox |
| Travel | route_mode="travel"（prefilter 正则 / pending-resume / continuation） | travel_graph_node（域图） | TravelGraphState → travel_context | ❌ 同步（checkpointer travel:{conv} 跨轮） | preferences upsert / run CAS 取消 |
| Selection Funnel | route_mode="selection_funnel"（prefilter） | selection_funnel_graph_node（域图） | SelectionFunnelState → funnel_context | ❌ 同步（HTTP 决策任务为独立 in-process 通道，运营门禁） | 导入池/决策 decision_version |
| SQL 数据分析 | 三层 Router direct（rule 强信号 / vector / LLM） | tool_selector→sql_skill | SQLResult/BusinessInsight（无域 TypedDict） | ✅ 可经 interactive_agent 任务 | 无写（SELECT-only 六层 + agent_readonly） |
| RAG / Knowledge | direct（rule/vector/LLM） | rag_skill（pipeline.ask） | AskOutcome（不进图 state） | ✅ rag_index 索引链（Celery） | 索引五路写（file_hash 幂等） |
| General Chat | general_chat（guard greeting / hierarchical general） | general_chat_node | — | ❌ | 无 |

域图注册唯一事实源：`backend/domains/__init__.py` → `domain_graph_registry`；新域禁改 builder。

## F2 Runtime Contract Freeze（必填项）

`OrchestratorState` 共享必填：`question / session_id / user_id / tenant_id / request_context(checkpoint_safe) / routing_context / route_mode`；routing 平铺键（domain/confidence/…）仅 hierarchical 路径写。域私有状态必须收在域 TypedDict + 单一 `*_context` 出口字段；`sql_context/selection_context` 类平铺命名禁止（测试锁定）。canonical `chat_messages` 只由 memory.end_turn 落库，CS 轮豁免，域图禁写。

## F3 State Ownership Freeze

五层边界（详见 STOP A §5 / STOP B §4）：
1. **Shared Runtime State**——F2 清单；
2. **Domain Private State**——域图 TypedDict（轮级）；
3. **Business Persistent State**——DB 权威（CS confirmations/tickets/handoffs、travel run via ConversationContext、selection pools/decisions），checkpoint 只是执行态；
4. **Memory**——L2 chat_messages（每轮原文，CS 豁免）/ L3 memory_records（scope=(tenant,user,memory_key)）；
5. **Checkpoint**——thread：主图 `agent-{s}-{ms}-{u8}`（每轮唯一）/ 任务 `task-{id}` / CS 裸 conv / Travel `travel:{conv}`；tenant 不入 key（P1-4 已知边界，多租户化前必须补）。

新域接入必须声明：数据属主维度（user/tenant/角色共享）+ pending 语义（强绑定/可中断/弱绑定）+ 出口字段白名单。选品「角色内共享运营数据集」为第一样例（P1-1）。

## F4 新 Domain 接入规范（12 步，冻结）

任务书 §F4 12 步照录生效，另加本仓硬约束：
- 用技能 `agent-platform-add-domain-graph`；prefilter 插入 router_node（否则域永不触发）；
- 域 TypedDict + `new_*_graph_input` 白名单（禁预置产物默认值）+ 输出契约 `build_*_result`；
- 身份：user_id/tenant_id 必传（禁 "default" 掩码新先例）；无租户列的数据面必须写明共享语义；
- 权限：挂统一授权（resolve_principal / resolve_operator_role / ensure_approved 三选一），HTTP 侧门禁比照 STOP A P0-1；
- 测试：`tests/domain_runtime/` 加隔离/泄漏用例；跑八目录回归。

## F5/F6 Metrics & Alerts

现有指标足够，未新增：`routing_domain_total{domain,source}` / `router_decision_total{mode}` / `router_layer_total` / `router_confidence` / `cs_intent` / `task_*`（admission/terminal/lease/fenced_write/recovery/authorization_denied）/ `context_*` 九族 / `chat_request_total{status}` / `llm_*`。建议告警（有指标支撑即可配）：route fallback 激增（router_layer_total{layer=llm} 突涨）、domain error 率（chat_request_total{status=error}）、task recovery 失败（task_recovery_total{result=fail}）、fenced write 出现（task_fenced_write_total>0 即异常）。CrossTenantViolation 无可靠 metric——走审计/演练（E3 脚本可入定时回归），不造假指标。

## F7 Baseline 清单

Domain 数量 **6**（3 域图 + SQL/RAG 主图技能域 + General）｜Router：prefilter 三层序 + 三层主 Router（rule→vector→LLM）+ hierarchical 粗分类（unknown→clarify）｜共享 State：F2｜Checkpoint：F3 四 thread 构成 + 7d TTL 单例守护｜Memory 边界：F3.4（CS 轮豁免主链）｜Permission 边界：sql.read 六层 / CS 归属+确认 CAS / RAG KB+文档级 / 任务执行时授权 fail-closed / 写工具审批门 / 运营端 resolve_operator_role｜Async 边界：域图同步，异步只经冻结 Task Runtime（消息只带 task_id）｜Side Effect：18 项台账全带幂等保护｜SSE contract：meta→status/log/delta/…→done|error（meta=node_labels+request_id；done 带 trace_id/context_usage/pending_action）｜Trace contract：trace_id 贯穿 HTTP→Graph→Domain→Skill/LLM→Task（task trace session_id=thread_id）。

## F8 Freeze 条件核验

跨域 routing 正确（C 10/10）✓ ｜ 跨域 state 不污染（B 37 用例 + FakeGraph 实参捕获）✓ ｜ session 连续（C9 落库核查）✓ ｜ tenant 隔离（E3 实机）✓ ｜ permission 连续（D-R2 + E3-404）✓ ｜ async identity 连续（D1/D2）✓ ｜ recovery domain 不丢（D3/D7）✓ ｜ side effect 可收敛（D5 + 台账）✓ ｜ SSE 生命周期正确（done 恰一次/断连收尾）✓ ｜ trace 闭环（五层贯穿）✓ ｜ 故障安全（E 7/7）✓

```text
DOMAIN_RUNTIME_PRODUCTION_CLOSURE_PASS = true
DOMAIN_RUNTIME_CORE_FROZEN             = true
```
