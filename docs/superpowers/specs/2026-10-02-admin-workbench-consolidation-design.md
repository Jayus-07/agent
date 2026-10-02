# 管理端工作台页面合并设计

## 目标

将管理端中按技术页面拆开的入口，收敛为四个按工作任务组织的工作台，并用 URL Tab 保持可分享、可回退和可兼容旧书签：

1. 选品工作台：选品漏斗 + 选品决策；
2. 知识库工作台：文档、待复核、入库失败、词库、文档操作日志；
3. 评测中心：评测结果、评测集治理、反馈候选；
4. 运行监控工作台：问答追踪、网关安全、Token 用量。

页面原有 API、权限和详情页继续复用，不在本次引入新的后端接口。

## 成功标准

- 侧栏只展示四个工作台入口，不再展示工作台内部的旧页面入口；
- 每个工作台通过 `?tab=` 直接定位到一个可分享的子页面；
- 旧 URL 访问后自动跳转到对应工作台 Tab，旧书签不失效；
- Tab 切换不触发整页刷新，浏览器前进/后退可以恢复 Tab；
- 详情页、首页待办、Tool/Report 等跨页链接仍能进入正确的详情或列表上下文；
- 现有角色门禁继续生效，尤其是知识库待复核、评测中心和管理操作；
- 不重复实现数据请求逻辑，不新增“只为工作台存在”的后端数据源。

## 范围与非范围

### 本阶段包含

- 新增四个工作台路由和共享 Tab 壳；
- 将现有页面主体抽取为可嵌入的面板组件；
- 旧列表 URL 的兼容跳转；
- 侧栏导航改为工作台入口；
- 详情页返回链接和管理端首页快捷入口改到新工作台；
- 面板级 URL 状态、权限和回归测试；
- 同步管理端设计文档中的页面清单。

### 本阶段不包含

- 不修改后端 API、数据库或业务计算逻辑；
- 不合并选品报告详情、Trace 详情、文档操作详情为 Tab；
- 不把竞品监控、报告中心、告警中心、安全运营、定时任务并入本次四个工作台；
- 不改变角色定义、接口权限或原有危险操作审批流程；
- 不删除旧路由文件，旧路由仅承担兼容跳转职责。

## 路由与兼容策略

### 新工作台路由

```text
/selection-workbench?tab=funnel|decision
/knowledge/workbench?tab=documents|pending|failures|keywords|operations
/evaluations/center?tab=results|datasets|feedback
/observability/monitoring?tab=traces|gateway|tokens
```

缺失或非法 `tab` 时使用各工作台的首个 Tab：

```text
selection-workbench -> funnel
knowledge/workbench -> documents
evaluations/center -> results
observability/monitoring -> traces
```

Tab 切换使用 `router.push` 更新查询参数，使浏览器前进/后退可以恢复 Tab；直接打开旧 URL 时使用 `router.replace` 或服务端 `redirect` 进入新路由，避免兼容跳转污染历史。工作台内部的 Tab 链接保留其它与面板有关的查询参数，例如 Trace 的 `has_tool`、`workflow_name` 和时间筛选。

### 旧 URL 映射

```text
/selection-funnel              -> /selection-workbench?tab=funnel
/selection-decision            -> /selection-workbench?tab=decision
/knowledge                     -> /knowledge/workbench?tab=documents
/knowledge/documents            -> /knowledge/workbench?tab=documents
/knowledge/pending              -> /knowledge/workbench?tab=pending
/knowledge/upload-failures      -> /knowledge/workbench?tab=failures
/knowledge/keywords             -> /knowledge/workbench?tab=keywords
/knowledge/operations           -> /knowledge/workbench?tab=operations
/evaluations                    -> /evaluations/center?tab=results
/evaluations/datasets           -> /evaluations/center?tab=datasets
/evaluations/feedback           -> /evaluations/center?tab=feedback
/observability                  -> /observability/monitoring?tab=traces
/observability/traces           -> /observability/monitoring?tab=traces
/observability/gateway          -> /observability/monitoring?tab=gateway
/observability/tokens           -> /observability/monitoring?tab=tokens
```

带有业务筛选参数的旧 URL 必须原样传递，只有 `tab` 由兼容层补齐或覆盖。以下详情路由保持原地址不跳转：

- `/selection-decision/[id]`；
- `/knowledge/operations/traces/[id]`；
- `/observability/traces/[id]`。

详情页返回列表时改为带 Tab 的工作台地址，避免回到已隐藏的旧列表入口。

## 组件设计

### 共享工作台壳

新增 `components/workbench/WorkbenchShell.tsx`，职责仅限于：

- 渲染页面标题、说明和水平 Tab；
- 从固定的 Tab 声明中校验当前 Tab；
- 通过 `router.push` 更新 `tab`；
- 给当前 Tab 提供键盘焦点样式、`aria-selected` 和 `role="tab"`；
- 为面板区域提供统一的滚动容器和最大宽度。

共享壳不负责请求数据、加载状态、Toast 或权限判断，避免把四类业务页面的行为耦合在一起。

新增 `components/workbench/useWorkbenchTab.ts`，提供：

```text
useWorkbenchTab<TabId>(tabIds, defaultTab)
  -> { tab, selectTab }
```

非法 Tab 回退到默认值；首次渲染和客户端路由变化都使用同一份 Tab 白名单，避免输入任意查询参数导致不可预期的面板渲染。

### 选品工作台

新增 `components/workbench/selection/SelectionWorkbench.tsx`，两个面板从现有路由页面抽取：

- `SelectionFunnelPanel`：保留商品榜/关键词榜/差评上传、赛道画像、候选池和痛点展开；
- `SelectionDecisionPanel`：保留决策参数、任务提交、任务轮询、状态列表和报告详情链接。

面板切换会卸载另一面板，停止不必要的轮询；报告详情仍进入 `/selection-decision/[id]`。工作台只新增统一标题和 Tab，不改变原有业务操作顺序。

### 知识库工作台

新增 `components/workbench/knowledge/KnowledgeWorkbench.tsx`，包含五个面板：

- `DocumentsPanel`；
- `PendingReviewPanel`；
- `UploadFailuresPanel`；
- `KeywordsPanel`；
- `OperationsPanel`。

待复核面板继续使用 admin 级 `RoleGate`；工作台入口沿用原知识内容的 editor 级门槛。操作日志详情仍进入 `/knowledge/operations/traces/[id]`，详情返回工作台的 `operations` Tab。

### 评测中心

新增 `components/workbench/evaluation/EvaluationCenter.tsx`，包含：

- `EvaluationResultsPanel`：原评测运行历史、指标、趋势和详情；
- `DatasetGovernancePanel`：原评测集目录、版本详情、审核队列和评测运行详情；
- `FeedbackCandidatesPanel`：原反馈候选审核。

工作台放在现有 `/evaluations` 路由段下，继续复用 `evaluations/layout.tsx` 的 admin 门禁。每个面板的请求和写操作逻辑保持原实现，禁止在壳组件里集中管理评测状态。

### 运行监控工作台

新增 `components/workbench/observability/MonitoringWorkbench.tsx`，包含：

- `TracesPanel`：原问答链路追踪列表、筛选、对比和下钻；
- `GatewayPanel`：原网关认证拒绝、限流和访问日志；
- `TokensPanel`：原 Token/成本趋势、模型细分和调用明细。

Trace 详情和其它页面传入的筛选参数必须穿透到 `TracesPanel`。Tool 治理、告警、报告等页面跳转到 Trace 列表时，目标地址统一使用 `/observability/monitoring?tab=traces`。

## 页面抽取与文件边界

现有 `app/**/page.tsx` 中的页面级数据逻辑迁移为 `components/workbench/**` 下的面板组件；原 `page.tsx` 改为：

1. 新工作台路由：读取 Tab 并渲染 `WorkbenchShell + Panel`；
2. 旧列表路由：只执行兼容跳转，不再复制面板代码；
3. 详情路由：保留原页面，只把返回/面包屑/关联列表链接切换到工作台 URL。

每个面板内部去掉原来用于独立路由的最外层滚动容器和重复的页面标题，保留筛选、表格、卡片、空态、错误态和操作反馈。这样不会出现“工作台滚动条套页面滚动条”或“标题重复”。

## 状态、权限与错误处理

- Tab 状态的唯一事实源是 URL 查询参数；本地 state 只保存面板内部的筛选和展开状态；
- 面板切换不共享业务数据 state，避免从一个面板泄漏筛选条件到另一个面板；
- 工作台壳加载失败不应吞掉面板错误，面板继续使用现有 Toast/ErrorState/EmptyState；
- 非法 Tab 使用默认 Tab，不展示空白页；
- 旧路由跳转失败时显示统一的“正在打开工作台”降级页，并允许返回运营总览；
- editor/admin/viewer 的可见性沿用当前导航和页面 RoleGate 语义，后端 403 仍是最终权限边界；
- 详情页不存在或无权限时沿用现有错误页，不在工作台壳中新增一套错误协议。

## 导航与文档

`navConfig.tsx` 的入口调整为：

- 业务运营：`选品工作台`；
- 内容与质量：`知识库工作台`、`评测中心`；
- 运行中心：`运行监控工作台`，其它告警/任务入口保持原位置；
- 系统设置不变。

旧入口不再出现在侧栏，但仍由兼容路由承接。首页待办和跨页快捷入口切换到新工作台地址；详情页返回链接同步切换。`docs/DESIGN.md` 更新管理端页面清单和工作台 URL 说明。

## 测试与验收

### 单元测试

- Tab 白名单、默认值和非法值回退；
- 旧路由到新工作台的映射，以及旧查询参数保留；
- 导航不再暴露旧列表入口，四个工作台入口和角色门槛正确；
- 详情页返回 URL 包含正确的 `tab`。

### 前端验证

```text
npm test
npx tsc --noEmit
```

### 手工验收

1. 直接打开四个新工作台，默认 Tab 正确；
2. 点击每个 Tab，地址栏更新且无整页刷新；
3. 旧 URL 逐一打开后进入正确 Tab；
4. 从首页待办、Tool 治理、报告和详情页跳转后，Tab/筛选上下文正确；
5. 待复核、评测中心等敏感页权限行为不回退；
6. 选品任务轮询、Trace 筛选和 Token 明细在 Tab 切换后没有重复轮询或残留状态；
7. 1440px 桌面和窄屏下 Tab 不溢出，键盘焦点可见。

## 风险与回滚

- 最大风险是抽取页面时误改现有请求或操作逻辑；通过“面板只搬运现有逻辑、壳不接业务 API”和逐页回归测试控制；
- 详情页链接较多，采用 `rg` 扫描旧列表 URL 并补测试，避免出现隐藏入口回到旧列表；
- 若工作台上线后发现布局问题，可先把侧栏恢复到旧列表入口，兼容跳转仍保留，不影响数据和 API；
- 回滚只需恢复工作台入口配置和新路由文件，旧路由跳转页改回原面板导出即可，不涉及数据库迁移。

