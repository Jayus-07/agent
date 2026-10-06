# DESIGN — 前端三端设计规范

> 用户端 `frontend`（:3100）· 管理端 `frontend-admin`（:3200）· 客服坐席端 `frontend-cs`（:3300）的统一设计规范。
> 最后验证：2026-10-07 · 基于三端代码逐目录 survey（token 色值 / 组件路径 / 状态表现均实测）；本次同步 /travel 三栏形态、管理端侧栏二级标题、AI 身份告知。2026-10-07 增量：用户端 +`/selection-funnel` 第四扇门、`HandoffCard` 域引导卡、`ReplyBadge` 回复归因徽章、`MarkdownContent` 宽表/H1 排版契约（多域隔离 M3-M4 `bb3b3df`、回复呈现一期 `3af6f54`）。
> 历史依据：[2026-09-17-UX体验架构设计.md](2026-09-17-UX体验架构设计.md)（三段式错误反馈 / 等待体验 / 空态体系的原始规范）、[2026-09-16-前端拆分计划.md](2026-09-16-前端拆分计划.md)（三端拆分背景）。

---

## 1. 设计系统总则

**「AI Native — 极致简约现代主义商务风」**，三端同源：

- **技术栈三端完全同构**：Next.js 14（App Router）+ React 18 + TypeScript + Tailwind CSS 3.4 + @tanstack/react-query 5 + zustand 5 + lucide-react 图标 + recharts 图表 + react-markdown。**零第三方 UI 组件库**（无 antd / mui / shadcn），全部自研。
- **三端是「复制分叉」关系，无共享包**：`globals.css` 与 `tailwind.config.ts` 在用户端与坐席端逐字节相同，管理端同一套 token。改设计 token 必须三端同步。
- **唯一样式方案 = Tailwind 原子类 + CSS 变量语义 token**（`text-text-primary`、`bg-surface-*`、`bg-accent`），无 css modules / styled-components。
- **纯浅色主题**：不支持暗色模式（tailwind 无 darkMode 配置）。
- **样式事实源**：各端 `src/app/globals.css` + `tailwind.config.ts`。改色板先改这里，不要在组件里写原生色值。

## 2. 设计 Token

### 2.1 主色与中性色（三端一致）

| Token | 值 | 用途 |
|---|---|---|
| `accent` | `#4D6BFE`（hover `#3b54d4`，soft 8% 透明底） | 发送键、链接、focus 边框、选中态、typing-dot |
| 背景 root / surface / elevated / hover | `#fafbfc` / `#ffffff` / `#f8f9fb` / `#f3f4f6` | 页面底 / 卡片 / 浮层 / 悬停 |
| 文字 primary / secondary / muted | `#1a1a2e` / `#6b7280` / `#9ca3af` | 三级文字 |
| 边框 subtle | `#e5e7eb` | 卡片与分割线 |
| 用户气泡 | `#f3f4f6`（右侧） | AI 回答无背景左对齐（见 §7.2） |

### 2.2 门户 / 登录页三端识别色（例外色系）

统一门户 `/` 与各端登录页用「分屏 + 毛玻璃卡 + 径向雾团背景」，每端一个识别色，进入后色调延续：

| 端 | 识别色 |
|---|---|
| 用户端 | 绿 `#1F7A4D` |
| 客服坐席端 | 蓝 `#2C6E9E` |
| 管理端 | 石墨 `#2E333A` |

### 2.3 字体 / 形状 / 动效

- **字体**：系统栈（`-apple-system, 'Segoe UI', 'Inter', Roboto`），无 webfont；正文 15px，markdown 0.9375rem / 行高 1.6
- **圆角**：输入框与卡片 `rounded-2xl`，消息气泡 `rounded-xl`，按钮胶囊 `rounded-full`，代码块 10px
- **阴影**：仅两层轻阴影（card 0.04 黑；input 带 accent 8% 蓝晕）；禁用重度投影
- **动画**：仅 fadeIn 上浮（0.25s）、typing-dot；**玻璃拟态弃用 `backdrop-filter`**（GPU 开销大收益≈0，用 92% 纯白底替代）
- **滚动条**：4px、悬停才显形
- **代码块**：深底 `#1a1a2e` + 自定义 highlight.js 浅色语法配色

## 3. 三端布局骨架

| 端 | 布局形态 | 导航事实源 | 守卫 |
|---|---|---|---|
| 用户端 | **三形态按路由切换**：`/agent` 任务模式（页面自渲染 252px TaskSidebar + ChatHeader）；`/` `/login` `/register` 独立页；其余（`/travel`）全局 Sidebar 控制台。移动端 ≤768px 底部 tab | `src/components/layout/navConfig.tsx` | AuthGate 挂 root layout，公开页白名单放行，失败弹回门户 `/` |
| 管理端 | 经典控制台：固定左侧栏（可折叠 14px 图标栏，glass 半透明）+ 右侧内容区（PageHeader + 筛选栏 + 表格/卡片） | `src/components/layout/navConfig.tsx`（自称单一数据源，五组菜单 + minRole） | AuthGate（入口门槛 admin）+ 页面级 RoleGate（8 处，角色不足渲染页内 ForbiddenCard 保留导航上下文） |
| 坐席端 | 控制台骨架同管理端；核心页 `/cs/handoff` 为 `grid-cols-5`：左 2 列队列（待接单 offer + 排队列表）+ 右 3 列会话聊天窗 | `src/components/layout/navConfig.tsx`（5 项，满意度统计仅 supervisor 可见） | AuthGate + RoleGate（csRole） |

**权限模型**（三端一致）：`viewer(0) < editor(1) < admin(2) < super_admin(3)`；菜单显隐（minRole）+ 页面 RoleGate + 后端 403 兜底——**UI 呈现不是真闸**。坐席端另有 `csRole` 坐席名单制。

## 4. 页面清单（2026-10-02 实测）

**用户端**（7 页，2026-10-07 实测）：`/` 统一门户（三端入口卡）· `/login` / `/register`（注册成功自动登录）· `/agent` 聊天主界面（`?cs=1` 客服抽屉、`?session=` 深链恢复）· `/travel` 三栏旅游控制台——左栏行程条件（TaskSidebar travel 模式：历史规划列表 + 需求摘要锁定），中栏行程画布（逐日时间轴 + 地图打点 + ICS 导出；生成中以 GeneratingCard 占位、真实美食/酒店/车次 Tool 结果按 SSE 动态进聊天流），右栏「旅行助手」页内对话式改单（复用同一会话，工具条收敛摘要 + 结果卡横向流 + 排队槽）· `/selection-funnel` 选品漏斗专属页（「四扇门」之一，2026-10-06 `bb3b3df`：类目/平台带参直达域图，HandoffCard guide 引导的归宿之一）；全局 Sidebar 控制台形态，另有 CityGuideDrawer 城市指南抽屉、方案档位切换器与预算协商卡；失败不以演示数据替代。

**管理端**（五组菜单，工作台承载高关联页面）：运营总览 `/` · 业务运营（`/data-explorer` / `/competitors` / `/selection-workbench`〔选品漏斗、选品决策 Tab〕/ `/reports`）· 内容与质量（`/knowledge/workbench`〔文档、待复核、入库失败、词库、操作日志 Tab〕/ `/evaluations/center`〔评测结果、评测集治理、反馈候选 Tab〕）· 运行中心（`/tasks` / `/observability/monitoring`〔问答追踪、网关安全、Token 用量 Tab〕/ `/security` / `/observability/alerts` / `/alerts` / `/schedules`）· 系统设置（`/prompts` / `/agents` / `/skills` / `/tools` / `/consistency` / `/releases` / `/cost-governance/budgets` / `/settings/models` / `/approvals` / `/settings/access`）。旧列表 URL 保留兼容重定向并参与当前菜单高亮，不再作为侧栏入口；侧栏同组页面通过“业务洞察 / 选品运营 / 业务产出 / 知识内容 / 评测治理 / 运行状态 / 告警与安全 / 自动化 / AI 资产 / 治理与发布 / 安全与权限”等二级标题区分。

**坐席端**（7 页）：`/cs` 工作台首页 · `/cs/handoff` 人工接入（队列 + offer + 聊天接管）· `/cs/conversations`（+`/[id]` 详情含逐消息 trace 双栏）· `/cs/tickets` 工单 · `/cs/stats` 满意度统计与质检日报 · `/login`

## 5. 组件规范

### 5.1 四态基础组件（`src/components/shared/`，三端同名同职责）

| 组件 | 规范 |
|---|---|
| `Toast.tsx` | 自研 ToastProvider + useToast，info/success/warning/error 四级，右上角；成功 3s / 错误 5s；layout 顶层挂一次 |
| `Skeleton.tsx` | 表格 / 列表加载骨架（rows×cols pulse 方块），**不许用裸 spinner 替代表格骨架** |
| `EmptyState.tsx` | 统一空态（`no_data` 📭 / `under_construction` 🚧）。**硬约束：必须给 actionHref 或 onAction 出路，禁止死白板、禁止写假数据** |
| `ErrorState.tsx` / `ErrorCard.tsx` | 统一错误态：红三角 + 错误码 + 重试按钮；ErrorCard 三段式（发生了什么 → 我能做什么 → 详情折叠），文案经 `src/api/errors.ts` 的 `describeApiError` 归一，**不渲染原始报错** |
| `ForbiddenCard.tsx` | 页内 403 卡片（配合 RoleGate，不整页跳转） |

### 5.2 用户端聊天核心组件（`frontend/src/components/chat/`）

`ChatView`（编排：错误卡/预算条/澄清卡/上下面板/消息流/输入框）→ `MessageList` + `MessageBubble`（DeepSeek 式非对称气泡，memo 拦截重渲；内嵌 `ReplyBadge` 回复归因徽章——done 帧 `reply_source` 四语义码中文映射「基于知识库回答/基于业务数据分析/基于实时数据查询/系统提示」，2026-10-05 `3af6f54`）→ `StreamingContent`（SSE delta rAF 节流逐字 + 独立光标 + 首 token 前 typing-dot）+ `ThinkingPanel`（已思考 N 秒折叠）+ `ProgressCards`（消息流内 bare 形态进度：节点 · token 消耗）+ `SourceCard`（引用来源彩色小片，按 doc_type 上色）+ `HandoffCard`（域引导交接卡，2026-10-06 `bb3b3df`：消费 SSE `handoff` AUX 帧，三入口带参跳转——旅游页预填 / 选品页带参 / CSDrawer 预填，点击埋点 `POST /observability/handoff/click`）+ `ChatInput` + `ComposerToolbar`（两段式输入）。

`MarkdownContent` 排版契约（同批 `3af6f54`）：表格包 `.table-scroll` 横向滚动容器、不撑破气泡；消息流内 H1 降级为 H2 渲染（正文从 H2 起排）。

### 5.3 已知缺口（新页面不得效仿，应收敛到规范）

- **语义色 / 状态徽章无统一 token**（各页内联红黄蓝绿 hex）——待建 `StatusBadge` 统一 token（prompts/StatusBadge.tsx 的 STATUS_META 映射是现成范式）
- **无共享 Modal / 分页组件**：弹窗约 10 处手写 `fixed inset-0` 遮罩；分页两种模式并存（服务端 PAGE_SIZE=20 vs PAGE_SIZES=[20,50,100] 下拉）
- 破坏性确认仍有多处 `window.confirm`，应换统一确认弹窗

## 6. 交互状态规范（四态）

| 状态 | 规范 | 参照实现 |
|---|---|---|
| **加载中** | 路由级 `app/loading.tsx` spinner；列表/表格用 Skeleton；流式生成不在流外显示状态行——进度由消息流内 ProgressCards 一行承载 | frontend `chat/ProgressCards.tsx` |
| **空数据** | EmptyState（必须有出路）；聊天空态 = WelcomeState 大标题 + 能力胶囊（点击直发示例问题），与输入框同组居中 | frontend `chat/WelcomeState.tsx` |
| **错误** | ErrorCard 三段式 + 重试；轻通知走 Toast；表单行内红字；错误语义一律 `describeApiError` 归一 | `src/api/errors.ts` |
| **通知 / 实时** | 坐席端转人工四路触达范式：toast 堆栈（可点击定位）+ 提示音（可静音持久化）+ 桌面 Notification（仅页面不可见时）+ `document.title` 角标；实时通道徽章（WS 实时推送 / 轮询降级）直观可见 | frontend-cs `cs/handoff/page.tsx` + `lib/csAgentWs.ts` |

## 7. 用户端聊天独特约定（已确认为代码事实，新功能不得违背）

1. **发送键兼任停止键**：生成中同一颗按钮变停止图标（深灰底），随时可点不置灰（`agent/ComposerToolbar.tsx`）
2. **消息流内不放卡片、行内纯文字**：AI 回答无气泡背景全宽渲染；生成中进度以 bare 无卡片形态嵌入且流结束自动消失（`chat/MessageBubble.tsx`、`MessageList.tsx`）
3. **引用不用 `[1]` 上标**：正文剪掉「参考文献」段，由正文下方 SourceCard 彩色小片承载（按 doc_type 上色 + 相关度等宽小字）
4. **时间双轨**：会话列表相对时间分组（刚刚/N 分钟前/今天/昨天/最近 7 天/更早，`lib/session-groups.ts`）；精确时刻 `toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })`；token 数 `toLocaleString()` + `tabular-nums`
5. **输入上限静默引导**：字数计数器接近上限才出现，超限红字引导走知识库上传而非硬报错（`lib/chatInputLimit.ts`）
6. **免责声明常驻**：输入框下方固定 10px 灰字「答案由 AI 生成，请核实关键信息」
7. **AI 身份告知（C12，2026-10-03）**：客服抽屉内 assistant 气泡自带「AI 客服 · 由人工智能生成」小字标识（hover 提示「本条回复由人工智能生成，仅供参考」，`cs/CSMessageBubble.tsx`）；客服欢迎页「AI 智能客服」徽标 + 免责说明（`cs/CSWelcome.tsx`）；转人工入口与会话头部常驻标识此前已有
8. **反馈静默化**：赞/踩 hover 才出现，点踩展开原因表单；提交类按钮变底色即确认、不可重复提交
9. **代码块悬浮复制**：hover 显形「复制/已复制」2s 回弹
10. **性能即设计**：普通气泡 memo 不订阅流式字段、组件级 store 订阅、rAF 节流、`compress: false` 保 SSE 打字机逐 chunk
11. **坐席辅助面板**：AI 推荐回复只填入输入框，**绝不自动发送**（frontend-cs `agent/AgentAssistPanel.tsx`）

## 8. 反模式清单（「不要做」）

- ❌ `backdrop-filter` 玻璃拟态（GPU 开销大收益≈0）
- ❌ 对授权零作用的「误导 UI」（部门选择器已因此移除）
- ❌ 消息流内双状态并存（错误状态行必须合并为一条）
- ❌ 空态死白板 / 写假数据
- ❌ 组件内写原生色值（用语义 token；现存违例见 §9）

## 9. 已知不一致（待修台账）

| 项 | 位置 | 问题 |
|---|---|---|
| `/travel` 页未用语义 token | `frontend/src/app/travel/` | 直接写 `blue-600`/`gray-200` 原生色 |
| MobileTabBar 死链 | `frontend/src/components/layout/MobileTabBar.tsx` | 4 tab 中 3 个指向已删除路由（/agent/tasks、/reports、/alerts） |
| 坐席端用色分叉 | `/cs/handoff`、`/cs/tickets` | 用原生 slate/blue/emerald 色板，与 `/cs` 系列语义 token 不一致 |
| 拆分遗留死代码 | frontend-cs `components/chat|agent` 大部分、layout `/agent` 特判、`not-found.tsx` 管理端口吻 | 三端拆分快照未清理，**不构成现状规范** |
| 管理端遗留 chat/agent 组件 | frontend-admin 同类 | `/agent` 路由已删但组件保留 |

---

## 旅游助手天数选择（v3 P0-A）

`TravelChatDrawer` 在直接规划缺天数的回复后，展示「按 3 天参考规划／自己填天数」。接受参考值以完整用户消息发起一次请求；自填选项聚焦天数输入。请求中与存在待应用预览时禁用选项；发起新消息或切换会话后旧选项失效，停止后的迟到结果不能恢复旧选项。问答回复显示旅行建议，不展示行程启动或应用卡。复用现有旅游页样式，不新增设计 token。

最后验证：2026-10-02 · 见 [P0-A 收尾验收](reports/2026-10-02-旅游灵感式规划v3-P0-A收尾验收.md)。

## 维护约定

- 改设计 token → 三端 `globals.css` + `tailwind.config.ts` 同步改，改完跑三端 `npx tsc --noEmit` + `npm test`
- 新页面必须复用 §5.1 四态组件，空态必须有出路
- 发现新的独特约定或反模式 → 本文档 §7/§8 增补；本文档随前端代码一同演进，最后验证日期不得落后于结构性 UI 变更
