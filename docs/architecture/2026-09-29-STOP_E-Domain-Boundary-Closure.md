# Architecture Simplification — STOP E：Domain 语义边界收口（Domain Boundary Closure）

日期：2026-09-29
基线：`main@3cd0c3c`（pre-STOP-E 审计 `2026-09-29-STOP_E_Preparation_Audit.md` 之后的实施记录）
性质：**架构语义治理，不是代码重构**。运行时保持物理隔离，零行为变更。

最终判定：

```text
DOMAIN_BOUNDARY_PASS=true
STOP_E_ROUTE_COMPATIBILITY_PASS=true
DOMAIN_SEMANTIC_PASS=true
PHYSICAL_MERGE=false
ROUTE_MODE_CHANGED=false
STOP_E_PASS=true
```

---

## 1. 原问题

代码与文档把 Domain 表达为 **5 个平级域**（`customer_service` / `travel` / `travel_commerce` / `travel_booking` / `selection_funnel`），开发者会误以为 `travel_commerce`、`travel_booking` 是独立业务域。而决策层的真实语义早已收敛（STOP B 起 `DomainRouter._PREFILTER_DOMAIN_MAP` 把两者归一为 `domain=travel + subflow=commerce/booking`），两层口径的关系没有在文档与管理端被表达——这是「决策语义」与「架构叙事」的脱节。

## 2. 为什么不物理合并（原方案否决理由）

1. **`route_mode` 改名会静默错路由**：`route_selector`（`router_node.py:858-881`）按 `state["route_mode"]` 查 `domain_graph_registry.get(mode)`，未命中**不报错而是落入 planner 兜底**——改名不是响亮失败，是静默故障。
2. **物理名被五层契约绑定**：LangGraph node id（`builder.py` 自动布线）、registry 注册键、prefilter 输出、`/api/health` 组件键、evaluation 模块 ID（`travel-commerce`/`travel-booking`，与 `data/eval_runs/` 历史可比性绑定）。
3. **独立生命周期是刻意设计**：commerce/booking 无 checkpointer（K0 决策：单发查询，订单事实在 PG），booking 崩溃恢复走 PG + celery，与 travel 图的 `travel:{conv}` checkpoint 前缀互不相干。
4. **语义合并已存在**：决策投影在 `DomainRouter`（STOP B），trace metadata 已持久化 domain/subflow。物理合并只会破坏第 1-3 条，不带来任何新语义。

## 3. 实施内容（全部改动面）

### E1 文档收口（必做，已完成）

| 文件 | 改动 |
|---|---|
| `README.md` | 6 处：架构总述、图 1/图 3 mermaid、术语表、系统规模表改为「3 个顶级业务域 / 5 个物理域图」口径；评测表加注 `travel-commerce`/`travel-booking` 是评测模块 ID 非独立域 |
| `docs/architecture/ai-runtime.md` | 「垂直域图」节：语义口径段 + 域图表行标注子流归属 + **内部调度标识警示**（route_mode 永久保留、查表 miss 静默兜底、语义投影与守护测试指针） |
| `AGENTS.md` | 规模口径行修正：`2 域图`（2026-09-16 过时计数）→ `5 物理域图＝3 顶级业务域（travel 含 planning/commerce/booking 子流）` |

> provenance：`ai-runtime.md` 的本节改动在本次提交前被并行会话的 docs 提交 `5d1b1e9`（15:35，docs 入口文档刷新）按工作区现状一并带入入库——内容即本节所述口径，无重复提交必要；特此登记以免溯源困惑。

### E3 Registry 语义元数据（可选档，已实施）

| 文件 | 改动 |
|---|---|
| `backend/orchestration/domain_graph.py` | `DomainGraph` 增加 `subflow: str \| None = None`（frozen dataclass 加默认字段，向后兼容）；纯展示/对账语义，**不参与 route_selector 查表**（查表键仍是 `name`） |
| `backend/travel/commerce/register.py` | 注册元数据 `subflow="commerce"`（注册键/节点名/adapter 零改动） |
| `backend/travel/booking/register.py` | 注册元数据 `subflow="booking"`（同上） |
| `backend/tests/orchestration/test_domain_semantic_consistency.py` | **新增守护测试**（4 例）：①每个注册键都能被 DomainRouter 归一；②顶级域图归一到自身、不带 subflow；③子流域图 registry.subflow == Router 归一 subflow 且顶级域真实注册；④`DomainRouter._from_prefilter` 端到端输出与元数据一致（`travel_commerce → domain=travel, subflow=commerce`；`travel_booking → domain=travel, subflow=booking`） |

### E2 管理端展示（可选档，已实施，纯前端）

| 文件 | 改动 |
|---|---|
| `frontend-admin/src/app/agents/page.tsx` | `ROUTE_MODE_DOMAIN_META` 展示映射：`travel_commerce` → 「Travel Domain · commerce 子流」、`travel_booking` → 「Travel Domain · booking 子流」；`route_mode` 原样透传，**后端 `/api/agents` 字段与值零改动** |

## 4. 测试结果（2026-09-29 实跑）

| 门 | 范围 | 结果 |
|---|---|---|
| 契约四门 | `test_registry_consistency` + `test_layer_consistency` + `test_adr0001_dual_registry_merge` + `skills/test_base_output_contract` | **45 passed** |
| Router / Domain | `tests/orchestration/router` + `graph/test_router_prefilter_order` + `tests/router/test_cross_domain_continuity` + 新语义测试 + `test_domain_registry` | **134 passed** |
| route 兼容（commerce） | `tests/travel/commerce/test_commerce_routing.py`（含 `route_mode="travel_commerce"` 硬断言） | **26 passed** |
| route 兼容（booking） | `tests/travel/booking/test_booking_routing.py`（含 `route_mode="travel_booking"` 硬断言；PGPORT=5433） | **9 passed** |
| evaluation | `tests/evaluation/` 全目录（skip 均为无云 key 的环境门，历史口径） | **163 passed, 112 skipped** |
| frontend-admin | `npx tsc --noEmit` ＋ `npm test`（vitest） | **tsc 0 错；376/376 passed** |
| 红线核验 | `git diff` 对 `router_node.py` / `commerce_prefilter.py` / `booking_prefilter.py` / `domain_router.py` | **零 diff**（route_mode 字符串、prefilter 顺序、registry 键全未动） |

> 执行口径备注：宿主机 `tests/orchestration` 全目录 + travel 混跑会触发已知挂死病态（详见记忆 `agent-host-pytest-instability`，PG 连接对 psycopg3 无超时），故按既定甄别法改为**定向分块 + GNU timeout 硬超时**执行；分块后纯路由块 27s、commerce 6s 收敛，证明卡点在 DB 依赖测试而非本次改动。

## 5. 保留项（物理层，永久不动）

| 保留项 | 证据/原因 |
|---|---|
| `route_mode` 取值：`travel_commerce` / `travel_booking` 及其余 | prefilter → route_selector → registry 查表键，改名 = 静默 planner 兜底 |
| `DomainGraph.name` 注册键（5 个）与 `node_name`（5 个 `*_graph_node`） | registry 查表 + builder 自动布线 |
| 代码包布局：`travel/`、`travel/commerce/`、`travel/booking/` | 独立生命周期（commerce/booking 无 checkpointer，booking 走 PG+celery 恢复） |
| checkpoint / SSE / state / evaluation 契约 | travel 图 `travel:{tenant:user}:{conv}` 前缀；评测模块 ID 与历史 run 可比性绑定 |
| `/api/agents` 字段结构与值 | 运行时契约；admin 归属说明只做在前端展示层 |
| 三个 prefilter 的优先级与开关语义 | `test_router_prefilter_order.py` 19 例守护（本次 134 例中含） |

## 6. Deferred / 知情取舍

1. **admin 后端不加 `subflow` 字段**：`/api/agents` 保持零契约变化，归属语义由前端展示映射承担。代价：前端 `ROUTE_MODE_DOMAIN_META` 与后端 `DomainGraph.subflow` 是物理两份同源语义——漂移风险由注释指源 + `test_domain_semantic_consistency.py` 锁后端两层兜住；若未来加第四个子流域图，需同步改前端映射（在新增域图手册已有 prefilter 接线清单，此处不再加机制）。
2. **travel 图本体的 `planning` 子流标签不进 registry**：registry 只对「独立生命周期的子流」声明 subflow（commerce/booking）；travel 图即 Travel Domain 本体，其 `planning` 标签只存在于 Router 归一与 trace metadata，文档叙事已表达，不新增第三处存储。
3. **`general_chat → ("general", None)`**：Router 决策枚举项，无对应域图（寒暄直答走主图 general_chat 节点），不属 Domain Registry 表达范围，维持现状。

## 7. 结论

STOP E 完成「**语义合并、物理隔离**」的最后一公里：决策层（STOP B 已有）→ 注册表层（`subflow` 元数据 + 语义一致性守护）→ 展示层（管理端归属标注）→ 文档叙事层（3 顶级域口径统一），四层表达一致；调度标识层（route_mode/注册键/节点名）原样冻结并被测试与红线核验双重确认。开发者现在看到的架构是 **Customer Service / Travel（planning·commerce·booking）/ Selection 三个顶级业务域**，运行时仍是 5 个物理域图，无任何行为变更。
