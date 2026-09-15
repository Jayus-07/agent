# /agent 任务模式改造方案 · P0

> **✅ 实施状态（2026-09-15）**：P0 已实施并提交（`5f4d9c6 feat(travel|lbs|frontend): /agent 任务模式 P0`）。
> - 12 个一级模块入口最终采用**双通道**：`layout.tsx` 在 `/agent` 下渲染折叠态全局 Sidebar（56px 模块图标条，`forceCompact` 禁止展开，快速直达）+ TaskSidebar 内「全部功能」折叠分组（可展开子菜单）。两者分工不同，并存不冗余。
> - 实际落地与本文第二节略有出入：TaskSidebar 用 `SessionList` 子组件承载列表逻辑；`RunStatusLine` 显示「本会话已消耗」（后端仅在 done 事件一次性下发 usage，实时累计待 P1）。
> - 验证：`npx tsc --noEmit` 通过，`npm run test` 124/124 通过。

> **目标**：把 `/agent` 从"控制台内嵌的对话页"改造成任务型 Agent 界面（左侧任务栏 + 居中对话流 + 两段式输入框）。
> **边界**：只动 `/agent` 一个路由。其余 11 个业务页面（知识库、客服、报告、告警、竞品、选品、定时任务、链路追踪、Prompt、评测、驾驶舱）零影响。
> **不含**：任务列表卡片、文件操作列表、实时 token 累计 —— 这三项需要扩展后端 SSE 协议，属 P1。

---

## 一、现状结构

```
RootLayout  (src/app/layout.tsx)
├── Sidebar  (w-64)                    ← 全局控制台导航，NAV 12 个业务模块，所有路由共享
└── /agent   (src/app/agent/page.tsx)
    ├── ChatHeader   (h-12)
    ├── ChatView     (flex-1)
    └── HistorySidebar (w-[260px])     ← 右侧会话历史
```

**核心问题**：对话区被左右两栏夹击 —— 左侧 256px + 右侧 260px = 516px 被占，中间留给对话的空间被严重压缩，视觉气质是"后台管理系统"而非"任务工作台"。

## 二、目标结构

```
/agent  (src/app/agent/page.tsx)
├── TaskSidebar  (w-[264px])
│   ├── 品牌 + 折叠
│   ├── 搜索框
│   ├── 「新建任务」主按钮
│   ├── 「全部功能」折叠分组           ← 收纳原 NAV 12 个模块，业务入口不丢
│   ├── 时间分组会话列表               ← 今天 / 昨天 / 最近 7 天 / 更早
│   └── 底部用户区
├── ChatHeader   (h-12)                ← 会话标题 + 分享 + 收起
└── ChatView
    ├── MessageList
    │   ├── 用户消息（去气泡、浅色块）
    │   ├── ThinkingPanel（已具备，不动）
    │   └── AI 正文 + SourceCard + TokenInfo（已具备，不动）
    ├── RunStatusLine                  ← 「生成回复中 · 已消耗 X」
    └── ChatInput（两段式）
        ├── textarea
        └── 工具栏：部门选择 · 附件 · 模型切换 · 权限 · 发送
```

`layout.tsx` 在 `/agent` 路由下**不渲染**全局 Sidebar，把整幅左侧空间让给 TaskSidebar。

---

## 三、文件改动清单

### 新增（6 个）

| # | 文件 | 说明 |
|---|---|---|
| 1 | `src/lib/session-groups.ts` | 从 HistorySidebar 抽出 `bucketOf` / `formatTime` / `groupByTime` / `BUCKET_LABELS`。**避免 380 行代码复制粘贴**，HistorySidebar 与 TaskSidebar 共用 |
| 2 | `src/components/agent/SessionRow.tsx` | 从 HistorySidebar 搬运 `SessionRow`（含重命名内联编辑、hover 删除）。样式保持，宽度自适应 |
| 3 | `src/components/agent/SessionList.tsx` | 分组渲染 + 空态 + 加载态 + 错误态 + `historyError` banner。封装 `sessions-cache` 拉取与 `sessionsVersion` 自动刷新逻辑 |
| 4 | `src/components/agent/TaskSidebar.tsx` | 截图式左侧栏主体：品牌区 / 搜索 / 新建任务 / 功能分组 / 会话列表 / 用户区 |
| 5 | `src/components/agent/RunStatusLine.tsx` | 底部状态行。P0 数据源：`isLoading` + 当前消息的 `usage.total_tokens`；P1 接实时累计 |
| 6 | `src/components/agent/ComposerToolbar.tsx` | 输入框下方工具栏：部门选择器（迁移）、附件按钮（先占位）、模型切换（迁入 LLMSwitcher）、权限开关（localStorage 占位） |

### 修改（6 个）

| # | 文件 | 改动 |
|---|---|---|
| 7 | `src/app/layout.tsx` | 引入 `usePathname()`；`pathname === '/agent'` 时不渲染全局 `Sidebar`。见下方代码 |
| 8 | `src/app/agent/page.tsx` | 装配新结构：左 TaskSidebar + 中 ChatHeader/ChatView，移除右侧 HistorySidebar |
| 9 | `src/components/chat/ChatInput.tsx` | 改为两段式：上方 textarea，下方 ComposerToolbar。**部门选择器逻辑必须保留**（决定 RAG 检索授权范围） |
| 10 | `src/components/chat/ChatHeader.tsx` | 移除 `LLMSwitcher`（下移到工具栏）；右侧加「分享」「收起」按钮 |
| 11 | `src/components/chat/MessageBubble.tsx` | 用户消息去掉 `bg-accent-soft rounded-2xl` 气泡感，改为浅灰块（`bg-surface-elevated` + `rounded-xl`）；AI 侧不动 |
| 12 | `src/components/chat/HistorySidebar.tsx` | 改为消费 `lib/session-groups.ts` 的共享函数，删除本地重复实现（保留组件本体，便于回滚） |

### layout.tsx 关键改动

```tsx
'use client'
import { usePathname } from 'next/navigation'

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const pathname = usePathname()
  // /agent 走任务模式：全局控制台导航让位给 TaskSidebar，由页面自行渲染
  const isTaskMode = pathname === '/agent'

  return (
    <html lang="zh-CN">
      <body className="h-full flex bg-surface-root">
        {!isTaskMode && (
          <Sidebar collapsed={!sidebarOpen} onToggle={() => setSidebarOpen((v) => !v)} />
        )}
        <main className="flex-1 flex flex-col min-w-0">
          <ToastProvider>{children}</ToastProvider>
        </main>
      </body>
    </html>
  )
}
```

> 用**精确匹配** `=== '/agent'`，不用 `startsWith` —— 保证 `/agent/tasks` 仍走原控制台导航，避免一次性扩大影响面。

---

## 四、保护清单（禁止动的部分）

改造样式时以下逻辑是踩过坑的成果，**只改 className，不动结构**：

1. **`ChatView.tsx` 的滚动机制** — `ResizeObserver` 内容高度监听 + `userScrolling` 4 秒防打断 + `rafScheduled` 节流。这是为解决"smooth scroll 多次排队导致视觉抖动"专门写的，不要回退成依赖数组方案。
2. **`MessageBubble` 的 memo 与组件级订阅** — `memo` 比较器 + `StreamingContent`/`ThinkingPanel` 各自订阅 store。流式期间只有最后一条气泡重渲。改样式时**不要新增任何 store 订阅**，否则 N 条历史气泡全部重渲，Markdown 重复解析。
3. **Zustand selector 只选原始字段** — `ChatView` 顶部注释已说明：selector 里调函数会导致无限循环。
4. **`store/chat.ts` 整体不动** — P0 不需要任何 store 改动。
5. **`AgentTimeline` 先不挂载** — 它的数据源 `streamEvents` 与 MessageBubble 共用，P0 阶段挂载会与消息流抢视觉焦点。留给 P1。
6. **`navConfig.tsx` 不动** — 有 `navConfig.test.ts` 覆盖，TaskSidebar 直接 `import { NAV }` 消费即可。
7. **部门选择器不能丢** — 它是 RAG 检索权限的输入，不是装饰。

---

## 五、实施顺序（4 步，每步可独立验收）

**Step 1 · 抽公共层（不改变任何视觉）**
抽出 `lib/session-groups.ts` + `SessionRow.tsx`，让 `HistorySidebar` 改为消费它们。
✅ 验收：页面外观零变化，`npm run test` 通过。

**Step 2 · 左栏切换**
新增 `TaskSidebar` + `SessionList`，改 `layout.tsx` 条件渲染，改 `agent/page.tsx` 装配。
✅ 验收：`/agent` 左栏为新样式且会话切换/重命名/删除/搜索全通；访问 `/knowledge` 左栏仍是控制台导航。

**Step 3 · 输入区与消息样式**
改 `ChatInput`（两段式）、新增 `ComposerToolbar`、迁移 `LLMSwitcher`、改 `MessageBubble` 用户消息、新增 `RunStatusLine`。
✅ 验收：部门选择器生效、模型切换生效、发送/停止正常、流式滚动不抖。

**Step 4 · 收尾**
`ChatHeader` 加分享/新建按钮、`EmptyState` 修复与文案调整。
✅ 验收：全链路手测 + `npx tsc --noEmit` + `npm run test`。

---

## 六、验收标准

- [ ] `/agent` 左栏：Logo / 搜索 / 新建任务 / 全部功能分组 / 时间分组会话列表 / 底部用户区，六块齐全
- [ ] 其余 11 个路由的左栏与改造前**完全一致**
- [ ] 会话深链 `/agent?session=xxx` 仍可用（`switchSession` + `loadHistory` 逻辑不能断）
- [ ] 部门选择器、模型切换、发送、停止生成全部正常
- [ ] 流式输出时滚动不抖动，长会话（50+ 条）不卡顿
- [ ] `navConfig.test.ts` / `ThinkingPanel.test.tsx` / `UploadDialog.test.tsx` 全部通过
- [ ] `npx tsc --noEmit` 无新增错误

---

## 七、顺手修的问题

1. **`EmptyState.tsx` 首条示例是坏数据** —— `{ label: '数据查询', text: '？' }`，渲染出来是一个孤零零的问号。应替换为真实示例（如"查询上个月销量前 10 的商品"）。
2. **`EmptyState` 的空态偏"问答助手"** —— 文案"有什么可以帮助你的？"适合 C 端聊天。任务模式下建议改为任务式引导（如"描述你要完成的任务"）。
3. **`ChatHeader` 的标题栏高度 12** —— 与截图相比偏矮，任务模式下可考虑增至 `h-14` 并加大标题字号。

---

## 八、P1 实施记录（2026-09-15 完成）

SSE 协议扩展为 `meta | status | log | delta | thinking | todo | usage | file | done | error`：

| 事件 | 触发时机 | 后端 | 前端 |
|---|---|---|---|
| `todo` | planner/critique/supervisor 节点跑完后发全量任务快照 | `events.make_todo_event` + `runner.py` 循环内发射 | `store.todoItems` → `TodoCard` |
| `usage` | supervisor 每轮调度后发轮内累计用量 | `events.make_usage_event` | `store.streamUsage` → `RunStatusLine`（实时优先，会话累计兜底） |
| `file` | skill 步骤落盘文件时（正则提取绝对路径，export_csv 已覆盖） | `events.make_file_event` + `_build_skill_events` | `store.fileOps`（文件级去重）→ `FileOpsCard` |

另挂载 `AgentTimeline`（此前零引用的成品组件）到 ChatView 顶部，与 TodoCard/FileOpsCard 构成执行进度信息带。

验证：后端事件流测试 19/19、前端 129/129、`tsc --noEmit` 通过。
