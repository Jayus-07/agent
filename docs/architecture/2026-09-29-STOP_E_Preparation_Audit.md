# Architecture Simplification — Pre STOP E Design Reconciliation（STOP E 准备审计）

日期：2026-09-29
基线：`main@a0be26c`（STOP B=`984a9ba`、STOP C=`39b3c8c` 已冻结）
性质：**只读审计**。本文所有结论以代码为准，未修改任何生产代码。
结论先行：`STOP_E_READY=true`，`NEXT_ACTION=IMPLEMENT_STOP_E`（按第二部分重校准后的范围）。

---

## 第一部分：当前 Domain 真实状态

### 1.1 Domain Registry —— 五个注册全部仍在

注册表：`backend/orchestration/domain_registry.py:12-34`。纯 `name → DomainGraph` 字典，
`get(route_mode)`（:24）直接以注册名查表。注册方式 = 各域模块 import 时自注册（G3）。

| 注册名（=route_mode 值） | node_name | label | 注册位置 |
|---|---|---|---|
| `customer_service` | `cs_graph_node` | 客服图执行 | `customer_service/register.py:11-13` |
| `travel` | `travel_graph_node` | 旅游规划图执行 | `travel/register.py:20-22` |
| `travel_commerce` | `travel_commerce_graph_node` | 旅游商务查询（酒店/机票） | `travel/commerce/register.py:13-15` |
| `travel_booking` | `travel_booking_graph_node` | 旅游预订（Booking Transaction） | `travel/booking/register.py:11-13` |
| `selection_funnel` | `selection_funnel_graph_node` | 智能选品漏斗执行 | `selection_funnel/register.py:20-22` |

`DomainGraph` 描述符（`orchestration/domain_graph.py:14-27`）为 frozen dataclass，
字段只有 `name / node_name / label / adapter`——**Registry 当前没有 subflow 概念**。

消费方（全部以 `name` 字符串为键）：

| 消费方 | 位置 | 消费方式 |
|---|---|---|
| builder 自动布线 | `orchestration/graph/builder.py:147-152`（add_node）、`:184-185`（edge_map）、`:192-194`（→END） | `domain.node_name` 注册 LangGraph 节点 |
| route_selector 分派 | `orchestration/graph/router_node.py:858-881` | `domain_graph_registry.get(mode)` → 返回 `node_name` |
| 管理端对账 API | `app/api/routes/agents.py:64-71` | `route_mode: domain.name` + `label` 原样输出 |
| 预览图构建 | `orchestration/graph/runner.py:1117-1118` | 按名查表 |

### 1.2 Domain Router —— 语义收敛**已经存在**（以代码为准）

`orchestration/router/domain_router.py:19-26`：

```python
_PREFILTER_DOMAIN_MAP = {
    "customer_service": ("customer_service", None),
    "travel":            ("travel", "planning"),
    "selection_funnel":  ("selection_funnel", "funnel"),
    "travel_booking":    ("travel", "booking"),
    "travel_commerce":   ("travel", "commerce"),
    "general_chat":      ("general", None),
}
```

DomainDecision 输出形态（`:130-137`）：**顶级域只输出 `travel`，commerce/booking 已经是 `subflow`**——
即生产 Router 决策层当前输出的就是目标形态，不是 `domain=travel_commerce`：
`{"domain": "travel", "subflow": "commerce", "confidence": 1.0, "source": "prefilter", "reasoning": "..."}`
（`DomainDecision` 定义见 `orchestration/router/models.py`）。`capability_router.py:13-16`
的 `_DOMAIN_ALIASES` 同向归一。STOP B（`984a9ba`）后该决策已随
`TraceRecord.metadata["router"]`（domain/subflow/capability/mode/confidence/source）持久化。

### 1.3 route_mode 兼容层 —— `travel_commerce` / `travel_booking` 仍存在，定性为**内部调度标识**

- prefilter 仍产出旧值：`orchestration/graph/commerce_prefilter.py:52`（`route_mode: "travel_commerce"`）、
  `booking_prefilter.py:48`（`"travel_booking"`）。
- `route_selector`（`router_node.py:858-881`）按 `state["route_mode"]` 分派：
  `direct` / `workflow` / `clarify` / `general_chat` 内置分支之外，
  `domain_graph_registry.get(mode)` 命中则返回域图节点，**未命中静默落入 `planner` 兜底**——
  改这两个字符串不会响亮报错，而是静默错路由，这是最大的一条红线。
- 判定：这两个名字在架构语义上**已经是** Travel Domain 的 subflow（1.2），它们以
  `route_mode` 形态存在的唯一身份是 **prefilter → route_selector → registry 查表的内部
  调度标识（兼容层）**，不是架构层 Domain。这与原计划「语义合并、物理隔离」的要求同构。

---

## 第二部分：STOP E 范围重校准（原计划 vs 调整后）

| | 原计划 | 重校准后 |
|---|---|---|
| 目标 | 五个平级域收敛为 3 顶级域（Travel 含 planning/commerce/booking） | 不变 |
| 语义层 | 未指明改哪层 | **不新增第二事实源**：语义投影已在 `DomainRouter`（1.2）与 trace metadata 存在，STOP E 只补「展示与文档叙事」+ 可选的 Registry 展示 metadata |
| Registry | 「Domain Registry 收敛」——有被误读为改注册键的风险 | **注册键（`DomainGraph.name`）、`node_name`、prefilter 的 `route_mode` 字符串一律不改**；subflow 仅作为**附加展示字段/文档语义**存在 |
| 代码包 | 不物理移动 | 不变（`travel/`、`travel/commerce/`、`travel/booking/` 保留） |
| 管理端 | 「Admin 展示层归属」 | `/api/agents` 的 `route_mode` 字段值不改（契约）；仅前端 label 渲染可加「旅游域 · 子流」说明（可选） |
| 文档 | README / Registry / 文档收敛 | README 4 处 + `ai-runtime.md` 2 处（见第四部分清单） |
| 判定依据 | — | 审计证据：state/checkpoint、管理端、评测三层均已证实对语义收敛安全（第三、五部分） |

一句话：**STOP E 从「合并 Domain」重校准为「补齐展示与文档层的 Travel Domain 叙事」；
决策语义层（Router/trace）已由 STOP B 提前完成，调度标识层（route_mode/注册键）永久保留。**

---

## 第三部分：影响范围与禁改清单

禁改（全部经代码证实为运行时契约，无 一处允许本次触碰）：

| 项 | 证据 |
|---|---|
| LangGraph node id（含五个 `*_graph_node`） | `builder.py:147-152` 自动布线 + 域图 `register.py` |
| checkpoint / SSE / task resume | travel 图 checkpoint 前缀 `travel[:tenant:user]:{conv}`（`travel_graph_node.py:398-430`）；commerce/booking 无 checkpointer（`travel/commerce/graph_builder.py:169`、`travel/booking/graph_builder.py:182`） |
| `route_mode` 取值（`travel_commerce`/`travel_booking`/其余） | prefilter 输出（`commerce_prefilter.py:52`、`booking_prefilter.py:48`）→ `route_selector` 分派 → `registry.get`，且有 4 处测试硬断言（`test_router_consolidation_adapters.py:53-54,191`、`test_commerce_routing.py:92`、`test_booking_routing.py:49`） |
| domain graph state 键 | `OrchestratorState`（`orchestration/state.py`）无任何 `travel_commerce_*`/`travel_booking_*` 键；三子图 state 互不共享、经适配器与主图只交换 `final_answer`（+travel 的 `travel_context`） |
| 数据库 schema / migration | `sql/migrations/052_travel_booking.sql` 等不动 |
| 前端 API 契约 | `domain_hint="customer_service"`（`frontend/src/hooks/useCSChat.ts:46`）、`/api/agents` 字段结构（`agents.py:64-71`）、`/api/health` 组件键 `"travel_commerce"`（`health.py:56`，前端不消费但保持稳定） |
| evaluation 契约 | 评测模块 ID `travel-commerce`/`travel-booking`（连字符，`evaluation/models.py:10`）是 runner 模块命名空间，与 `data/eval_runs/` 历史可比性绑定，不改 |

需要触碰的层及原因（STOP E 的全部合法改动面）：

1. **Registry 展示 metadata（可选、纯附加）**：`DomainGraph` 增加带默认值的新字段（如
   `subflow: str | None = None`）→ frozen dataclass 加默认字段向后兼容，五个 register 不改键，
   仅 commerce/booking 两个 register 各加一行元数据。原因：不加字段，Registry 在机器层面
   永远无法表达「travel 的子流」，语义只能存在于文档。**若接受纯文档叙事可跳过此项。**
2. **管理端展示（可选）**：admin agents 页对 `route_mode` 文本的渲染加显示层映射（原样
   透传值不变）。前端已证实无枚举校验（第五部分风险 3），不会报错。
3. **架构文档 / README（必做）**：见第四部分。

---

## 第四部分：STOP E 实施计划（只出计划，不执行）

**STOP E Goal**：架构叙事收敛为 3 个顶级业务域（`customer_service` / `travel`（subflow:
planning|commerce|booking）/ `selection_funnel`），Registry 展示 metadata、管理端展示与
文档同步；代码包、图节点、route_mode、state、API 值全部不动（语义合并、物理隔离）。

**Files likely affected**（按优先级，均为最小改动）：

1. `docs/architecture/ai-runtime.md:41,44-49` —— 「五个域图」表述与表格行归类
   （commerce/booking 两行归入 Travel 子流，保留开关列与物理路径列）；
2. `README.md:17,37,125,175` —— 四处「5 个域图」并列表述改为 3 顶级域 + Travel 子流口径
   （`README.md:531` 目录树已是子域口径无需改；`:306-307` 评测表模块名**保留**，可加注
   「评测模块名，非独立域图」）；
3. `AGENTS.md:67` —— 域图数量口径可注「5 个物理域图 = 3 顶级域（travel 含 2 子流）」（可选）；
4. `orchestration/domain_graph.py` + `travel/commerce/register.py` + `travel/booking/register.py`
   —— 附加 subflow 展示字段（可选档，见第三部分 1）；
5. `frontend-admin/src/app/agents/page.tsx` —— label 渲染映射（可选档，透传值不变）；
6. 守护测试：若实施第 4 项，补一条 Registry subflow 元数据 ↔ `DomainRouter._PREFILTER_DOMAIN_MAP`
   一致性断言（防两处口径漂移）。

**Files forbidden to touch**：第三部分禁改清单全表；另 `system-overview.md`
（grep 证实无五域表述，无需改）、prompt key 命名空间 `customer_service.system/answer`
（同字面不同语义，勿顺手改）。

**Migration strategy**：E1 文档叙事（1-3，零代码风险）→ E2 管理端展示（5）→
E3 Registry 附加 metadata + 一致性守护（4+6）。每步独立提交、独立验证
（契约四门 + router 定向回归）；E2/E3 为可选项，E1 即可结 STOP E 的文档目标，
E3 结「Registry 可表达 subflow」目标。验证口径：四个契约门 + `tests/orchestration`
+ 若触前端 `npx tsc --noEmit`。

---

## 第五部分：风险检查

**风险 1：DomainRouter 与 DomainGraphRegistry 语义不一致 —— 成立，且是 STOP E 的存在理由。**
决策层（`domain_router.py:19-26`）输出 `travel+subflow`，调度层（`domain_registry.py:24` +
`route_selector`）仍以五个平级名为键。这是 STOP B 刻意的兼容过渡（决策归一、调度不动）。
治理方式 = 本文第四部分：文档与展示层采用 travel 子流叙事，调度键永久保留并登记为
「内部调度标识」；**不得**为「消除不一致」去改调度键。

**风险 2：travel_commerce / travel_booking 的 checkpoint/state 依赖 —— 无。**
state 层：三个域图 state 互不共享、无任何以这两个名字命名的键；checkpoint 层：仅 travel
图有 checkpoint（`travel:` 前缀，不含两名字），commerce/booking 无 checkpointer（K0 决策：
单发查询，订单事实在 PG）；跨轮恢复（`travel_pending_resolver.py:122-218`、
`continuation_resolver.py:110`）只认 `travel` 域；`router_node.py:22-27` 的
`_ROUTE_MODE_DOMAIN` 刻意不含两名字——commerce/booking 命中不回写 `active_domain`，
即两名字**从不进入任何跨轮持久化载体**。booking 崩溃恢复走 PG + celery
（`travel/booking/recovery.py`），与图名无关。

**风险 3：管理端依赖旧域名 —— 无硬依赖。**
`frontend-admin` 对 `travel/travel_commerce/travel_booking` 字面量零命中；域名唯一露出点是
agents 页 `route_mode` 原样文本透传（`agents/page.tsx:35-36`），无枚举校验、无域筛选下拉、
无按域统计（summary 按 `kind` 分组）。`selection_funnel`、`customer_service` 仅出现在注释与
prompt key 命名空间（`config/promptGroups.ts:12`，语义独立勿动）。`frontend/`、`frontend-cs/`
对两名字零命中；用户端 `/travel` 页只调 `POST /api/travel/plan`。

**风险 4：evaluation fixture 依赖旧域名 —— 无语义依赖。**
`backend/evaluation/datasets/` 全目录对两名字 grep 为 0；`run_router_eval.py` 消费的
`expected_domain` 枚举不含它们；e2e `route` 取值仅 `direct/plan/workflow`。两名字只以
(a) 连字符评测模块 ID（`travel-commerce`/`travel-booking`，runner 命名空间）与
(b) `backend.config.travel_*` import 路径存在——均属物理层，物理隔离下不受影响。

---

## 最终结论

```text
STOP_E_READY=true
NEXT_ACTION=IMPLEMENT_STOP_E        # 按第四部分重校准范围；E1 文档档为必做最小集
DOMAIN_SEMANTIC_MERGE_ALREADY_IN_ROUTER=true   # domain_router.py:19-26，STOP B 期间完成
REGISTRY_SUBFLOW_SUPPORT=false      # 当前 Registry 无 subflow 字段（可选实施项 E3）
ROUTE_MODE_RENAME_ALLOWED=false     # 永久红线：静默落入 planner 兜底，非响亮失败
PHYSICAL_MERGE_REQUIRED=false       # 代码包/子图/节点名不动
```

强约束重申：本审计未改任何代码；STOP E 实施时每步路径限定提交、跑契约四门与 router
定向回归；E2/E3 可选项若不做，须在 STOP E 结案文档登记为知情裁剪而非遗漏。
