> ⚠️ **本文件已由 v3 取代，仅作历史留档**：见 `docs/superpowers/plans/2026-09-15-frontend-split-plan-v3.md`（含 S0 安全轨、ADR-001~010、决策台账）。**执行以 v3 为准。**

# 前端拆分落地实施计划：用户端 / 管理运营端双分区

> 建立时间：2026-09-15 20:10
> **修订 v2：2026-09-15 20:20（响应评审 13 点）**
> 依据：`frontend/` 与后端容器栈现状**实测**（git status + 目录/配置/导航/鉴权/接口/数据库全量核对）
> 关联：`docs/agent-task-mode-p0-plan.md`（已交付）、`docs/auth/02-详细架构设计.md`（RBAC 设计）、`docs/gateway-apisix-final-report.md`（网关切流完成）
> 定位：本文档是**前端分区**的唯一方案源；实施时按「进度看板」回填提交号。

---

## 修订记录（v1 → v2）

| 评审点 | 处置 | 落点 |
|---|---|---|
| 1 路由数量三处对不上 | **实测修正为 31**（v1 的 24 来自目录数、附录 A 的 30 漏了 `/prompts/[key]/playground`）；新增「计数口径」定义 | §1.1、§2.2、§附录 A |
| 2 P1-6 依赖 P2-0 却排在后面 | 角色信号降级实现**提前到 P1**，新增 `P1-0` | §2.2 P1 |
| 3 M2 冒烟含尚未存在的新页 | M2 冒烟范围改为「分区后 31 条（不含新页）」；新增页 200 归 M3 | §2.3 |
| 4 P1-5 合并未在计数中体现 | 明确 **P1-5 不减路由**（保留两条 URL，仅复用组件），消除 `-1` 歧义 | §2.2 P1-5 |
| 5 比例与附录不符 | **去掉比例，改绝对值**：公开 1 / 用户端 5 / 双端 2 / 管理端 23 | §1.1、§附录 A |
| 6 A1 混淆两件事 | 拆为 **A1a（前端知道你是谁）/ A1b（后端不让你干）**，并写明「P2-4 完成 ≠ 安全边界建立」 | §2.5、§2.3 |
| 7 敏感页与后端接口未对应 | 新增 **§八 敏感页 ↔ 接口 ↔ 鉴权状态表**（实测逐条核查） | §八 |
| 8 P2-1 验收措辞是安全承诺 | 改为「前端重定向到 403 页；API 层拦截依赖 ARCH-06」 | §2.2 P2-1 |
| 9 错误码未进 P0 验收 | P0-1 验收增加错误码映射 + 测试 | §2.2 P0 |
| 10 动态段 fixture 未给值 | 新增 **§附录 B**，9 个动态段全部给实测值 + 取法 | §附录 B |
| 12 P0-4 组件归位无去向表 | 新增 **§附录 C** 逐文件去向表 | §附录 C |
| 13 缺 E2E 策略 | 新增 **§附录 E**：P2 起最小 Playwright 3 例（已决） | §附录 E |
| D1~D4 决策点 | 全部按评审意见**标记为已决**，新增 D5 | §附录 D |

> **评审编号 11 缺项**：你这轮列到 10 后直接跳到 12，第 11 条未见内容。若是漏发，补给我即可增补。

### 本轮新增发现（v1 未覆盖，均为实测）

> 这 4 条都不是前端问题，但**会直接影响本计划的排期与验收口径**，因此前置说明。

**N1 · `/prompts` 三个路由当前 500（表缺失）**
- 现象：`GET /prompts` 与 `GET /prompts/rag.qa` 均返回 `{"error":"ProgrammingError"}`；容器日志 `relation "prompts" does not exist`。
- 根因（**已全 schema 核实**）：`agent_memory` 共 17 张表（`public` 7 + `customer_service` 10），**全部 schema 内搜索 `%prompt%` 命中 0 行**；且 `to_regclass('public.alembic_version')` 为 NULL → **容器栈从未执行 alembic 迁移**（现有表均为运行期自建，非迁移产物）。
- 建表脚本已存在：`backend/sql/alembic/memory/versions/0002_prompt_management.py`。
- **根因已由并发会话独立佐证**（见 `.workbuddy/memory/2026-09-15.md` 20:25「深挖 DB schema 管理」）：compose **全程未调用 alembic**（全仓 grep 命中 0），alembic 事实上只在宿主机手跑 → 容器栈里 memory 线迁移根本没跑过。**N1 是「alembic 未接线」这一机制缺口的下游症状，不是孤例。**
- 处置注意：补跑时**优先用定向 `alembic upgrade head`（memory 线）**；`scripts/rebuild_pg.py --keep-data` 虽是现成入口，但该会话已实测它有「产出库缺 `agent_readonly` 且 DROP `ai` schema」的副作用，不适合为了一张 `prompts` 表而动全库。
- 影响：P2 把 `/prompts` 列为敏感页之前，**先得让它能跑**；否则「拦截一个 500 页面」毫无意义。

**N2 · ~~`agent_business` 库 0 张表~~ → 【本项已作废，保留作误判记录】**
- ~~实测 `\dt` 无任何关系~~ → **误判**。`\dt` 只列 `public` schema，而该库的表在**命名 schema** 下。
- **复核结论**：`agent_business` 实为**多 schema 库，共 18 张表 / 7 个业务 schema**（`ai`、`finance`、`crawler`、`customer`、`inventory`、`order`、`product`），属正常状态。同理 `agent_memory` 是 17 张表（`public` 7 + `customer_service` 10）。
- **排查口径固化**：凡在含多 schema 的 PG 上核对表，**必须查 `information_schema.tables`（按 `table_schema` 分组），不能用 `\dt`**——后者静默漏掉所有非 public schema，会得出"库是空的"这种完全错误的结论。
- 本项对计划无影响，但作为**验收方法学教训**保留：这正是 A9（curl 200 假通过）的同类错误——**用一个只覆盖部分范围的探测手段，得出了全称结论**。

**N3 · `X-Operator-Role` 是伪鉴权，且网关不剥它**
- `backend/app/api/routes/prompts.py`：`x_operator_role: str = Header(default="viewer")`，写端点 `default="editor"`。
- 网关 `GatewayAuthProperties.forgedHeaders` 清单**只有** `X-User-Id` / `X-User-Name` / `X-User-Dept` / `X-Auth-Type` —— **不含 `X-Operator-Role`**。
- 结论：任意能访问 `/api/prompts/*` 的客户端，带上 `X-Operator-Role: admin` 即可发布/回滚 high-risk prompt。**这是现网已存在的越权路径，不是未来风险。**
- 佐证：`_operator_role()` / `_operator_id()`（`prompts.py` L68-72）是**死代码**——返回自身参数、从未接线到 identity 层，说明当时留了接线意图未完成。
- 前端**从未发送**该头（全仓 grep 0 命中），所以前端实际以默认角色运行。

**N4 · `components/EmptyState.tsx` 与 `components/shared/EmptyState.tsx` 重复**
- 目录实测两份同名组件并存，与 `ErrorBoundary` / `ErrorState` 的职责边界重叠。并入 P0-4 一起处置（先比对差异再合并）。

---

## 零、结论先行

| 问题 | 结论 |
|---|---|
| 要不要拆成「用户端 + 管理端」两个前端？ | **要拆信息架构，暂时不拆物理应用。** 用 Next.js Route Group 做三分区（公开/工作台/管理台），URL 一律不变 |
| 现在最该先做什么？ | **不是路由分区，是数据层收敛**。`lib/api/*`、`services/*`、`lib/*.ts` 三套网络层约定并存，是所有页面的地基，分区前不收敛，后面无法抽共享包 |
| 管理端能不能马上做权限隔离？ | **不能，且问题比预想更严重。** 前端零 RBAC 信号；后端除 prompts 的可伪造头外**没有任何角色维度**。见 §八 |
| 什么情况下才值得升级成两个独立应用？ | 见 §五「方案 B 触发器」——满足任一条再做；当前单人团队做物理拆分的 ROI 为负 |
| 后端要补什么？ | 4 个只读接口（Agent 注册表 / Skill 能力清单 / 知识库授权矩阵 / 用户角色）+ 1 个前端已有后端缺页（审批中心）+ **1 个现有越权修复（N3）**。见 §七 |

**推荐路线**：方案 A（单应用 Route Group 三分区），四阶段 P0→P3，约 6~8 周出可用的管理台；方案 B 挂触发器不排期。

---

## 一、总体说明

### 1.1 现状体检（实测证据）

#### 计数口径（v2 统一，三处不再打架）

| 口径 | 数值 | 取法 |
|---|---|---|
| **完整路由数（含动态段）** | **31** | `find src/app -name "page.tsx" \| wc -l` |
| 顶层目录段 | 13 | `find src/app -maxdepth 1 -mindepth 1 -type d \| wc -l` |

> v1 写「24 个路由段」是把 `find -maxdepth 2 -type d` 的**目录数**当成了路由数；v1 附录 A 列了 30 条，漏掉 `/prompts/[key]/playground`（嵌套深度 3）。**以 31 为准**，附录 A 已补全。

#### 路由归属绝对值（替代 v1 的「1:4」比例）

| 归属 | 条数 | 路由 |
|---|---|---|
| 公开 | **1** | `/login` |
| 用户端 | **5** | `/agent`、`/agent/tasks`、`/cs`、`/alerts`、`/alerts/[id]` |
| 双端 | **2** | `/reports`、`/reports/[id]` |
| 管理端 | **23** | 其余（见附录 A） |
| **合计** | **31** | |

#### 其他实测结论

| 维度 | 实测结果 | 影响 |
|---|---|---|
| 技术栈 | Next.js 14.2 App Router / React 18 / Tailwind 3 / Zustand 5 / TanStack Query 5 / vitest 4 | 分区成本低（Route Group 是原生能力），无需引新依赖 |
| 规模 | `src/` 约 **21.5k 行** TS/TSX；单文件最大 `app/competitors/page.tsx` **1325 行** | 尚未到"必须物理拆分"的量级；千行文件需顺手瘦身 |
| 布局 | `layout.tsx` 用 `pathname === '/agent'` 特判隐藏全局 Sidebar | 已是"分区"雏形，但是**硬编码路径**实现的 |
| 数据层 | **三套约定并存**：`lib/api/*`（5 个）+ `src/services/*`（11 个）+ `lib/*.ts`（fetcher/authFetch/sessions-cache/sse-parser/context-summary） | 同一件事三种写法（`selectionDecision.ts` vs `chat.ts`），抽共享包时无从下手 |
| 组件层 | `components/<domain>/` **同时**有 11 个散在 `components/` 根下；另有 `EmptyState` 双份重复（N4） | 归属靠记忆，新人/新会话容易放错位置 |
| 类型层 | `src/types/` 只有 `cs.ts` + `trace.ts`，其余类型内联在页面里 | 跨域类型无家可归 |
| 鉴权 | `AuthGate` 客户端守卫，**无 `middleware.ts`**；`lib/auth.ts` 解析 `userInfo` 但**全仓零处使用角色**；全仓 grep `roles`/`isAdmin` **0 命中** | 管理端角色隔离缺硬前置 |
| 构建 | `next.config.js`：`output:'standalone'`、`compress:false`（SSE 防 gzip 缓冲，2026-09-14 实测踩坑）、`distDir` 可用 `NEXT_DIST_DIR` 覆盖、rewrites（`/api/auth/**`+`/api/sys/**`→网关 8080，其余 `/api/**`→FastAPI 8000） | **这些是"拆分税"**：多一个应用就要多复制一份，错一处就回归 SSE 打字机 |
| 已知重复 | `observability/traces/[id]` 与 `knowledge/operations/traces/[id]` 两份 trace 详情页 | 分区时合并（URL 保留） |
| 导航契约 | `navConfig.test.ts` 已守住「/agent 入口不丢失」「路径全局唯一」 | 分区改导航时必须同步改测试 |

### 1.2 三个方案的共同前提（无论选哪个都要做）

1. **URL 保持不变**。Route Group 不产生路径段，`(admin)/knowledge` 的对外 URL 仍是 `/knowledge` —— 零书签破坏、零文档返工、`navConfig.test.ts` 基本可复用。
2. **`app/layout.tsx` 降级为纯壳**：只保留 `<html>/<body>` + `AuthGate` + `ToastProvider`，Shell（侧边栏）下移到各组 `layout.tsx`。
3. **敏感路由集合显式声明**：`/prompts`（含 playground）、`/observability/*`、`/evaluations`、`/knowledge/pending`、`/schedules` 归管理端，该清单即 §八 的输入。

---

## 二、方案 A：单应用 + Route Group 三分区（推荐）

### 2.1 方案概述

在现有 Next.js 应用内，用**路由组**把 31 个路由切成三块，每块挂自己的 Shell；数据层与组件层做一次收敛。

```
src/app/
├── layout.tsx                    ← 纯壳：html/body + AuthGate + Toast
├── (public)/
│   └── login/page.tsx            ← /login（公开，无 Shell）
├── (workspace)/                  ← 用户端：任务型 Agent 工作台（5 条路由）
│   ├── layout.tsx                ← WorkspaceShell
│   ├── agent/page.tsx            ← /agent（TaskSidebar 自渲染，现有）
│   ├── agent/tasks/page.tsx      ← /agent/tasks
│   ├── cs/page.tsx               ← /cs（坐席工作台）
│   └── alerts/{page,[id]}/…      ← /alerts、/alerts/[id]
└── (admin)/                      ← 管理运营端（23 条路由）
    ├── layout.tsx                ← AdminShell（分组导航 + 面包屑 + 用户区）
    ├── page.tsx                  ← /（数据驾驶舱）
    ├── reports/{page,[id]}/…     ← 双端页面，按 Shell 归属管理端渲染
    ├── knowledge/…               ← 含 pending / operations / keywords / documents
    ├── prompts/…                 ← 含 [key] / [key]/playground
    ├── observability/…           ← traces / tokens / alerts
    ├── evaluations/  schedules/  competitors/
    ├── selection/  selection-decision/[id]/
    ├── approvals/                ← ★ 新增：审批中心（后端路由已有，前端缺页）
    ├── agents/                   ← ★ 新增：Agent 注册表（需后端新接口）
    ├── skills/                   ← ★ 新增：Skill / Tool / Workflow 清单（需后端新接口）
    └── users/                    ← ★ 新增：用户与权限（依赖 auth-service RBAC）
```

横切层收敛为：

```
src/
├── api/            ← 统一网络层：client.ts（fetcher+authFetch+401 refresh+错误码映射）+ <domain>.ts
├── features/       ← 业务域归拢：<domain>/{components,api,hooks,store,types}（页面仍在 app/ 下）
├── components/ui/  ← 纯展示基元
├── lib/            ← 纯工具（sse-parser、session-groups、department、format）
└── types/          ← 仅跨域共享类型
```

**核心业务目标**：普通员工打开就是"干活的地方"，管理员打开就是"配置和看清的地方"，两侧共用同一份网络层与 UI 基元。

### 2.2 实施步骤拆解与排序

#### P0 — 地基收敛（不碰 UI，2~3 天）

> 排序理由：分区是"搬家"，先把地基铺平，否则会把三套网络层约定一起搬进新结构。

| # | 步骤 | 交付物 | 验收 |
|---|---|---|---|
| P0-1 | 建 `src/api/client.ts`：合并 `lib/fetcher.ts` + `lib/authFetch.ts`，统一 `ApiError` / 401→refresh / 超时 / SSE 首帧解析 | 一个 client | ① `tsc --noEmit` 0 错；② 现有 401 refresh 行为不变（含 single-flight）；③ **RAG 4.2 错误码在前端有统一映射表，且 `client.ts` 有对应测试**（原路线图 P3-1 并入此处） |
| P0-2 | `lib/api/*` + `services/*` 迁入 `src/api/<domain>.ts`，统一导出命名 | 约 16 个域模块 | 旧路径保留 re-export barrel 一个发布周期 |
| P0-3 | `lib/api.ts` 与 `services/*` 改为**兼容转发层**（`export * from '@/api/...'`） | 兼容层 | 不新增 import；`grep` 确认无页面直连旧实现 |
| P0-4 | 组件归属规整：11 个根级组件 + `EmptyState` 重复项按 **§附录 C** 逐文件迁移 | 目录收敛 | `npm run test` ≥ 现有 124 基线 |
| P0-5 | 数据层一致性测试：新增 `api/__tests__/surface.test.ts` 断言"每域一个模块、无重复导出" | 契约测试 | 新增测试绿 |

#### P1 — 路由三分区 + 双 Shell（3~5 天，含新增 P1-0）

| # | 步骤 | 交付物 | 验收 |
|---|---|---|---|
| **P1-0** | **角色信号降级实现**（v2 从原 P2-0 前移，因 P1-6 依赖它）：`userInfo.roles` 优先 → 缺失时读 `NEXT_PUBLIC_ADMIN_USERS` 白名单；**代码注释显式标注"非安全边界"** | 一个 `useRole()` hook | hook 有单测；注释存在 |
| P1-1 | 新建 `(public)/(workspace)/(admin)` 三个路由组，按 §2.1 目录树移动文件（**URL 不变**） | 三分区 | `next build` 通过（同一路径被两组定义会在构建期报错，天然门禁） |
| P1-2 | **31 条现有路由**逐一冒烟：`curl` 全部 200（fixture 见 §附录 B） | 冒烟脚本 | 31/31 = 200 |
| P1-3 | `layout.tsx` 瘦身为纯壳，Shell 下移到组 layout | 两个 layout | `/login` 无 Shell，`pathname === '/agent'` 特判删除 |
| P1-4 | `navConfig.tsx` 拆为 `nav/workspaceNav.tsx` + `nav/adminNav.tsx`；`AdminShell` 按"内容/运营/可观测/治理"分组；**双端页在两个 Shell 用不同入口标签**（见 §附录 D D4） | 两份导航配置 | `navConfig.test.ts` 改造后仍断言 `/agent` 不丢失 + 路径唯一 |
| P1-5 | trace 详情去重：`knowledge/operations/traces/[id]` 改为复用 `observability/traces/[id]` 的组件。**保留两条 URL，不减路由** | 一套 trace 详情组件 | 两条 URL 均 200 且渲染一致 |
| P1-6 | `AuthGate` → `RoleGate`（角色判定来自 P1-0） | 守卫升级 | `/` 落地：非管理员重定向 `/agent` |

#### P2 — 管理端权限与补齐（2~3 周）

| # | 步骤 | 交付物 | 依赖 |
|---|---|---|---|
| P2-0 | **N1 修复**：定向 `alembic upgrade head`（memory 线）建 `prompts` 表 | `/prompts` 可用 | 迁移脚本 `0002_prompt_management.py` 已在库；**不要用 `rebuild_pg.py` 补**（会拖垮 `agent_readonly` 与 `ai` schema，见 N1 处置注意） |
| P2-1 | 敏感页守卫：5 个敏感路由非管理员 → **前端重定向到 403 页**。验收表述见下 | 403 页 + 守卫 | **此为 UI 层；API 层拦截依赖 ARCH-06** |
| P2-2 | 后端 4 个只读接口（见 §七）+ **N3 越权修复**（`X-Operator-Role` 进网关剥离清单 + 接线 identity） | API | 越权复现用例由 HTTP 层断言 |
| P2-3 | 新增页：Agent 注册表、Skill/Tool/Workflow 清单、知识库授权矩阵、审批中心 | 4 个页面（31 → 35 条路由） | 数据真实来自后端，无硬编码 |
| P2-4 | **真角色信号**：auth-service JWT claims 加 `roles` → 网关透传 → 前端消费（替换 P1-0 白名单） | claims 链路 | 白名单代码删除 |
| P2-5 | **ARCH-06 服务端方法级权限**，覆盖 §八 的 5 组敏感接口 | 后端鉴权 | 见 §2.3 M4 的硬性 exit criteria |

> **P2-1 验收表述（v2 修正，避免被当成安全承诺）**
> ~~"非管理员访问全部被拦"~~ →
> **"非管理员访问 5 个敏感路由 URL 时，前端重定向到 403 页。此为 UI 层行为；API 层拦截依赖 ARCH-06（P2-5），在该项完成前不得对外表述为『已实现权限隔离』。"**

#### P3 — 治理能力（1~2 周，按需）

Prompt 审批/灰度与发布门禁（评测 PASS → 才可发布）、权限矩阵与审计日志、管理端操作留痕。

### 2.3 关键里程碑及时间节点

| 里程碑 | 周期 | 验收标准（可测） |
|---|---|---|
| M1 地基就绪 | W1 | `api/` 单层网络通行；`tsc --noEmit` 0 错；vitest ≥124 绿；错误码映射有测试；旧 barrel 保留 |
| M2 分区完成 | W2 | 三分区落地；**31 条现有路由** curl 全 200（不含 P2-3 新增页）；`/login` 无 Shell；`navConfig.test` 改造后绿 |
| M3 管理台可见性收敛 | W3~W5 | `/prompts` 已可用（P2-0）；5 个敏感路由非管理员 100% 重定向 403；4 个新页可用（**35 条路由** curl 全 200，fixture 含新增 4 条）；管理员 5 项高频操作 ≤2 跳 |
| M4 真权限闭环 | W6~W8 | ① roles claims 全链路打通、白名单代码删除；② **ARCH-06 覆盖 §八 表中全部 5 组敏感接口**（硬性 exit criteria，不接受"有明确结论"这类软标准）；③ `X-Operator-Role` 越权用例已无法复现 |

### 2.4 所需资源评估

| 角色 | 人日 | 说明 |
|---|---|---|
| 前端 | P0 3 + P1 6（含 P1-0）+ P2 12 + P3 8 ≈ **29 人日** | 单人可做，建议按阶段交付 |
| 后端 | P2-2 的 4 个只读接口 + N1 迁移 + N3 修复 ≈ **7 人日** | N3 是安全修复，优先 |
| 鉴权（auth-service） | claims 加 roles ≈ **2 人日** | 跨仓库（Enterprise_OA），需协调 |
| E2E | Playwright 最小 3 例 ≈ **1 人日** | 见 §附录 E |
| 测试/QA | 全路由冒烟 + 敏感路由权限回归 ≈ **3 人日** | 已有 curl 冒烟约定，增量小 |

**成本估算（假设与口径）**：前端 ¥1,500/人日、后端 ¥1,800/人日、鉴权 ¥1,800/人日。前端 29 人日 ≈ ¥4.35 万，后端 7 人日 ≈ ¥1.26 万，鉴权 2 人日 ≈ ¥0.36 万，E2E 1 人日 ≈ ¥0.15 万，**合计约 ¥6.1 万**（估算仅供排期参考，不含 P3）。云资源增量 ≈ 0。

### 2.5 潜在风险识别及应对措施

| # | 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|---|
| **A1a** | **前端角色判断被误当安全边界**（"前端知道你是谁"） | 高（敏感页数据实际可被普通员工直连 API 拿到） | **高** | P1-0 代码注释 + §2.2 P2-1 的验收表述；对外沟通禁用"权限隔离"措辞 |
| **A1b** | **后端从未实现角色维度**（"后端不让你干"）——ARCH-06 在鉴权设计里是"推荐推迟"，且现有 prompts 头可伪造（N3） | **高（现网已有越权路径）** | **已发生** | ① N3 立即修复（网关剥离清单 + identity 接线）；② ARCH-06 从"推迟"升级为 P2-5；③ **明确：P2-4 完成 ≠ 安全边界建立**——P2-4 只解决 A1a，A1b 必须靠 P2-5 |
| A2 | Route Group 迁移期路径冲突 | 中（构建失败） | 中 | 靠 `next build` 构建期报错天然拦截；分组提交，一次只动一组 |
| A3 | 布局切换样式回归 | 中 | 中 | 31 路由 curl 200 + 关键页截图对比；`globals.css` 变量不动 |
| A4 | 数据层收敛破坏既有调用方 | 中 | 中 | 旧 barrel 保留一个发布周期；`tsc --noEmit` + vitest 基线双门禁；禁止同一提交内改行为 |
| A5 | **并行会话冲突**（多会话同时改 `frontend/`） | 高（覆盖他人未提交成果） | 高 | 开工前 `git status` + 文件 mtime 核对；分区期间在看板声明"frontend 冻结"，冲突时只做增量 |
| A6 | 沙箱内 `next dev/build` 的 distDir 清理被 safe-delete 拦截 | 低 | 高 | 沿用 `NEXT_DIST_DIR=.next-dev`（指向尚不存在的空目录）；每次重启换新目录 |
| A7 | 动态段冒烟 fixture 失效（数据变动/清库） | 中 | 中 | 采用「list → 取首个 id」的自愈式取法（脚本化），§附录 B 的固定值仅作兜底 |
| A8 | 管理端 4 个新页依赖后端接口，接口延期拖住 P2 | 中 | 中 | P2-3 与 P2-2 并行；接口未就绪时先出骨架 + 空态，**不写假数据** |
| A9 | 全路由 curl 200 的**假通过**（SPA 一律 200，页面内容可能报错，如 N1 类问题；同源错误见 N2 的误判复盘） | 中 | 高 | 冒烟脚本增加"响应体不含 `"error"` 关键字"断言；关键页辅以 E2E 或人工核对 |

### 2.6 成功落地的衡量指标

**系统指标**
- `tsc --noEmit` 0 错；vitest ≥ 现有 124 基线（分区后目标 +8）
- M2：31/31 路由 curl 200；M3：35/35（且响应体无 `"error"`）
- `/agent` 首屏 JS 体积相对基线**不增加**（管理端代码不进入用户端 bundle —— 靠 Route Group 按组 code splitting 达成，需实测确认）

**研发效率指标**
- 新增一个管理页面的改动面：从"改 3 处 + 靠记忆猜归属"降到"1 个目录 + 1 行导航配置"
- 导航一致性有测试守护（路径唯一 + 页面归属不遗漏）

**业务指标**
- 管理员 5 项高频操作（改 Prompt / 传知识 / 看 Trace / 查评测 / 配定时任务）**≤2 跳可达**
- 5 个敏感路由非管理员 100% 重定向 403（UI 层）
- 普通员工在 `/` 落地 → 自动落 `/agent`

**安全指标（v2 新增，独立于 UI 指标）**
- `X-Operator-Role` 越权用例无法复现（N3 修复后）
- ARCH-06 对 §八 表中 5 组接口的覆盖率 = 5/5

---

## 三、方案 B：Monorepo 双应用（挂触发器，不排期）

### 3.1 方案概述

`frontend/` 升级为 pnpm workspace：`apps/workspace` + `apps/admin` + `packages/shared`（网络层、鉴权、UI 基元、SSE 解析）。两个应用独立构建、独立发布、可独立域名。

### 3.2 实施步骤拆解与排序

| 阶段 | 步骤 | 说明 |
|---|---|---|
| B0 | 抽 `packages/shared`：`api/client`、`auth`、`sse-parser`、`session-groups`、`components/ui`、Tailwind preset | **前置条件：方案 A 的 P0 必须先完成**，否则三套网络层约定原样搬进共享包 |
| B1 | 建两个 app 骨架，复制构建配置 | `next.config.js` 的 rewrites / `compress:false` / `distDir` 各一份；`vitest.config.ts` 的 `oxc.jsx` + alias 各一份 |
| B2 | 按 A 的分区结果整体搬迁 | 迁移脚本 + 35 路由冒烟 |
| B3 | CI 双构建 + 双 Dockerfile + standalone 双产物 | 反代按路径分流 |
| B4 | 共享包版本治理 | changesets + 同仓强制策略，禁止组件双份实现 |

### 3.3 关键里程碑

| 里程碑 | 周期 | 验收 |
|---|---|---|
| B1 共享包可用 | W1~W2 | 两个 app 引用同一份 `@agent/shared`，无双份实现 |
| B2 双应用跑通 | W3 | 两套 dev + 两套 build 成功；**SSE 打字机效果两边都实测正常**（`compress:false` 两边都生效） |
| B3 双发布 | W4 | 独立镜像 + 反代分流；两套 CI 绿灯 |

### 3.4 所需资源评估

方案 A 全部前端人日 **+ 额外 10~15 人日**（workspace 化、共享包提取、双 CI、反代分流、两套构建排障）。**注意：沙箱内 `next dev/build` 的 distDir 坑会翻倍**（每次重启要准备新空目录 ×2）。

### 3.5 潜在风险识别及应对措施

| # | 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|---|
| B1 | 共享配置漂移——任一应用漏掉 `compress:false` → **SSE 打字机整段回归**（2026-09-14 实测过的坑） | 高 | **高** | 配置进 `packages/shared` preset，禁止 app 内手写；加"SSE 逐 chunk"集成测试 |
| B2 | rewrites 分流双份维护 | 中 | 高 | 抽成可复用函数 |
| B3 | 组件/工具双份实现后漂移 | 中 | 高 | changesets + CI 校验共享包外不得重复定义导出 |
| B4 | 短期 ROI 为负（单人团队、无独立发布节奏） | 中 | 高 | 挂触发器不排期（§五） |
| B5 | 部署与反代复杂度上升 | 中 | 中 | 规则纳入 `部署清单.md`；灰度期两套并存可回退 |
| B6 | 沙箱构建排障成本翻倍 | 低 | 高 | 沿用 `NEXT_DIST_DIR` 约定，写进 README |

### 3.6 成功落地的衡量指标

- 两个应用可独立发布（互不触发对方流水线）
- 共享包双份实现数为 **0**（CI 强制）
- 管理台 bundle 不进入用户端（需实测确认体积）
- 同一功能改动只改一处（共享包内）

---

## 四、方案 C：最小目录规整（基线对照）

### 4.1 方案概述

不引入路由组、不动布局，只做三件事：数据层收敛（同 A 的 P0）、根级组件按域归位（§附录 C）、删除重复 trace 详情页与 `EmptyState` 重复项。

### 4.2~4.6 摘要

- **步骤**：即 A 的 P0-1~P0-5 + P1-5，约 4 人日。
- **里程碑**：W1 内完成，验收同 M1 加"重复实现已消除"。
- **资源**：前端 4 人日 ≈ ¥0.6 万。
- **风险**：低。主要风险是"做完没有可感知变化"，容易半途而废。
- **指标**：代码归属明确率提升，但**业务指标无改善**（用户端与管理端仍混在同一侧边栏）。

**定位**：这是"不做分区的最大合理投入"。若暂不打算做管理台，选它。

---

## 五、三个方案落地可行性对比

### 5.1 多维度评分（1~5 分，权重合计 100）

| 维度 | 权重 | A 单应用分区 | B 双应用 | C 最小规整 |
|---|---|---|---|---|
| 与平台现状契合 / 可复用度 | 20% | **5** | 3 | 5 |
| 技术难度（越高越易） | 15% | **4** | 2 | 5 |
| 业务价值 / ROI | 25% | **5** | 3 | 2 |
| 实施周期（越短越高） | 10% | **4** | 2 | 5 |
| 风险可控性 | 15% | **4** | 3 | 5 |
| 资源投入（越省越高） | 15% | **4** | 2 | 4 |
| **加权总分** | 100% | **4.45** | 2.55 | 4.05 |

### 5.2 可行性结论

- **A 推荐**：21.5k 行规模、单人/小团队、无独立发布节奏 —— 物理拆分收益收不回成本；而 IA 混乱的痛点已真实存在（23 个管理页塞在用户侧边栏里）。Route Group 是 Next 原生能力，**零新依赖、零 URL 破坏**。
- **B 不推荐现在做**：唯一确定收益是"管理台 bundle 不发给员工"——但客户端 bundle 从来不是安全边界（真正的边界在网关与 API），代价是共享包治理 + 双 CI + 配置漂移风险。
- **C 是下界**：只解决代码整洁，不解决 IA 与权限问题。

### 5.3 优先级建议与落地节奏

```
W1        W2         W3 ~ W5            W6 ~ W8
├─P0 地基─┼─P1 分区──┼─P2 管理端权限+补齐┼─P2-4/P2-5 真权限闭环┐
│ api 收敛 │ 三分区   │ N1 迁移 → 敏感页守卫│ roles claims      │
│ 组件归位 │ P1-0 角色│ 4 新页 + 4 接口   │ ARCH-06 5/5 覆盖  │
└─────────┴─────────┴──────────────────┴───────────────────┘
                                    ↓ 满足下列任一触发器才启动
方案 B 触发器：
  ① 管理端发布节奏与用户端冲突（一周多次 vs 稳定）
  ② 管理端引入大依赖（monaco / echarts / 大型表格）
  ③ 管理端需要独立域名或不同安全暴露面（对外发布）
  ④ 团队 > 4 人且分两条前端线
```

**明确不做**：不加 `/admin` URL 前缀；在 A 完成前不启动 B；不对 `globals.css` 变量做重命名。

---

## 六、与现有 Roadmap 的衔接

| 现有事项 | 与本计划的关系 | 建议 |
|---|---|---|
| `docs/agent-task-mode-p0-plan.md`（已交付） | TaskSidebar 即未来的 WorkspaceShell | P1-3 直接复用它作为 `(workspace)/layout.tsx`，删除 `layout.tsx` 的路径特判 |
| 统一路线图 P3-1「RAG 4.2 错误码体系（需前端联动）」 | 错误码消费属网络层 | **并入 P0-1**（含测试），避免改两遍 |
| 统一路线图 P5-2「guest 路由策略 + department.ts 登录态化」 | 与 `RoleGate` 同源 | **并入 P1-0 / P1-6**：角色与部门都从登录态取，不再 localStorage |
| auth 路线 ARCH-06（服务端方法级权限，现为"推荐推迟"） | **管理端安全性的真前置** | **升级为 P2-5 必做**，exit criteria 为「覆盖 §八 5 组接口」 |
| auth 路线 ARCH-13（JWT claims 加 dept） | 同一 claims 通道 | 与 P2-4「claims 加 roles」**同批实施**，一次改完 |
| auth 设计§P3「8000 收口」 | 与 N3 相关 | N3 修复**不能只依赖 8000 收口**——网关侧剥离清单也要补，两者互补 |
| RAG 优化计划 / 阶段 5 企业级用量审计 | 管理端「Token 用量/审计」页已有 | P2-3 顺带接入，不新增页面 |
| 前端全路由 curl 冒烟约定 | 分区验收手段 | 沿用，路由数从 31 → 35，并补 A9 的响应体断言 |

---

## 七、后端欠账清单（管理端落地依赖）

| # | 能力 | 现状（实测） | 需要的接口 | 优先级 |
|---|---|---|---|---|
| 1 | Agent 注册表 | **无对外接口**。域图注册只在 `backend/domains/__init__.py` → `domain_graph_registry`，是进程内对象 | `GET /api/agents`——已注册域图：name / 状态 / 依赖能力 / 版本 | P2 高 |
| 2 | Skill / Tool 清单 | **无对外接口**。单一事实源在 `skills/registry.py::_instances`，`tool_registry.CAPABILITY_MAP` 由它派生；路由清单在 `orchestration/router/capabilities.yaml` | `GET /api/capabilities`——能力 / 工具 / 参数 schema / 是否启用 / 所属域 | P2 高 |
| 3 | Workflow 清单 | ✅ **已有** `GET /workflows`（含 `/runs`、`/runs/{id}`、`POST /{name}/trigger`） | 前端补页面即可 | P2 中 |
| 4 | 知识库授权矩阵 | **无对外接口**。矩阵在后端 `owner_depts` 逻辑与 `config/knowledge_base.py` 的 `DEPARTMENTS` | `GET /api/knowledge/authorization`——知识库 × 部门矩阵 + 当前用户可见集合 | P2 中 |
| 5 | 用户 / 角色 | 在 **auth-service（Enterprise_OA）**，RBAC 五表已种子；app 侧只消费身份与部门 | claims 加 `roles`（ARCH-13 通道）+ 管理页走 auth-service | P2 高（跨仓） |
| 6 | 审批中心 | 后端 **已有** `approvals` router；前端**无页面** | 前端补页 | P2 中 |
| 7 | 知识库待审 | 前端有 `/knowledge/pending`；后端 rag 路由已有 | 归入管理端 + 权限拦截 | P2 低 |
| **8** | **N3 越权修复** | `X-Operator-Role` 可伪造；不在网关 `forgedHeaders` 清单 | ① 加入剥离清单；② `prompts.py` 的 `_operator_role()` 接线到 identity（删除死代码） | **P2 最高（安全）** |
| **9** | **N1 迁移缺跑** | `agent_memory` **全 schema** 无 `prompts` 表、无 `alembic_version`；compose 未调用 alembic（已由并发会话佐证） | 定向 `alembic upgrade head`（memory 线）。**根治是「alembic 单轨化」，属另一条线（见 20:25 记忆）** | **P2 最高（阻塞页）** |

> **#1 #2 是"平台治理"类页面，价值最高**（回答"平台现在有哪些 Agent / 能力可用、谁改了它"），但当前能力清单只存在于进程内对象与 YAML，**没有任何对外只读视图** —— 这是本次盘点最值得注意的功能缺口。

---

## 八、敏感页 ↔ 后端接口 ↔ 鉴权状态（v2 新增）

> 本表是 P2-5（ARCH-06）的**验收输入**。结论：当前 5 组敏感接口中，**0 组**具备真实的方法级权限；其中 1 组有一个可绕过的伪鉴权实现。

| # | 敏感路由 | 后端接口（实测路径） | 现有鉴权状态 | ARCH-06 覆盖 |
|---|---|---|---|---|
| 1 | `/prompts`、`/prompts/[key]`、`/prompts/[key]/playground` | `GET/POST /prompts*` | ⚠️ **伪鉴权**：`_check_permission(risk_level, action, role)` 存在，但 `role` 取自**客户端头 `X-Operator-Role`**（`Header(default="viewer")`，写端点 `default="editor"`）。该头**不在**网关 `forgedHeaders` 剥离清单（清单仅 `X-User-Id`/`X-User-Name`/`X-User-Dept`/`X-Auth-Type`）→ 任意客户端设 `X-Operator-Role: admin` 即可发布/回滚 high-risk prompt。且前端从不发送该头（grep 0 命中） | ❌ 未覆盖（且现状**已可绕**，见 N3） |
| 2 | `/observability`、`/observability/traces`、`/observability/traces/[id]`、`/observability/tokens`、`/observability/alerts` | `/observability/traces`、`/traces/{id}`、`/traces/active`、`/traces/stats`、`/tokens/summary`、`/tokens/calls`、`/cs-quality` | ❌ 无方法级鉴权。仅 `X-API-Key` + 网关 JWT 认证，**无角色维度** | ❌ |
| 3 | `/evaluations` | `/evaluation*` | ❌ 无 | ❌ |
| 4 | `/knowledge/pending` | `/rag/documents*`（含审核态软过滤） | ❌ 无。`X-Internal-Token` 是服务间机制，不覆盖前端调用 | ❌ |
| 5 | `/schedules` | `/schedules*` | ❌ 无 | ❌ |

**全局结论**：除 #1 那个可伪造的头之外，后端**没有任何角色/权限维度**。身份（`X-User-Id` 等）由网关注入且不可伪造，但**只用于审计与归属，不用于授权**。

**因此 P2-5 的最小交付**：为上述 5 组接口补一层"角色 → 允许动作"的判定，角色来源必须是**网关注入的、不可伪造的**凭据（JWT claims 或网关注入头），**不得**再引入任何客户端可自设的头。

---

## 附录 A：路由归属全表（31 条，v2 修正）

| # | 路由 | 归属 | 备注 |
|---|---|---|---|
| 1 | `/login` | 公开 | `(public)`，无 Shell |
| 2 | `/agent` | 用户端 | 任务模式主入口（TaskSidebar 自渲染） |
| 3 | `/agent/tasks` | 用户端 | |
| 4 | `/cs` | 用户端 | 坐席工作台 |
| 5 | `/alerts` | 用户端 | 库存预警待办 |
| 6 | `/alerts/[id]` | 用户端 | |
| 7 | `/reports` | 双端 | 用户端「我的报告」/ 管理端「全部报告」 |
| 8 | `/reports/[id]` | 双端 | |
| 9 | `/` | 管理端 | 数据驾驶舱；非管理员落地重定向 `/agent` |
| 10 | `/knowledge` | 管理端 | |
| 11 | `/knowledge/documents` | 管理端 | |
| 12 | `/knowledge/keywords` | 管理端 | |
| 13 | `/knowledge/operations` | 管理端 | |
| 14 | `/knowledge/operations/traces/[id]` | 管理端 | ★ P1-5 与 #22 去重（URL 保留） |
| 15 | `/knowledge/pending` | 管理端 | 敏感 |
| 16 | `/cs/conversations` | 管理端 | 会话管理/质检 |
| 17 | `/cs/conversations/[id]` | 管理端 | |
| 18 | `/observability` | 管理端 | 敏感 |
| 19 | `/observability/traces` | 管理端 | 敏感 |
| 20 | `/observability/traces/[id]` | 管理端 | 敏感 |
| 21 | `/observability/tokens` | 管理端 | 敏感 |
| 22 | `/observability/alerts` | 管理端 | 敏感 |
| 23 | `/prompts` | 管理端 | 敏感；**当前 500（N1）** |
| 24 | `/prompts/[key]` | 管理端 | 敏感；**当前 500（N1）** |
| 25 | `/prompts/[key]/playground` | 管理端 | 敏感；**v1 遗漏项** |
| 26 | `/evaluations` | 管理端 | 敏感 |
| 27 | `/schedules` | 管理端 | 敏感 |
| 28 | `/competitors` | 管理端 | 页面 1325 行，需瘦身 |
| 29 | `/selection` | 管理端 | |
| 30 | `/selection-decision` | 管理端 | |
| 31 | `/selection-decision/[id]` | 管理端 | |
| +32 | `/approvals` | 管理端 | ★ P2-3 新增（后端已有） |
| +33 | `/agents` | 管理端 | ★ P2-3 新增（需后端接口） |
| +34 | `/skills` | 管理端 | ★ P2-3 新增（需后端接口） |
| +35 | `/users` | 管理端 | ★ P2-3 新增（需 RBAC） |

**合计**：现状 **31** 条 → 分区后 **31**（P1-5 不减路由）→ P2-3 后 **35** 条。

---

## 附录 B：动态段冒烟 fixture（v2 新增，2026-09-15 实测）

> 取数条件：容器栈运行中（`agent-app-1` healthy），`X-API-Key` 取自容器 env（`docker exec agent-app-1 sh -c 'echo "$API_KEY"'`）。
> **前端口径**：`next.config.js` 的 rewrite 会把 `/api/**` 的 `/api` 剥掉，故前端请求 `/api/<path>` ↔ 后端 `/<path>`。

| 动态路由 | 后端接口 | **实测 fixture** | 来源 / 备注 |
|---|---|---|---|
| `/observability/traces/[id]` | `GET /observability/traces/{id}` | **`ee3b91e8d0f5`** | 实测列表首条（2026-09-15T07:29:22，session `wf-final`） |
| 同上（备选） | 同上 | `7a51d505cfd5` | `/reports` 首条的 `trace_id` |
| `/knowledge/operations/traces/[id]` | 同上 | `ee3b91e8d0f5` | 与上一行同源（P1-5 去重后同组件） |
| `/reports/[id]` | `GET /reports/{id}` | **`7d4fdd9bc978`** | 实测列表首条（2026-09-15 daily_report） |
| 同上（备选） | 同上 | `5758a109df10` | 2026-09-13 daily_report |
| `/cs/conversations/[id]` | `GET /cs/conversations/{id}` | **`conv_test_002`** | 实测列表首条（user_1002，pending） |
| `/alerts/[id]` | `GET /inventory/cases/{case_id}` | **`1`** | 实测 `/inventory/cases` 首条（product_id=`iPhone-15-Pro`，state=critical） |
| `/selection-decision/[id]` | `GET /selection-decision/tasks/{task_id}` | **`7a0458a78139`** | 实测 `/selection-decision/tasks` 首条（verdict=no_go） |
| `/prompts/[key]` | `GET /prompts/{key}` | `rag.qa`、`planner.system` | 代码受控 key（`backend/prompts/registry.py`）；**当前 500，见 N1** |
| `/prompts/[key]/playground` | 同上 | 同上 | 同上 |

**执行注意（A7 应对）**：上表固定值会随数据变动失效。冒烟脚本应采用**自愈式取法**——
`GET <列表接口> → 取 items[0].id → 拼详情 URL → 断言 200 且响应体不含 "error"`。固定值仅作人工兜底。

**已排除的错误路径（实测 404，避免踩坑）**：`/selection-decision?limit=1`（正确为 `/selection-decision/tasks`）、`/inventory/alerts`（正确为 `/inventory/cases`）。

---

## 附录 C：P0-4 组件归位表（v2 新增）

| 现位置 | 去向 | 理由 |
|---|---|---|
| `components/ChatInput.tsx` | `features/chat/` | 对话域 |
| `components/ChatView.tsx` | `features/chat/` | 对话域 |
| `components/MessageList.tsx` | `features/chat/` | 对话域 |
| `components/MessageBubble.tsx` | `features/chat/` | 对话域 |
| `components/MarkdownContent.tsx` | `features/chat/` | 含 rehype/remark 配置，业务语义强于纯基元 |
| `components/SourceCard.tsx` | `features/chat/` | 引用来源卡片 |
| `components/Sidebar.tsx` | `features/layout/` | 两个 Shell 共用的折叠逻辑 |
| `components/LLMSwitcher.tsx` | `features/agent/` | 已被 ComposerToolbar 消费 |
| `components/MultiQueryToggle.tsx` | `features/knowledge/` | 仅知识库检索使用 |
| `components/EmptyState.tsx` | `components/ui/` | ★ **与 `components/shared/EmptyState.tsx` 重复（N4）**：先比对差异合并为一份，再落位 |
| `components/ErrorBoundary.tsx` | `components/ui/` | 纯基元。注意与 `components/shared/ErrorState.tsx` 职责区分（Boundary=捕获，State=展示），合并前确认 |
| `components/shared/*`（EmptyState/ErrorState/PlaceholderPage/Skeleton/Toast） | `components/ui/` | 目录语义统一（`shared` → `ui`） |

**目录约定（D3 已决）**：`features/<domain>/{components,api,hooks,types,store}` 放业务组件与逻辑，**页面仍在 `app/` 下**——与 App Router colocation 习惯一致，也是将来抽 `packages/shared` 的最短路径。

---

## 附录 D：决策点（v2 全部已决）

| # | 决策点 | 结论 | 依据 |
|---|---|---|---|
| **D1** | URL 是否加 `/admin` 前缀 | **不加**。Route Group 不产生路径段，零书签/文档破坏；同一路径被两组定义会在 `next build` 期报错，是天然门禁 | 评审同意 |
| **D2** | ARCH-06 是否从"推迟"提前到 P2 | **提前，且升级为 P2-5**。exit criteria 为**「ARCH-06 覆盖 §八 表中全部 5 组敏感接口」**（硬性、可核对），不接受"有明确结论"这类软标准 | 评审强烈建议；且 N3 证明已有现网越权 |
| **D3** | `features/` 是否作为最终组件约定 | **引入**。`features/<domain>/` 放组件、hooks、api、types、store；**页面仍留 `app/` 下**，与 App Router colocation 一致，也是抽 `packages/shared` 的最短路径 | 评审建议 |
| **D4** | `/alerts`、`/reports` 是否收进 `/agent` | **第一阶段保留**。且在导航上区分：**用户端 Shell 显示「我的告警 / 我的报告」，管理端 Shell 显示「告警规则 / 全部报告」**（同一 URL、不同 Shell、不同入口标签）——落点在 P1-4 的 nav 配置 | 评审建议 |
| **D5** | E2E 测试策略（原第 13 点） | **P2 起引入最小 Playwright，3 条用例**；P0/P1 仍用 curl + 人工回归 | 见 §附录 E |

---

## 附录 E：E2E 测试策略（v2 新增，D5 已决）

**结论：不"本阶段不做 E2E"，而是"P2 起做最小 E2E"。**

**理由**：awkward 但必须承认——curl 只能证明"返回 200"，而本轮要验收的三件事**全是纯客户端行为**：
1. `RoleGate` 对 5 个敏感路由的重定向到 403；
2. `/` 的落地重定向（非管理员 → `/agent`）；
3. Route Group 的 Shell 切换（同一 URL 在不同组渲染不同 Shell）。

curl 拿到的只是 SPA 的 200 HTML，**验证不了这三件事**。而其中前两项恰好是**权限**相关——最不该靠"人工点一下看看"的地方。A9（curl 200 假通过）也印证了这一点。

**交付范围（P2 阶段，约 1 人日）**

| # | 用例 | 断言 |
|---|---|---|
| E1 | 非管理员直连 `/prompts`、`/observability/traces`、`/evaluations`、`/knowledge/pending`、`/schedules` | 5/5 重定向到 403 页，URL 不残留敏感内容 |
| E2 | 非管理员访问 `/` | 重定向到 `/agent` |
| E3 | 管理员访问 `/agent` 与 `/knowledge` | Shell 不同：`/agent` 无全局侧边栏（TaskSidebar），`/knowledge` 有 AdminShell 分组导航 |

**明确不做的**：不做全量 E2E 回归、不接 CI 阻塞门禁（先手动跑）、不覆盖业务流（对话/上传/评测等仍用现有 vitest + 手工）。**待 P2 完成且前端有稳定 preview 环境后再评估是否扩面。**

**环境注意**：Playwright 在 WorkBuddy 沙箱内首次需下载浏览器二进制，且 `next dev` 仍需 `NEXT_DIST_DIR` 指向新空目录（见 A6）。
