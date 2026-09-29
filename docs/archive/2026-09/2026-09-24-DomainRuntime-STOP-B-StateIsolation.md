# Domain Runtime STOP B — Domain Contract & State Isolation（状态隔离收口报告）

> 日期：2026-09-24 ｜ 前置：STOP A PASS（6628fa8）
> 原则：不重写 SubGraph、不为结构漂亮重构——现有结构已能隔离的只补缺口
> 生产改动合计：**1 个 schema 字段补登记**（state.py `cs_pending_action`），其余全部为测试与文档

---

## 1. Verdict

```text
STOP_B_PASS      = true
STOP_C_ALLOWED   = true
```

---

## 2. Shared Runtime Contract（B1）

以现有代码为准（不换 State 类），最小共享上下文已在 `AgentState`（orchestration/state.py:65）固化：

| 字段 | 类型/形态 | 必填性 | 说明 |
|---|---|---|---|
| request_context | RequestContext / checkpoint_safe dict | checkpointer 开启时必填 | session_id/user_id/tenant_id/roles/data_scope/permissions/department/subject_type/idempotency_key/kb_id/model/deadline；trace/stream_sink 不入 checkpoint |
| user_id / tenant_id / session_id / department | 平铺键 | 每轮由 make_initial_state 写入 | 未登记即被 LangGraph 剥离（历史坑已全部闭环） |
| routing_context | dict（active_domain/last_intent/last_action/brief_summary/pending_question） | runner 图外组装 | 域粘滞唯一合法载体 |
| route_mode / route_decision | str / dict | router 必写 | 域分派唯一依据（route_selector） |
| messages / question / final_answer / step_results | 主链业务 | — | canonical chat_messages 只由 memory.end_turn 落库，域图禁改 |

**新域接入契约**（F3/F4 的先应用，B11 冻结）：共享键只允许上表；域私有状态必须收在自己 TypedDict + 自己的 `*_context` 出口字段内；`sql_context`/`selection_context` 之类平铺命名禁止出现（回归测试锁定）。

## 3. Domain Private State（B2）

| 域 | 私有 State | 出口字段 | 输入契约 |
|---|---|---|---|
| CS | CSGraphState（graph_state.py:76-112） | cs_context / cs_action_result / cs_audit_entries / **cs_pending_action（本轮补登记）** | new_cs_graph_input（显式白名单 + 执行态默认值，state_loader 从 DB 重载业务态） |
| Travel | TravelGraphState（graph_state.py:46-124） | travel_context / final_answer | new_travel_graph_input（5 键白名单，**禁预置产物默认值**） |
| Selection | SelectionFunnelState（graph_state.py:41-73） | funnel_context / final_answer / status | new_selection_funnel_graph_input（6 键白名单） |
| SQL | 无域 TypedDict（SQLResult/BusinessInsight 数据协议） | step_results | state 平铺 question + plan 产物，无域上下文通道 |
| RAG | 无（AskOutcome 不进图 state） | step_results.output | question/kb_id |

本轮补缺口：`cs_pending_action` 登记进 OrchestratorState（state.py）。此前 runner 从节点原始输出读取所以功能未坏，但它是「updates 流剥离 schema 外键」坑的活证据——任何改为从 state 读的消费方都会静默拿到 None。

## 4. Domain Ownership（B3）

完整 Ownership Matrix 见 STOP A 报告 §5（writer/reader/lifetime/cross-domain allowed 逐字段）。冻结口径：

- `cs_context`（含 order_id）——owner CS，轮级，每轮由 prefilter 重建；唯一读取点 CS 入口（cs_prefilter / CS 图 state_loader）；Redis 订单承接 TTL 7200s，无显式清域（读取点封闭，P1-8 登记观察）。
- `travel_context`——owner Travel，轮级覆盖 + ConversationContext 会话级摘要；结构化 pending（travel_pending）受 active_domain 门控。
- `funnel_context`——owner Selection，轮级 + 会话级候选摘要；唯选品系内部 direct_executor 读 top 注入 workflow（同域允许）。
- `routing_context.active_domain`——Runtime 所有，唯一跨域共享路由状态。

## 5. Domain Switch / Return / Pending 语义（B4/B5/B6）

- **切域（B4）**：CS active（含 pending 确认）不粘滞——他域强信号请求正常切域；router 在 travel 轮新增的更新不含任何 CS 键；CS pending/订单不进 travel 域图输入（FakeGraph 捕获实调入参证明）。Travel 可用共享历史语义（routing_context 摘要 + FollowUpResolver），但 CS pending 不是它的 active state。
- **回域（B5）**：CS 业务状态权威在 DB（confirmations/handoffs/tickets，键 (user_id, session_id)），每轮 cs_state_loader 重载并显式重置执行态——CS→Travel→CS 后原 pending 按业务状态机原样恢复（含过期判定 expired），不存在「最后一次 state 覆盖全部」。travel 回归走 checkpoint（travel:{conv}）或 ConversationContext 摘要重建（graceful reconstruction）。
- **Pending 分层（B6）**：
  - 强绑定 = CS 确认（只有 pending/pending_confirmation 两态进确认流；need_info 型不吃确认词；终态五值 expired/duplicate/success/failed/cancelled 固定映射）；
  - 可中断 = travel pending（active_domain != travel 永不拦；CS 强信号在场立即放行；总闸 TRAVEL_PENDING_RESUME_ENABLED 关闭即失效；>40 字/未对上槽位交回正常路由不猜）；
  - 弱绑定 = continuation 信号词回 active_domain（≤40 字、无外域强信号才生效）。
  - 未造通用状态机：三层语义全部复用各域既有状态机（confirmation_flow / TravelPendingResolver / ContinuationResolver）。

## 6. Context Pin（B7）

现状即符合「只 pin 当前请求执行必需」：CS 图入口 `_register_request_pins`（graph_builder.py:80-121）把 pending_action.target_id 与 last_order_id 注册为请求级 ContextVar pin（PIN_CONFIRMATION / PIN_ENTITY），随请求销毁、不跨轮累积；消费方为冻结层 Context Budget（L5 摘要保真 + LLM 裁剪豁免）。未 pin 整个域状态——KEEP。

## 7. Checkpoint Isolation（B9）

thread_id 构成（共用 agent_memory 三表，namespace 靠前缀）：

| 图 | thread_id | 备注 |
|---|---|---|
| 主图 chat | `agent-{session}-{ms}-{uuid8}` | 每轮唯一，不做跨轮合并 |
| 主图任务 | `task-{task_id}` | 任务 resume 唯一路径，域图不在其上 |
| CS | 裸 `conversation_id` | 空会话不设 thread |
| Travel | `travel:{conversation_id}` / `travel-{uuid}` | 前缀=namespace 隔离 |

测试锁定：同会话 CS/travel thread 必不相等；travel 空会话 fallback 逐次唯一；`travel:task-x` ≠ `task-x`（resume 不落域图 thread）；四个构造根两两不同。B9「resume SQL 恢复到 CS 节点」结构性不存在（任务执行器固定主图 + rag_index 守卫拒绝进 agent 图）。

## 8. 新增测试（B8/B10）

`backend/tests/domain_runtime/`（5 文件，37 用例，全绿）：

| 文件 | 锁定 |
|---|---|
| test_domain_state_isolation.py | schema 登记完整（含本轮 cs_pending_action）；sql_context/selection_context 禁现；三域 TypedDict 互不含他域命名空间字段；四个输出契约喂污染数据仍干净 |
| test_domain_switching.py | CS pending 不粘滞 travel 强信号；切域轮新增键零 CS；travel 活跃任务 new_run 续航（pending_resume 通道） |
| test_domain_checkpoint_namespace.py | 四 thread 构成契约 + 跨域不共享不变量 |
| test_domain_pending_semantics.py | CS 强绑定（两态/need_info 不吃确认/终态五值）；travel 可中断（active_domain 门/CS 强信号放行/总闸/超长/未对上槽位六不拦） |
| test_cross_domain_context_leak.py | FakeGraph 捕获真实适配器入参：CS 订单号/待确认动作不进 travel 输入；travel itinerary/目的地不进 CS 输入；general_chat 与 sql_skill 模块源无域上下文引用（tripwire）；funnel/travel 输入白名单 |

只 mock 外部边界（域图本体、ConversationContext 仓库、CS 检测器缓存）；router_node 裸调复用既有 harness。

## 9. 回归

```text
tests/orchestration + tests/customer_service + tests/sql + tests/travel
+ tests/selection_decision + tests/selection_funnel + tests/context_budget
+ tests/domain_runtime
→ 2681 passed / 3 failed / 1 skipped（497s，--no-cov 局部口径）
```

存量红处理（§9 对照结论，均与本轮 diff 无关，不为全绿改无关代码）：

| 失败 | clean HEAD 对照 | 判定 |
|---|---|---|
| workflow/test_persistence.py::test_list_returns_descending_order | clean HEAD 同样 FAILED | BASELINE_RED（存量） |
| workflow/test_market_research_smoke.py::test_llm_failure_degrades_to_skeleton | clean HEAD 同样 ERROR | BASELINE_RED（存量，环境依赖冒烟） |
| selection_funnel/test_stages.py::test_verify_watchlist_candidate_still_uses_competitor_history | clean HEAD 与主树**单跑均 PASSED**，仅全量套件顺序下失败 | 存量顺序性 flake，登记在案 |

domain_runtime 5 文件 37 用例全绿；registry/layer/adr0001 门禁全绿（STOP A 阶段验证）。

## 10. 遗留与移交 STOP C

- P1-5（session_id body 可控为 CS/travel checkpoint 根）：执行态污染面被 state_loader 重置兜住，业务态有 DB 权威——接受为已知边界，F3 冻结记录；多租户化前必须加 user 维度。
- P1-8（CS 订单承接 TTL 2h 无显式清域）：读取点封闭，维持 TTL 语义，观察。
- P1-1（选品共享数据集无属主列）：域接入规范第一个样例，F3 声明「角色内共享」语义。
- 其余 P1/P2 台账以 STOP A 报告为准，本轮无新增 P0/P1。

```text
STOP_B_PASS      = true
STOP_C_ALLOWED   = true
```
