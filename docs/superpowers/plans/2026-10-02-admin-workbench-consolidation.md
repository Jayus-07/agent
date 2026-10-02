# Admin Workbench Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将管理端的选品、知识库、评测和运行监控旧列表页合并为四个带 URL Tab 的工作台，保留现有 API、权限、详情页和旧链接兼容性。

**Architecture:** 新增共享 WorkbenchShell 和 Tab 状态 Hook；每个旧页面主体抽取为独立面板组件，由新的工作台路由按白名单 Tab 条件渲染。旧列表路由只做 replace 兼容跳转并透传查询参数，详情路由继续保留。导航和管理端跨页入口改指向新工作台。

**Tech Stack:** Next.js 14 App Router、React、TypeScript、Vitest、现有管理端 API 服务和 RoleGate。

**Spec:** docs/superpowers/specs/2026-10-02-admin-workbench-consolidation-design.md

## Global Constraints

- 全部改动限定在 frontend-admin、docs/DESIGN.md 和本计划涉及的管理端文档；不改后端 API、数据库、角色定义和审批协议。
- 当前工作区存在大量其它会话的已修改/未跟踪文件。每个任务开始前运行 git status --short 和目标文件的 git diff，只修改本任务目标范围；禁止 reset、checkout 或覆盖他人改动。
- 遵循 TDD：先写或补强失败测试，运行目标测试确认失败，再做最小实现，最后运行同一测试确认通过。
- 面板只搬运现有页面的请求、表单、轮询、Toast、错误态和权限逻辑；WorkbenchShell 不调用业务 API、不持有业务状态、不吞掉面板错误。
- Tab 的唯一事实源是 URL 查询参数；Tab 白名单之外的值回退到默认 Tab。Tab 切换使用 router.push，兼容旧 URL 使用 router.replace，并保留除 tab 外的查询参数。
- 不删除旧路由文件。旧列表路由完成兼容跳转后不再复制页面主体；选品报告详情、操作日志详情和 Trace 详情保持原地址。
- 修改或新增代码注释使用中文；不使用 except Exception: pass 等静默兜底。

## Review Focus

实现完成后重点审查以下风险，并为每项保留可复现测试或验证证据：

1. 缺失、非法和未知 tab 是否都回退到正确默认页，且不会渲染任意查询值。
2. 旧 URL 上的筛选参数是否原样保留，尤其是 Trace 的 has_tool、workflow_name、时间范围和分页参数。
3. 详情页返回、首页待办、Tool/Report/告警跳转是否进入新工作台的正确 Tab，而不是隐藏的旧列表。
4. 知识库待复核的 admin RoleGate、评测段的 admin layout 和现有 editor/viewer 导航语义是否保持。
5. 选品任务轮询、Trace 查询和 Token 图表在 Tab 卸载/切换后是否停止旧请求，没有重复轮询或残留状态。
6. 1440px 和窄屏下 Tab 可读、可滚动，键盘焦点和 aria-selected 正确。

## Task 1: 建立共享 Workbench Tab 与旧路由映射契约

**Files:**

- Create: frontend-admin/src/components/workbench/useWorkbenchTab.ts
- Create: frontend-admin/src/components/workbench/WorkbenchShell.tsx
- Create: frontend-admin/src/lib/workbenchRoutes.ts
- Test: frontend-admin/src/components/workbench/useWorkbenchTab.test.ts
- Test: frontend-admin/src/lib/workbenchRoutes.test.ts
- Test: frontend-admin/src/components/workbench/WorkbenchShell.test.tsx

**Interfaces:**

- useWorkbenchTab<TabId>(tabIds, defaultTab) 消费 ReadonlyArray<TabId> 和当前 URL 的 tab，产生 { tab, selectTab }；selectTab 通过 router.push 更新查询参数。
- resolveWorkbenchTab<TabId>(value, tabIds, defaultTab) 为无 React 的白名单/默认值纯函数，供单元测试和 Hook 共用。
- workbenchRoutes 维护四个工作台的 pathname、合法 Tab 和默认 Tab；buildWorkbenchUrl(workbench, tab, searchParams) 只覆盖 tab，其余查询参数原样编码保留。
- WorkbenchShell 只消费标题、说明、Tab 声明、当前 Tab 和切换回调，渲染 role="tablist"、role="tab"、aria-selected、可见焦点样式和统一内容滚动容器。

**Steps:**

- [ ] 先在 useWorkbenchTab.test.ts 写缺失值、合法值、非法值和路由变化的失败测试；在 workbenchRoutes.test.ts 写四组旧路由映射、查询参数保留、旧 tab 覆盖和 URL 编码测试；在 WorkbenchShell.test.tsx 写 Tab 角色、选中态、点击调用和键盘焦点测试。
- [ ] 运行 npm test -- --run src/components/workbench/useWorkbenchTab.test.ts src/lib/workbenchRoutes.test.ts src/components/workbench/WorkbenchShell.test.tsx，确认测试因文件/接口不存在而失败。
- [ ] 实现共享类型、白名单解析、URL 构造和 Shell；Shell 不引入任何业务 API，不在组件内集中管理面板数据。
- [ ] 重跑上述目标测试并修复类型或可访问性问题；再运行 npx tsc --noEmit 确认共享类型可被 App Router 使用。
- [ ] 复查 git diff 只包含本任务的新文件；如文件独立干净，使用路径限定提交 git add -A -- <paths> 与 git commit -m "feat(admin): add workbench tab contracts" -- <paths>。

## Task 2: 抽取选品漏斗面板并接入工作台

**Files:**

- Create: frontend-admin/src/components/workbench/selection/SelectionFunnelPanel.tsx
- Test: frontend-admin/src/components/workbench/selection/SelectionFunnelPanel.test.tsx
- Modify: frontend-admin/src/app/selection-funnel/page.tsx
- Modify: frontend-admin/src/components/workbench/selection/SelectionWorkbench.tsx（如 Task 3 提前创建则只补漏斗分支）

**Interfaces:**

- SelectionFunnelPanel 无业务新 props，继续消费现有 selectionFunnelService 和上传/候选池组件；如需外层布局，使用统一面板 class，不再输出独立页面标题和页面级滚动容器。
- 面板必须保留商品榜、关键词榜、差评上传、赛道画像、候选池、分类筛选和痛点展开的原交互与错误态。

**Steps:**

- [ ] 从现有 selection-funnel/page.tsx 识别可观察行为，在测试中覆盖服务请求参数、上传入口、候选池渲染和空/错状态；先将测试指向待创建的面板并运行确认失败。
- [ ] 把页面主体搬到 SelectionFunnelPanel，仅移除独立路由的最外层标题/滚动包裹；不得改 API 参数、排序、上传流程或 Toast 文案语义。
- [ ] 让旧 selection-funnel/page.tsx 成为兼容跳转页（具体映射在 Task 3 统一接线），并运行面板测试、相关现有测试和 npx tsc --noEmit。
- [ ] 检查 diff，确认没有把其它会话对旧页面的改动覆盖；独立文件按路径提交。

## Task 3: 完成选品工作台、决策面板和旧路由兼容

**Files:**

- Create: frontend-admin/src/components/workbench/selection/SelectionDecisionPanel.tsx
- Create: frontend-admin/src/components/workbench/selection/SelectionWorkbench.tsx
- Create: frontend-admin/src/app/selection-workbench/page.tsx
- Test: frontend-admin/src/components/workbench/selection/SelectionDecisionPanel.test.tsx
- Test: frontend-admin/src/components/workbench/selection/SelectionWorkbench.test.tsx
- Modify: frontend-admin/src/app/selection-funnel/page.tsx
- Modify: frontend-admin/src/app/selection-decision/page.tsx
- Modify: frontend-admin/src/app/selection-decision/[id]/page.tsx

**Interfaces:**

- SelectionDecisionPanel 保留决策参数、任务提交、3 秒轮询、状态列表和 /selection-decision/[id] 报告详情链接；组件卸载时清理定时器/请求。
- SelectionWorkbench 声明 funnel|decision 两个 Tab，默认 funnel，按当前 Tab 只挂载一个面板，标题为“选品工作台”。
- /selection-workbench 接受 ?tab=funnel|decision；/selection-funnel 和 /selection-decision 兼容跳转并保留其它查询参数。

**Steps:**

- [ ] 先为决策面板写任务提交、轮询清理、状态列表和详情链接测试；为工作台写默认 Tab、Tab 切换只挂载一个面板、非法 Tab 回退测试；运行目标测试确认失败。
- [ ] 抽取 selection-decision/page.tsx 主体为 SelectionDecisionPanel，保持原 polling interval、请求 payload 和错误处理；先用 fake timer/现有 mock 证明卸载清理，再完成实现。
- [ ] 新增 SelectionWorkbench 和 /selection-workbench/page.tsx，使用共享 Shell/Hook；将两个旧列表页改为 router.replace 兼容页。
- [ ] 将决策详情页的“返回列表/面包屑”改为 /selection-workbench?tab=decision，不改变详情请求和权限处理。
- [ ] 运行选品目标测试、npm test -- --run 相关页面测试和 npx tsc --noEmit；复查不会出现两个轮询实例。

## Task 4: 抽取知识库五个面板并接入知识库工作台

**Files:**

- Create: frontend-admin/src/components/workbench/knowledge/DocumentsPanel.tsx
- Create: frontend-admin/src/components/workbench/knowledge/PendingReviewPanel.tsx
- Create: frontend-admin/src/components/workbench/knowledge/UploadFailuresPanel.tsx
- Create: frontend-admin/src/components/workbench/knowledge/KeywordsPanel.tsx
- Create: frontend-admin/src/components/workbench/knowledge/OperationsPanel.tsx
- Create: frontend-admin/src/components/workbench/knowledge/KnowledgeWorkbench.tsx
- Create: frontend-admin/src/app/knowledge/workbench/page.tsx
- Test: frontend-admin/src/components/workbench/knowledge/KnowledgeWorkbench.test.tsx
- Test: frontend-admin/src/components/workbench/knowledge/PendingReviewPanel.test.tsx
- Test: frontend-admin/src/lib/workbenchRoutes.test.ts（补知识库参数映射）
- Modify: frontend-admin/src/app/knowledge/page.tsx
- Modify: frontend-admin/src/app/knowledge/documents/page.tsx
- Modify: frontend-admin/src/app/knowledge/pending/page.tsx
- Modify: frontend-admin/src/app/knowledge/upload-failures/page.tsx
- Modify: frontend-admin/src/app/knowledge/keywords/page.tsx
- Modify: frontend-admin/src/app/knowledge/operations/page.tsx
- Modify: frontend-admin/src/app/knowledge/operations/traces/[id]/page.tsx

**Interfaces:**

- 五个面板分别承接原页面请求和写操作；DocumentsPanel 保留上传、重建索引、删除、详情/血缘链接，OperationsPanel 保留日志和操作详情链接。
- PendingReviewPanel 内部继续使用现有 admin 级 RoleGate；知识库工作台沿用现有内容入口的 editor 级门槛，不在 Shell 新增权限判断。
- KnowledgeWorkbench 声明 documents|pending|failures|keywords|operations，默认 documents，每次只挂载当前面板。
- 旧 /knowledge、五个旧列表 URL 均跳转到 /knowledge/workbench?tab=...，透传非 tab 参数；操作详情返回 /knowledge/workbench?tab=operations。

**Steps:**

- [ ] 逐个为知识库工作台写面板可见性测试，优先补 PendingReviewPanel 的 RoleGate 测试和 KnowledgeWorkbench 的五 Tab/单面板挂载测试；运行确认失败。
- [ ] 先抽取文档、待复核、入库失败、词库和操作日志主体；移除各自页面级滚动/标题，保留表格、分页、上传、删除、重试、错误态、Toast 和现有 API 调用。
- [ ] 将待复核原页面的门禁代码完整放入 PendingReviewPanel 或现有合适的权限边界，确保工作台切 Tab 不绕过 admin 保护。
- [ ] 新增 /knowledge/workbench 和 KnowledgeWorkbench，将旧页面改成兼容 redirect；更新操作详情返回和面包屑。
- [ ] 运行知识库目标测试、现有 knowledge 相关测试和 npx tsc --noEmit；检查切换 Tab 后没有重复加载或两个页面级滚动条。

## Task 5: 抽取评测结果、评测集治理和反馈候选

**Files:**

- Create: frontend-admin/src/components/workbench/evaluation/EvaluationResultsPanel.tsx
- Create: frontend-admin/src/components/workbench/evaluation/DatasetGovernancePanel.tsx
- Create: frontend-admin/src/components/workbench/evaluation/FeedbackCandidatesPanel.tsx
- Create: frontend-admin/src/components/workbench/evaluation/EvaluationCenter.tsx
- Create: frontend-admin/src/app/evaluations/center/page.tsx
- Test: frontend-admin/src/components/workbench/evaluation/EvaluationCenter.test.tsx
- Test: frontend-admin/src/components/workbench/evaluation/DatasetGovernancePanel.test.tsx
- Modify: frontend-admin/src/app/evaluations/page.tsx
- Modify: frontend-admin/src/app/evaluations/datasets/page.tsx
- Modify: frontend-admin/src/app/evaluations/feedback/page.tsx
- Preserve without weakening: frontend-admin/src/app/evaluations/layout.tsx

**Interfaces:**

- EvaluationResultsPanel 保留原评测历史、指标趋势、运行详情入口；DatasetGovernancePanel 继续组合 DatasetCatalog、DatasetReviewQueue、DatasetVersionDetail、EvaluationRunDetail；FeedbackCandidatesPanel 保留候选审核操作。
- EvaluationCenter 声明 results|datasets|feedback，默认 results，继续位于 /evaluations 路由段内，从而继承现有 admin layout。
- 旧 /evaluations、/evaluations/datasets、/evaluations/feedback 兼容跳转到中心 Tab，不改变评测 API 和写操作审批。

**Steps:**

- [ ] 先为中心 Tab 白名单、旧 URL 映射、评测集子组件挂载和写操作入口写失败测试；确认 layout 仍包住中心路由。
- [ ] 抽取三个面板，保留现有 loading/error/empty 状态和子路由详情行为；壳不接管评测状态。
- [ ] 新增 /evaluations/center 并把三个旧列表页变为 replace 兼容页；保留 /evaluations/layout.tsx 的 admin RoleGate，不把敏感权限下沉为仅前端隐藏。
- [ ] 运行评测目标测试、现有 evaluation 测试和 npx tsc --noEmit；确认切 Tab 不重复触发评测列表请求。

## Task 6: 抽取 Trace、网关和 Token 面板并接入运行监控工作台

**Files:**

- Create: frontend-admin/src/components/workbench/observability/TracesPanel.tsx
- Create: frontend-admin/src/components/workbench/observability/GatewayPanel.tsx
- Create: frontend-admin/src/components/workbench/observability/TokensPanel.tsx
- Create: frontend-admin/src/components/workbench/observability/MonitoringWorkbench.tsx
- Create: frontend-admin/src/app/observability/monitoring/page.tsx
- Test: frontend-admin/src/components/workbench/observability/MonitoringWorkbench.test.tsx
- Test: frontend-admin/src/components/workbench/observability/TracesPanel.test.tsx
- Modify: frontend-admin/src/app/observability/page.tsx
- Modify: frontend-admin/src/app/observability/traces/page.tsx
- Modify: frontend-admin/src/app/observability/gateway/page.tsx
- Modify: frontend-admin/src/app/observability/tokens/page.tsx
- Modify: frontend-admin/src/app/observability/traces/[id]/page.tsx

**Interfaces:**

- TracesPanel 继续消费现有筛选、比较、列表、下钻和查询参数；切换到工作台时保留 has_tool、workflow_name、时间和分页等参数。
- GatewayPanel 和 TokensPanel 保留现有图表、明细、错误态和筛选请求；不把 Tool、告警或报告列表并入工作台。
- MonitoringWorkbench 声明 traces|gateway|tokens，默认 traces，每次只挂载一个面板；Trace 详情地址保持 /observability/traces/[id]。
- /observability、/observability/traces、/observability/gateway、/observability/tokens 兼容跳转到 /observability/monitoring?tab=...；详情返回指向 tab=traces 并保留必要筛选。

**Steps:**

- [ ] 先补 Trace 参数透传、比较入口、Token 图表渲染和工作台 Tab 测试，运行确认失败。
- [ ] 抽取三面板，重点保留 Trace 的请求取消/筛选状态、Token 图表动态加载和网关安全错误处理；移除重复页面标题及外层滚动，不改数据请求协议。
- [ ] 新增监控工作台路由和旧 URL replace 兼容页；更新 Trace 详情返回/面包屑。
- [ ] 运行 observability 目标测试、现有相关测试和 npx tsc --noEmit；用 fake timer 或请求 mock 验证切 Tab 后没有残留轮询/重复请求。

## Task 7: 迁移导航、首页和跨页入口，更新管理端设计文档

**Files:**

- Modify: frontend-admin/src/components/layout/navConfig.tsx
- Modify: frontend-admin/src/components/layout/navConfig.test.ts
- Modify: frontend-admin/src/app/page.tsx
- Modify: frontend-admin/src/app/tools/page.tsx
- Modify: frontend-admin/src/app/alerts/[id]/page.tsx
- Modify: frontend-admin/src/app/reports/[id]/page.tsx
- Modify: frontend-admin/src/app/observability/selection-funnel/page.tsx（仅检查/修正跳转，不并入工作台）
- Modify: docs/DESIGN.md
- Test: frontend-admin/src/lib/workbenchRoutes.test.ts（补跨页链接参数断言，或新增 src/app/workbenchLinks.test.ts）

**Interfaces:**

- 侧栏只暴露：选品工作台、知识库工作台、评测中心、运行监控工作台；运行中心其它告警/任务入口和系统设置保持原位置。
- Dashboard 待办、Tool 治理、告警、报告等列表入口改为新工作台 URL；Trace 相关链接统一为 /observability/monitoring?tab=traces，必要筛选查询参数不丢失。
- 详情页仍保留原路径，只有列表/返回/面包屑指向新工作台。
- docs/DESIGN.md 只更新管理端页面清单、分组和四个工作台 URL，不手抄后端能力/工具数量。

**Steps:**

- [ ] 先更新 navConfig.test.ts：断言四个工作台入口、分组、角色字段和旧列表入口不再暴露；补跨页 URL 的失败断言。
- [ ] 更新导航配置和所有已检出的跨页硬编码链接；用 rg -n "(/selection-funnel|/selection-decision|/knowledge/(documents|pending|upload-failures|keywords|operations)|/evaluations/(datasets|feedback)?|/observability/(traces|gateway|tokens))" frontend-admin/src 扫描残留，并逐个判断详情路径、兼容路由或需要迁移的列表入口。
- [ ] 更新 docs/DESIGN.md 的管理端页面清单；不动与当前会话无关的设计文档变更。
- [ ] 运行导航、工作台路由和受影响页面测试；检查旧路径仅在兼容页、详情页或明确的业务上下文中存在。

## Task 8: 集成验收、手工回归与交付检查

**Files:**

- Modify only files 发现为真实问题的工作台/测试/设计文档文件；不为验收生成临时代码。

**Steps:**

- [ ] 在 frontend-admin 运行 npm test，确认完整 Vitest 通过；失败时按失败测试定位，不扩大修改范围。
- [ ] 运行 npx tsc --noEmit，确认 App Router、客户端 Hook、RoleGate 和组件 props 无类型错误。
- [ ] 运行 git diff --check，再用 git diff --stat 和逐文件 git diff 检查没有意外改动；特别确认没有把其它会话的 backend/data/frontend 改动加入本任务。
- [ ] 使用现有 devctl.bat status 确认管理端服务状态；在可用浏览器会话中手工验证四个新 URL、默认/非法 Tab、Tab 前进后退、旧 URL 跳转、权限门禁、详情返回、首页/Tool/Report/告警入口、1440px 和窄屏 Tab 布局。
- [ ] 对选品任务轮询、Trace 查询和 Token 图表观察切 Tab 后网络请求/定时器没有重复实例；记录无法进行的浏览器验收项及原因，不以未验证冒充通过。
- [ ] 仅路径限定提交本功能独立文件；提交前再次确认 git status --short，最终报告列出测试命令和实际结果，并说明任何未完成的手工验收。

## Completion Checklist

- [ ] 四个工作台新路由可直接打开，缺失/非法 Tab 有默认行为。
- [ ] 旧列表 URL 全部 replace 到正确工作台 Tab，并保留业务查询参数。
- [ ] 选品、知识库、评测、监控面板复用原请求和权限逻辑，无重复实现。
- [ ] 详情页、首页、Tool/Report/告警等跨页入口已切换到工作台上下文。
- [ ] 侧栏不再显示旧列表入口，四个工作台分组和角色正确。
- [ ] npm test、npx tsc --noEmit、git diff --check 有实际通过证据。
- [ ] 工作区中其它会话的改动未被覆盖、未被混入本功能提交。
