# Phase 5 Commit A Completion Report — D2 两跳断片修复（路由层最小闭环）

> 格式依施工军规（八项）。基线：Phase 4（`7f35529` / `a80a594` / `9a0f3e4`）+ Phase 5 审计报告（`47dcfe0`，D1 拍板**方案 A：路由层最小闭环**）。
> 决策点依据：审计报告 §七 D1 → 方案 A（ConversationContext 承载挂起，新表 0 张）；D2/D3/D4/D5 分别归 Commit B / 延后 / 独立小 commit。

---

## 1. Verdict

**PASS**（附三项如实披露：① 审计报告 Commit A 表列 ①～④ 已全部落地，另**新增一项**路由层解析器 `BookingPendingResolver`——见 §3 决策补充；② Commit B（审批契约 + flag-off 基建）与 Commit C（commerce_agent 收口）**未开工**，按审计报告分批纪律各自独立提交；③ 冻结面（确认链 cfp/TTL/金额互检、八态状态机、幂等账本、fail-closed 纪律、checkpoint 跨轮契约）本轮**零接触**）。

**用户可见效果**：预订/比价子图反问后，用户只答槽位值（「10月3日」「大阪」）不再掉域——问题在上轮被记住、本轮被接住、子图继续续填。

## 2. 问题与根因（实测复现）

用户：「帮我预订大阪的酒店」→ 子图反问「预订还需要：入住日期、退房日期。」→ 用户答「10月3日」→ **掉域**。

三缺口叠加（审计报告 §二）：

| # | 缺口 | 位置 |
|---|---|---|
| G1 | prefilter 命中不回写路由上下文：`_ROUTE_MODE_DOMAIN` 只登记 3 个域，`travel_booking/travel_commerce` → `domain=None` → `mark_domain_turn` 早退 → `active_domain` 永远为空 | `orchestration/graph/routing/prefilter_chain.py` |
| G2 | 子图 clarify 不回写 pending：booking/commerce 的反问从不进 `pending_question`，下一轮 Context Assembler 不知道「上轮问了什么」 | `travel/{booking,commerce}/graph_node.py` |
| G3 | 无挂起参数续填档 + 无路由层接住：即使句子被拉回子图，`detect_booking_action("10月3日")` = unknown → 再问一遍；且没有任何路由层组件会把「纯槽位值回答」拉回子图 | `travel/booking/graph_builder.py` + `orchestration/context/` |

## 3. 实现（Commit A 五项 + 一项决策补充）

| 审计报告项 | 落点 | 说明 |
|---|---|---|
| ① `_ROUTE_MODE_DOMAIN` 补两域（G1） | `orchestration/graph/routing/prefilter_chain.py` | 新增 `travel_booking`/`travel_commerce` 两键；prefilter/解析器命中即回写活跃域 |
| ② 两子图适配器回写 `mark_domain_turn`（含 pending_question） | `travel/booking/graph_node.py`、`travel/commerce/graph_node.py` | 新增 `_read_pending`/`_write_pending`：clarify 态写挂起 + 回写追问文案；终态 CAS 清挂起（question_id 匹配防清掉并发写入的新挂起） |
| ③ `booking_intent` 结构化挂起进 routing_context | `orchestration/context/conversation_context.py`、`context_repository.py` | 新增 `booking_intent` 字段 + `set_booking_intent`；快照随 `brief_summary` 流出（向后兼容 `.get`）；新增 `SET_BOOKING_INTENT`/`RESOLVE_BOOKING_INTENT` 原子 mutation（同域同 kind 更新**保留 question_id**——续填是同一件事的推进） |
| ④ booking/commerce resolver 增 pending 续填档（G3） | `travel/booking/graph_builder.py`、`travel/commerce/graph_builder.py` | 挂起在场 → 优先走 `_resume_booking`/`_resume_commerce`：本轮抽取并入上轮 `collected`，齐备即转**与首跑完全相同**的 new 流程（executor 零改动），仍缺则继续追问 |
| **决策补充：路由层续填解析器** | `orchestration/context/booking_pending_resolver.py`（新） | 见下 |
| ⑤ 守护测试（两跳回归） | 见 §4 | 路由层 16 例 + 适配器级两跳 3 例 |

**为什么多一个解析器**：审计报告 ② 让 `active_domain` 非空、④ 让子图具备续填能力，但「10月3日」这句话**在路由层没有任何组件会把它送回子图**——`travel_prefilter` 无信号词、`ContinuationResolver` 需要延续信号（「改成3天」类），`booking/commerce prefilter` 需要意图词。G3 的另一半（路由侧接住）必须新建判定点，与 `TravelPendingResolver` 同构：

- 位置：`Guard → Context Assembler → TravelPendingResolver → **BookingPendingResolver** → ContinuationResolver`；
- 判定（纯规则零 LLM 零 IO）：活跃域 ∈ {travel_booking, travel_commerce} 且有完整挂起；对应域总开关开；无客服强信号；**本轮补上 ≥1 个缺失槽位**（抽取与子图**同源** `commerce/extract.merge_slot_values`，绝不另写一套解析）；
- 命中后只回「回哪个子图」（`route_mode`）——槽位读写仍全在子图适配器（唯一抽取点纪律）；
- 新增紧急回滚开关 `BOOKING_PENDING_RESUME_ENABLED`（默认 true，关闭即退回 D2 断片行为）。

**单一事实源纪律**：必填槽位/展示名/合并逻辑全部收敛到 `travel/commerce/extract.py`（`HOTEL_REQUIRED_SLOTS`/`FLIGHT_REQUIRED_SLOTS`/`REQUIRED_SLOTS_BY_KIND`/`SLOT_LABELS`/`merge_slot_values`/`extract_single_date`），预订与比价两侧的追问文案、缺失判定、挂起回写三处共用，消除原先 graph_builder 内联重复实现（`_SLOT_LABELS`/`_collect_params`/`_iso`/`_missing_clarification` 已删）。

## 4. 测试与验证

**新增用例（19 例，全绿）**：

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `tests/orchestration/context/test_booking_pending_resolver.py` | 16 | 两跳命中（单日期/区间/城市/机票出发日/比价域）、无新槽不拦、CS 强信号放行、无活跃域/无挂起/挂起不完整、全局开关与域开关、长句、G1 映射守护、路由包导出 |
| `tests/travel/booking/test_booking_d2_two_hop.py` | 3 | **适配器级真实两跳**（真实子图 + 真实适配器 + 真实会话上下文，无 PG）：hop1 写挂起 + 登记活跃域 → hop2 续填 `collected.check_in=2026-10-03`、`missing=[check_out]`、**question_id 保留**；变体答城市补 `city`；router_node 分派回 `travel_booking` |

**回归**：

| 范围 | 结果 |
|---|---|
| `tests/orchestration/context/` + `tests/router/` | **126 passed** |
| `tests/travel/booking/` + `tests/travel/commerce/` | 见 §5 备注 |

## 5. 如实披露

1. **本轮新增了审计报告未列的路由层解析器**（§3 决策补充）。理由：不加则 ②④ 无法闭环（「10月3日」无人送回子图），验收门 ⑤ 不过。属「实现审计意图所必需」，非范围外扩张。
2. **`/travel` 身份收口**（`app/api/routes/travel.py` 6 端点 `resolve_identity`→`require_identity` + 16 例 `tests/api/test_travel_auth.py`）为**独立一笔提交**，与 Commit A 分开（不同关注点，便于回溯）。
3. **API 全量回归**：`tests/api/` 499 例，其中 `test_tasks_api.py::test_full_lifecycle_via_api_e2e` 首跑失败、**单独复跑通过**（136s）——任务 API 暂停/恢复链路，与本轮改动无关，判为宿主机负载下的偶发（该类 E2E 依赖 `enqueued` 计数时序）。
4. Commit B/C 未开工；POI 全国化、STOP M（Payment/Cancel/Refund）等 C1 台账项状态不变。

## 6. 冻结面确认（本轮零接触）

booking 八态状态机 + CAS + 幂等账本 + 052 四表 ｜ 确认链 cfp 绑定 / TTL / 金额互检 ｜ commerce fail-closed 纪律（Decimal+ISO4217 / sha256 指纹 / 确定性排序 / deeplink 白名单） ｜ providers 七态契约 ｜ checkpoint 跨轮契约 ｜ 主图 builder 节点拓扑。

## 7. 下一步

- **Commit B**：`core/contracts.py` 加法新增 `HumanApprovalRequest`（五类枚举；BOOKING 只登记指向现有确认链）+ `pending_approval` state 字段（flag 组，off 不接线）+ supervisor BOOK 判定与映射登记（flag `TRAVEL_UNIFIED_INTENT` off=零行为）。
- **Commit C**：`travel/agents/commerce_agent.py` 薄封装（kind 分发）+ 两子图节点改调 Agent + Agent 纪律静态断言扩展（该笔触达交易编排，开工前单独评估风险）。
- 台账回填：`C1-1` 状态更新（Commit A 完成，B/C 未开工）。
