# 前端 (Next.js)

> 统一单页面对话界面 + Monitor 子页。后端 Multi-Agent 自动路由到 SQL/RAG/Report Worker。

## 1. 总览

```
web/src/
├── app/
│   ├── layout.tsx              # 根布局（Sidebar + 顶栏）
│   ├── page.tsx                # / 路由 → ChatView
│   ├── monitor/page.tsx        # /monitor 路由 → MonitorDashboard
│   └── globals.css             # 全局样式
├── components/
│   ├── Sidebar.tsx             # 侧边栏（视图切换 + 会话列表）
│   ├── ChatView.tsx            # 主容器（连接 ChatInput + MessageList + StatusBar + LLMSwitcher）
│   ├── MessageList.tsx         # 消息列表容器
│   ├── MessageBubble.tsx       # 消息气泡（SSE 进度 + Markdown 渲染）
│   ├── ChatInput.tsx           # 通用输入框（通过 onSend prop 解耦）
│   ├── ThinkingPanel.tsx       # 思维链日志面板（📊/📚/📄/🧠）
│   ├── StatusBar.tsx           # 顶部实时进度条
│   ├── EmptyState.tsx          # 空状态示例页
│   ├── SourceCard.tsx          # 来源文档卡片
│   ├── MarkdownContent.tsx     # Markdown 渲染（react-markdown）
│   ├── ErrorBoundary.tsx       # React 错误边界
│   ├── LLMSwitcher.tsx         # LLM 切换器 + 余额显示
│   └── monitor/
│       └── MonitorDashboard.tsx   # 可观测性大盘
├── hooks/
│   ├── useSSE.ts               # SSE 流消费（带 AbortController）
│   └── useChat.ts              # 发送消息 hook
├── lib/
│   ├── api.ts                  # API 客户端（含 SSE buffer 刷新）
│   ├── types.ts                # 类型定义
│   └── sse-parser.ts           # 共享 SSE 进度解析
└── store/
    └── chat.ts                  # Zustand store
```

## 2. 路由

| 路径 | 组件 | 功能 |
|---|---|---|
| `/` | `ChatView` | 主对话界面 |
| `/monitor` | `MonitorDashboard` | 可观测性大盘 |

通过 Sidebar 顶部的视图切换器在两页面间导航（用 `useRouter().push()`）。

## 3. 状态管理 (Zustand)

**单 store** `web/src/store/chat.ts` 管理 5 类状态：

| 字段 | 类型 | 用途 |
|---|---|---|
| `sessions` | `Session[]` | 会话列表（持久） |
| `currentId` | `string` | 当前会话 ID |
| `streamEvents` | `SSEStreamEvent[]` | 当前流式事件累积（临时） |
| `currentStatus` | `string` | 当前 LangGraph 节点名（StatusBar 消费） |
| `deltaText` | `string` | 流式累积文本（MessageBubble 消费） |
| `nodeLabels` | `Record<string, string>` | meta 事件下发的 emoji 映射 |
| `isLoading` / `error` / `currentRequestId` | `boolean` / `string?` | UI 标志 + 中止信号 |

**Actions**：CRUD 会话（`newSession` / `switchSession` / `renameSession` / `deleteSession`） + CRUD 消息（`addMessage` / `addStreamEvent` / `replaceLastAssistant`） + 状态（`setLoading` / `setError` / `setCurrentRequestId` / `resetStream`）。

**注意**：
- 流式事件临时态与持久会话状态混在一个 store，性能可优化（plan 暂不动）
- `addStreamEvent` 在流式时**会克隆整个 sessions 数组**（性能瓶颈）

## 4. SSE 流

### 4.1 后端事件类型（`web/src/lib/types.ts`）

```ts
type SSEStreamEvent =
  | { event: 'meta';   data: MetaEvent }   // node_labels 映射
  | { event: 'status'; data: StatusEvent }  // node 切换
  | { event: 'log';    data: LogEvent }     // Worker 日志
  | { event: 'delta';  data: DeltaEvent }   // 句子块
  | { event: 'done';   data: DoneEvent }    // 完成 + sources
  | { event: 'error';  data: ErrorEvent }   // 错误
```

### 4.2 客户端消费

`web/src/hooks/useSSE.ts` 包装 `streamChat(req, signal)`：
- 调用 `POST /chat/stream`
- 用 `for await` 解析 SSE 帧
- 每个事件 dispatch 到 zustand store
- 收到 `error` 事件 → 立即持久化 + 终止
- 收到 `done` 事件 → 写 `message.content` 完整文本 + sources
- catch 异常 → 区分 abort / 网络错误，分别处理
- `finally` → setLoading(false) + clearRequestId

`AbortController` 用于"停止生成"按钮（`ChatView.tsx:stopStream`），同时调用 `POST /chat/abort` 通知后端。

### 4.3 解析器

`web/src/lib/sse-parser.ts`（已删除，合并到 `api.ts:streamChat` 内的 `_tryParseEvent`）。

## 5. 关键组件

### 5.1 ChatView (`components/ChatView.tsx`)

主容器。结构：

```
[error banner]
[reconnecting notice]
[LLMSwitcher (right-aligned)]
[scrollable content: EmptyState OR MessageList]
[StatusBar]
[stop button (when loading)]
[ChatInput]
```

- 自动滚动：用户手动上滚时暂缓，5s 无操作后恢复
- "重连提示"是**伪状态**（实际无重连，4s 后自动消失）

### 5.2 MessageBubble (`components/MessageBubble.tsx`)

单条消息渲染。流式 vs 完成态分支：

- 流式态（`isCurrentStreaming`）：RAF 节流显示 `deltaText` + 闪烁光标
- 完成态：渲染 `message.content`（已 strip 参考文献）+ SourceCard（如果有 sources）

**stripReferences**：移除正文末尾的 `### 参考文献` 区块（SourceCard 已展示，避免重复）。

### 5.3 ThinkingPanel (`components/ThinkingPanel.tsx`)

思维链日志面板（折叠态默认）：
- 展开后显示 `streamEvents.filter(e => e.event === 'log')` 时间线
- Payload 字段折叠展示（避免噪声）
- 红点徽标：新日志到达时闪烁 3s 或展开后消失
- 错误 / 警告计数显示

### 5.4 StatusBar (`components/StatusBar.tsx`)

顶部状态条：从 store 读 `currentStatus` + `nodeLabels`，映射到 emoji 标签。`done` 后自动隐藏。

### 5.5 LLMSwitcher (`components/LLMSwitcher.tsx`)

Google 风格 LLM 切换器 + 余额显示：
- 触发器胶囊：合并"模型名 + 余额"
- 下拉面板：列出所有模型（`/llm/models`）+ 当前余额（`/llm/balance`）
- 切换：调用 `POST /llm/switch`

### 5.6 MonitorDashboard (`components/monitor/MonitorDashboard.tsx`)

可观测性大盘：4 个区块（KPI / 资源 / 延迟 / 告警），每 5s 轮询 `/observability/*` 端点。

## 6. 关键 Hooks

### 6.1 useSSE (`hooks/useSSE.ts`)

```ts
const { startStream, stopStream } = useSSE()
// startStream(question, sessionId): 启动流
// stopStream(): 中断 + 通知后端
```

内部持有 `AbortController` + `requestId` (nanoid)，用 `useRef` 避免 hook 重渲。

### 6.2 useChat (`hooks/useChat.ts`)

13 行 wrapper（`useSendMessage`），把 `useSSE` 包一层。可考虑直接删除（plan 暂不动）。

## 7. API 客户端 (`lib/api.ts`)

所有 fetch 集中：

| 函数 | 用途 |
|---|---|
| `streamChat(req, signal)` | POST /chat/stream（SSE） |
| `abortChat(sessionId, requestId)` | POST /chat/abort |
| `listLLMModels()` | GET /llm/models |
| `getCurrentLLM()` | GET /llm/current |
| `switchLLM(model)` | POST /llm/switch |
| `getLLMBalance(provider?)` | GET /llm/balance |

SSE 解析用 `TextDecoder` + 行缓冲，处理分块接收。

## 8. 类型定义 (`lib/types.ts`)

- `ChatRequest` / `ChatResponse` — API 请求 / 响应
- `Source` — 来源文档
- `SSEStreamEvent` 联合类型 — SSE 事件
- `Message` — 单条消息（含 `streamEvents` 累积）
- `Session` — 会话（含 messages 列表）
- `ChatMode` — 会话模式（`chat` / `sql` / `rag` / `report`）

## 9. 关键约定

- **统一单页面**：后端 Multi-Agent 自动路由 Worker，用户无需选择模式
- **ChatInput 通过 onSend prop 解耦**：不依赖全局 store，便于复用
- **SSE 流支持 AbortController 中止**：用户点击"停止生成"会同时发后端 abort 信号
- **Zustand 单 store**：简单场景不需要 RTK Query / Redux（plan 暂不引入）
- **不直接 fetch**：所有 HTTP 走 `lib/api.ts`

## 10. 修改指南

- **加新 SSE 事件类型**：在 `types.ts` 加 `data` 类型 + `SSEStreamEvent` 联合分支 + `api.ts:_tryParseEvent` 不用改（自动）
- **加新消息字段**：在 `types.ts:Message` 加 + `MessageBubble.tsx` 渲染
- **改主题色**：全局 CSS 变量（`globals.css`）
- **加新视图**：在 `app/` 加新路由 + 在 `Sidebar.tsx` 加导航按钮
- **改后端 API**：在 `api.ts` 加新函数 + 在 `types.ts` 加新类型

## 11. 已知问题 / 待优化

- `store/chat.ts` 222 行，混 5 类状态（plan 暂不拆）
- `LLMSwitcher.tsx` 269 行（plan 暂不拆）
- `MessageBubble.tsx` 的 RAF 节流 22 行手写 — 可用 `useSyncExternalStore` 简化
- `ChatView.tsx` 的 `reconnecting` 伪状态命名误导（实际无重连）
- 没有 toast 系统（`error` banner + `reconnecting` banner 视觉冲突）
- `useChat.ts` 13 行 wrapper 价值低（plan 暂不删）
- `addStreamEvent` 每次事件克隆整 sessions 数组（流式越长越慢）
