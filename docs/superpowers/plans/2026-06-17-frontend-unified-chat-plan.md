# Frontend Unified Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge 4 independent route pages into a single chat-first interface using backend Multi-Agent auto-routing.

**Architecture:** Delete sql/rag/report pages and ModeTabs. Add shared SSE parser. Refactor store (new action), hooks (use store API instead of bypassing it), and 5 components (data-driven ThinkingPanel, decoupled ChatInput, unified EmptyState, shared progress parsing). Net: 21→17 files, ~1400→~900 lines.

**Tech Stack:** Next.js 14, React 18, TypeScript, Zustand, Tailwind CSS

## Global Constraints

- No changes to `lib/api.ts` — all API functions preserved
- No changes to `app/layout.tsx` beyond removing ModeTabs import
- No changes to `tailwind.config.ts` or `app/globals.css`
- `ChatMode` type kept internally in `lib/types.ts`; `MODE_LABELS` removed
- All SSE streaming behavior preserved — `POST /chat/stream` is the sole request path
- Worker icons: `📊` sql_worker, `📚` rag_worker, `📄` report_worker, `🧠` planner

---

### Task 1: Add shared SSE progress parser

**Files:**
- Create: `web/src/lib/sse-parser.ts`

**Interfaces:**
- Consumes: `SSEEvent` from `@/lib/types` (exists)
- Produces: `StepInfo`, `Progress`, `parseProgress(events: SSEEvent[]): Progress`

- [ ] **Step 1: Create the parser file**

```typescript
// web/src/lib/sse-parser.ts
import type { SSEEvent } from './types'

export interface StepInfo {
  stepId: string
  status: string
  description: string
  node: string
  elapsed?: number
}

export interface Progress {
  steps: StepInfo[]
  total: number
  completed: number
  failed: number
  running: StepInfo | null
  isDone: boolean
}

export function parseProgress(events: SSEEvent[]): Progress {
  const stepMap = new Map<string, StepInfo>()
  let planCount = 0
  let isDone = false

  for (const e of events) {
    if (e.stage === 'planning') {
      planCount = e.data.task_count ?? 0
    }
    if (e.stage === 'executing' && e.data.step_id) {
      const existing = stepMap.get(e.data.step_id)
      if (!existing || existing.status === 'running') {
        stepMap.set(e.data.step_id, {
          stepId: e.data.step_id,
          status: e.data.status ?? 'running',
          description: e.data.description ?? '',
          node: e.node,
          elapsed: e.data.elapsed,
        })
      }
    }
    if (e.stage === 'done') {
      isDone = true
    }
  }

  const steps = Array.from(stepMap.values())
  const total = planCount || steps.length
  const completed = steps.filter((s) => s.status === 'success').length
  const failed = steps.filter((s) => s.status === 'failed').length
  const running = steps.find((s) => s.status === 'running') ?? null

  return { steps, total, completed, failed, running, isDone }
}
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors related to `sse-parser.ts`.

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/sse-parser.ts
git commit -m "feat: add shared SSE progress parser"
```

---

### Task 2: Add replaceLastAssistant to Zustand store

**Files:**
- Modify: `web/src/store/chat.ts`

**Interfaces:**
- Consumes: `Session`, `Message`, `SSEEvent` from `@/lib/types` (exist)
- Produces: `replaceLastAssistant(content: string, thinking?: SSEEvent[]): void`
- Removes: `setMode`, `mode` from state

- [ ] **Step 1: Add replaceLastAssistant action and remove mode-related code**

In `web/src/store/chat.ts`, make these changes:

**1a. Remove `mode` from state interface and initial state:**

Remove line `mode: ChatMode` from `ChatState` interface.
Remove line `setMode: (mode: ChatMode) => void` from `ChatState` interface.
Remove line `mode: 'chat',` from initial state object.

**1b. Remove `setMode` action:**

```typescript
// DELETE these lines:
setMode: (mode) => set({ mode }),
```

**1c. Add `replaceLastAssistant` action after `addThinkingEvent`:**

```typescript
replaceLastAssistant: (content, thinking) => {
  set((state) => ({
    sessions: state.sessions.map((s) => {
      if (s.id !== state.currentId) return s
      const msgs = [...s.messages]
      const lastIdx = msgs.length - 1
      if (lastIdx >= 0 && msgs[lastIdx].role === 'assistant') {
        msgs[lastIdx] = {
          ...msgs[lastIdx],
          content,
          thinking,
          timestamp: Date.now(),
        }
      }
      return { ...s, messages: msgs, updatedAt: Date.now() }
    }),
  }))
},
```

**1d. Remove `ChatMode` import if no longer needed:**

Check if `ChatMode` is used elsewhere in the file (in `createSession`). It is — keep the import but remove `setMode` from the interface.

Actually, since we're removing `mode` from state, `createSession` no longer takes a mode parameter. Update it:

```typescript
function createSession(): Session {
  const id = nanoid()
  return {
    id,
    title: '新对话',
    mode: 'chat',       // always 'chat' now
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  }
}
```

And update `newSession`:

```typescript
newSession: () => {
  const s = createSession()
  set((state) => ({
    sessions: [s, ...state.sessions],
    currentId: s.id,
    thinking: [],
    error: null,
  }))
  return s.id
},
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors. Fix any type issues — ensure all references to `mode` and `setMode` are removed from the store.

- [ ] **Step 3: Commit**

```bash
git add web/src/store/chat.ts
git commit -m "refactor(store): add replaceLastAssistant action, remove mode state"
```

---

### Task 3: Refactor useSSE hook to use store actions

**Files:**
- Modify: `web/src/hooks/useSSE.ts`

**Interfaces:**
- Consumes: `replaceLastAssistant` from `store/chat.ts` (Task 2), `streamChat` from `@/lib/api` (exists)
- Produces: `startStream(question: string, sessionId: string): Promise<void>` (unchanged signature)

- [ ] **Step 1: Rewrite useSSE.ts**

```typescript
// web/src/hooks/useSSE.ts
'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { streamChat } from '@/lib/api'
import type { SSEEvent } from '@/lib/types'

export function useSSE() {
  const startStream = useCallback(async (question: string, sessionId: string) => {
    const { addMessage, addThinkingEvent, replaceLastAssistant, setLoading, setError } =
      useChatStore.getState()

    setLoading(true)
    setError(null)

    // User message
    addMessage('user', question)

    // Empty assistant placeholder
    addMessage('assistant', '')

    try {
      const thinkingEvents: SSEEvent[] = []

      for await (const event of streamChat({ question, session_id: sessionId })) {
        thinkingEvents.push(event)
        addThinkingEvent(event)

        if (event.stage === 'done' && event.data.final_answer) {
          replaceLastAssistant(event.data.final_answer, thinkingEvents)
        }
      }
    } catch (err: any) {
      setError(err.message || '请求失败')
      replaceLastAssistant(
        `## 请求失败\n\n${err.message || '未知错误'}`,
      )
    } finally {
      setLoading(false)
    }
  }, [])

  return { startStream }
}
```

Key change: the two `store.getState().sessions...map...setState` blocks (lines 40-55 and 63-75 in original) are replaced by single `replaceLastAssistant` calls.

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/useSSE.ts
git commit -m "refactor(useSSE): use replaceLastAssistant instead of manual store mutation"
```

---

### Task 4: Simplify useChat hook

**Files:**
- Modify: `web/src/hooks/useChat.ts`

**Interfaces:**
- Consumes: `useSSE` from `./useSSE` (Task 3), `useChatStore` (exists)
- Produces: `{ send: (question: string) => Promise<void> }`
- Removes: `sendSync`

- [ ] **Step 1: Rewrite useChat.ts**

```typescript
// web/src/hooks/useChat.ts
'use client'

import { useCallback } from 'react'
import { useChatStore } from '@/store/chat'
import { useSSE } from './useSSE'

export function useSendMessage() {
  const currentId = useChatStore((s) => s.currentId)
  const { startStream } = useSSE()

  const send = useCallback(
    async (question: string) => {
      await startStream(question, currentId)
    },
    [currentId, startStream],
  )

  return { send }
}
```

Delete the `sendSync` function entirely and the unused `sendChat` import.

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/hooks/useChat.ts
git commit -m "refactor(useChat): remove sendSync, SSE-only path"
```

---

### Task 5: Refactor ThinkingPanel — data-driven rendering

**Files:**
- Modify: `web/src/components/ThinkingPanel.tsx`

**Interfaces:**
- Consumes: `SSEEvent` from `@/lib/types` (exists)
- Produces: React component `<ThinkingPanel events={SSEEvent[]} />` (unchanged signature)

- [ ] **Step 1: Rewrite ThinkingPanel.tsx**

```typescript
// web/src/components/ThinkingPanel.tsx
'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, CheckCircle2, XCircle, Circle, SkipForward } from 'lucide-react'
import type { SSEEvent } from '@/lib/types'

const WORKER_ICONS: Record<string, string> = {
  sql_worker: '📊',
  rag_worker: '📚',
  report_worker: '📄',
  planner: '🧠',
}

function statusIcon(status?: string) {
  switch (status) {
    case 'success': return <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
    case 'failed':  return <XCircle size={14} className="text-red-400 shrink-0" />
    case 'running': return <Circle size={14} className="text-blue-400 shrink-0" />
    case 'skipped': return <SkipForward size={14} className="text-[#8e8e8e] shrink-0" />
    default:        return null
  }
}

interface RenderItem {
  icon: React.ReactNode
  label: string
  detail: string
  extra?: React.ReactNode
  isError?: boolean
}

function stageToItem(e: SSEEvent): RenderItem | null {
  const icon = WORKER_ICONS[e.node] ?? ''

  switch (e.stage) {
    case 'planning':
      return {
        icon: <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />,
        label: e.label,
        detail: e.message,
        extra: e.data.tasks && e.data.tasks.length > 0 ? (
          <ul className="mt-1 space-y-0.5">
            {e.data.tasks.map((t: string, j: number) => (
              <li key={j} className="text-[#8e8e8e] pl-3">{j + 1}. {t}</li>
            ))}
          </ul>
        ) : undefined,
      }

    case 'executing':
      return {
        icon: statusIcon(e.data.status),
        label: `${icon} ${e.label}`,
        detail: e.data.description || e.message,
        extra: e.data.elapsed != null ? (
          <span className="text-[#6e6e6e] ml-1.5 tabular-nums">{e.data.elapsed.toFixed(1)}s</span>
        ) : undefined,
        isError: e.data.status === 'failed',
      }

    case 'done':
      return {
        icon: <CheckCircle2 size={14} className="text-emerald-500 shrink-0 mt-0.5" />,
        label: '',
        detail: e.message,
      }

    case 'error':
      return {
        icon: <XCircle size={14} className="text-red-400 shrink-0 mt-0.5" />,
        label: '',
        detail: e.message,
        isError: true,
      }

    default:
      return null
  }
}

export default function ThinkingPanel({ events }: { events: SSEEvent[] }) {
  const [collapsed, setCollapsed] = useState(false)

  if (events.length === 0) return null

  // Deduplicate: keep latest state per step_id (executing) or stage (planning/done/error)
  const seen = new Map<string, SSEEvent>()
  for (const e of events) {
    if (e.stage === 'executing' && e.data.step_id) {
      seen.set(e.data.step_id, e)
    } else if (e.stage === 'planning' || e.stage === 'done' || e.stage === 'error') {
      seen.set(e.stage, e)
    }
  }

  const items = Array.from(seen.values())
    .map(stageToItem)
    .filter((item): item is RenderItem => item !== null)
  const hasError = items.some((item) => item.isError)
  const allDone = events.some((e) => e.stage === 'done')
  const successCount = events.filter((e) => e.stage === 'executing' && e.data.status === 'success').length
  const totalSteps = items.length

  return (
    <div className="mb-4 border border-[#3f3f3f] rounded-xl overflow-hidden bg-[#1a1a1a]">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className={`w-full flex items-center gap-2 px-4 py-2.5 text-xs font-medium transition-colors ${
          hasError ? 'text-red-400' : allDone ? 'text-emerald-400' : 'text-[#b4b4b4]'
        }`}
      >
        {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
        <span>思考过程</span>
        <span className="text-[#8e8e8e] font-normal">({successCount}/{totalSteps} 步)</span>
      </button>

      {!collapsed && (
        <div className="px-4 pb-3 space-y-1.5">
          {items.map((item, i) => (
            <div key={i} className="flex items-start gap-2.5 text-xs py-1">
              {item.icon}
              <div className="min-w-0">
                {item.label && (
                  <span className={item.isError ? 'text-red-400' : 'text-[#ececec]'}>
                    {item.label}
                  </span>
                )}
                <span className={item.isError ? 'text-red-400/80' : 'text-[#8e8e8e]'}>
                  {item.label ? ' ' : ''}{item.detail}
                </span>
                {item.extra}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ThinkingPanel.tsx
git commit -m "refactor(ThinkingPanel): data-driven stage rendering with worker icons"
```

---

### Task 6: Refactor MessageBubble — use shared parseProgress

**Files:**
- Modify: `web/src/components/MessageBubble.tsx`

**Interfaces:**
- Consumes: `parseProgress` from `@/lib/sse-parser` (Task 1), `ThinkingPanel` (Task 5, unchanged import), `MarkdownContent` (unchanged)
- Produces: React component `<MessageBubble message={Message} />` (unchanged signature)

- [ ] **Step 1: Rewrite MessageBubble.tsx**

```typescript
// web/src/components/MessageBubble.tsx
'use client'

import { useMemo } from 'react'
import { User, Bot, Loader2 } from 'lucide-react'
import type { Message } from '@/lib/types'
import MarkdownContent from './MarkdownContent'
import ThinkingPanel from './ThinkingPanel'
import { parseProgress } from '@/lib/sse-parser'

const WORKER_ICONS: Record<string, string> = {
  sql_worker: '📊',
  rag_worker: '📚',
  report_worker: '📄',
  planner: '🧠',
}

export default function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const isEmpty = !message.content
  const thinking = message.thinking

  const progress = useMemo(() => {
    if (!thinking?.length) return null
    return parseProgress(thinking)
  }, [thinking])

  return (
    <div className={`animate-fade-in flex gap-4 ${isUser ? 'justify-end' : ''}`}>
      {/* Avatar: Bot */}
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center shrink-0">
          <Bot size={18} className="text-white" />
        </div>
      )}

      <div className={`min-w-0 max-w-[85%] ${isUser ? 'order-first' : ''}`}>
        {/* User message */}
        {isUser ? (
          <div className="bg-[#2f2f2f] rounded-2xl rounded-br-md px-4 py-3 text-[#ececec] text-sm leading-relaxed">
            {message.content}
          </div>
        ) : (
          <div>
            {/* SSE thinking process */}
            {thinking && thinking.length > 0 && <ThinkingPanel events={thinking} />}

            {/* Streaming in progress */}
            {isEmpty && progress?.running ? (
              <div className="flex items-center gap-2 text-sm text-[#b4b4b4] py-1">
                <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
                <span className="text-[#8e8e8e] shrink-0">
                  {WORKER_ICONS[progress.running.node] ?? '🔧'}
                </span>
                <span className="truncate text-[#ececec]">{progress.running.description}</span>
                {progress.total > 0 && (
                  <span className="text-xs text-[#8e8e8e] ml-1 shrink-0">
                    [{progress.completed}/{progress.total}]
                  </span>
                )}
                {progress.running.elapsed != null && (
                  <span className="text-xs text-[#6e6e6e] shrink-0 tabular-nums">
                    {progress.running.elapsed.toFixed(1)}s
                  </span>
                )}
              </div>
            ) : isEmpty && !progress ? (
              <div className="flex items-center gap-2 text-sm text-[#b4b4b4] py-1">
                <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
                <span className="text-[#ececec]">正在思考...</span>
              </div>
            ) : (
              /* Markdown content */
              <div className="text-sm text-[#ececec]">
                <MarkdownContent content={message.content} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Avatar: User */}
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-violet-600 flex items-center justify-center shrink-0">
          <User size={16} className="text-white" />
        </div>
      )}
    </div>
  )
}
```

Key changes:
- Removed local `getRunningStep` function (~20 lines)
- Added import of `parseProgress` from `@/lib/sse-parser`
- `WorkerIcons` renamed to `WORKER_ICONS` (consistency with ThinkingPanel)
- Removed `XCircle`, `CheckCircle2` imports (unused now)

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/MessageBubble.tsx
git commit -m "refactor(MessageBubble): use shared parseProgress from sse-parser"
```

---

### Task 7: Refactor StatusBar — use shared parseProgress

**Files:**
- Modify: `web/src/components/StatusBar.tsx`

**Interfaces:**
- Consumes: `parseProgress` from `@/lib/sse-parser` (Task 1), `useChatStore` (exists)
- Produces: React component `<StatusBar />` (unchanged signature)

- [ ] **Step 1: Rewrite StatusBar.tsx**

```typescript
// web/src/components/StatusBar.tsx
'use client'

import { useMemo } from 'react'
import { Loader2, CheckCircle2, XCircle, Database, Library, FileText, Brain } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import { parseProgress } from '@/lib/sse-parser'

const WORKER_ICONS: Record<string, React.ReactNode> = {
  sql_worker: <Database size={13} />,
  rag_worker: <Library size={13} />,
  report_worker: <FileText size={13} />,
  planner: <Brain size={13} />,
}

export default function StatusBar() {
  const thinking = useChatStore((s) => s.thinking)
  const isLoading = useChatStore((s) => s.isLoading)

  const progress = useMemo(() => {
    if (!thinking.length) return null
    return parseProgress(thinking)
  }, [thinking])

  if (!isLoading && !progress) return null
  if (progress?.isDone) return null

  return (
    <div className="shrink-0 border-t border-[#3f3f3f] bg-[#1a1a1a] px-4 py-2">
      <div className="max-w-3xl mx-auto flex items-center gap-3">
        {/* Current action */}
        {progress?.running ? (
          <div className="flex items-center gap-2 text-sm text-[#b4b4b4] min-w-0">
            <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
            <span className="text-[#8e8e8e] shrink-0">
              {WORKER_ICONS[progress.running.node] ?? null}
            </span>
            <span className="truncate text-[#ececec]">{progress.running.description}</span>
          </div>
        ) : !progress && isLoading ? (
          <div className="flex items-center gap-2 text-sm text-[#b4b4b4]">
            <Loader2 size={14} className="text-blue-400 animate-spin shrink-0" />
            <span className="text-[#ececec]">正在分析问题...</span>
          </div>
        ) : progress && !progress.isDone ? (
          <div className="flex items-center gap-2 text-sm text-emerald-400">
            <CheckCircle2 size={14} className="shrink-0" />
            <span>等待下一步...</span>
          </div>
        ) : null}

        {/* Progress counter */}
        {progress && progress.total > 0 && (
          <div className="flex items-center gap-2 ml-auto text-xs text-[#8e8e8e] shrink-0">
            {progress.failed > 0 && (
              <span className="flex items-center gap-1 text-red-400">
                <XCircle size={12} />
                {progress.failed}
              </span>
            )}
            <span className={progress.completed === progress.total ? 'text-emerald-400' : ''}>
              [{progress.completed}/{progress.total}]
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
```

Key changes:
- Removed local progress computation (~30 lines of stepMap iteration)
- Replaced with `parseProgress(thinking)` from shared parser
- `lastDone` variable replaced by `progress.isDone`

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/StatusBar.tsx
git commit -m "refactor(StatusBar): use shared parseProgress from sse-parser"
```

---

### Task 8: Decouple ChatInput

**Files:**
- Modify: `web/src/components/ChatInput.tsx`

**Interfaces:**
- Consumes: nothing from store now (receives props)
- Produces: React component `<ChatInput onSend={(text) => void} isLoading={boolean} />`

- [ ] **Step 1: Rewrite ChatInput.tsx**

Remove internal `useSendMessage()` call. Accept `onSend` and `isLoading` as props.

```typescript
// web/src/components/ChatInput.tsx
'use client'

import { useState, useRef, useEffect, KeyboardEvent } from 'react'
import { Send, Loader2 } from 'lucide-react'

interface Props {
  onSend: (text: string) => void
  isLoading: boolean
}

export default function ChatInput({ onSend, isLoading }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 200) + 'px'
    }
  }, [input])

  function handleSend() {
    const trimmed = input.trim()
    if (!trimmed || isLoading) return
    setInput('')
    onSend(trimmed)
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="shrink-0 border-t border-[#3f3f3f] bg-[#212121]">
      <div className="max-w-3xl mx-auto px-4 py-3">
        <div className="flex items-end gap-3 bg-[#2f2f2f] rounded-2xl px-4 py-2.5 border border-[#3f3f3f] focus-within:border-[#5f5f5f] transition-colors">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="输入你的问题... (Enter 发送, Shift+Enter 换行)"
            rows={1}
            disabled={isLoading}
            className="flex-1 bg-transparent resize-none outline-none text-sm text-[#ececec] placeholder-[#8e8e8e] max-h-[200px] disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="shrink-0 p-1.5 rounded-lg bg-[#ececec] text-[#171717] hover:bg-white disabled:opacity-30 disabled:cursor-not-allowed transition-all"
            aria-label="发送"
          >
            {isLoading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
        <p className="text-[10px] text-[#8e8e8e] text-center mt-2">
          Agent AI 基于 LangGraph Multi-Agent 架构 &middot; 答案由 LLM 生成，请核实关键信息
        </p>
      </div>
    </div>
  )
}
```

Removed imports: `useChatStore`, `useSendMessage`.

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ChatInput.tsx
git commit -m "refactor(ChatInput): decouple via onSend prop"
```

---

### Task 9: Refactor EmptyState — unified examples

**Files:**
- Modify: `web/src/components/EmptyState.tsx`

**Interfaces:**
- Consumes: nothing from store (no more mode dependency)
- Produces: React component `<EmptyState onExampleClick={(text) => void} />`

- [ ] **Step 1: Rewrite EmptyState.tsx**

```typescript
// web/src/components/EmptyState.tsx
'use client'

import { MessageSquare } from 'lucide-react'

const TITLE = 'Multi-Agent 智能助手'
const DESC = 'AI 自动拆解复杂任务，并行调用 📊 SQL 查询、📚 知识库检索 和 📄 报告引擎'

const EXAMPLES = [
  { icon: '📊', label: '数据查询', text: '技术部有多少人？' },
  { icon: '📚', label: '知识检索', text: '微服务架构的最佳实践？' },
  { icon: '📄', label: '生成报告', text: '分析技术部预算使用情况并生成报告' },
  { icon: '🤖', label: '复杂分析', text: '对比各部门绩效，给出改进建议' },
]

interface Props {
  onExampleClick?: (question: string) => void
}

export default function EmptyState({ onExampleClick }: Props) {
  return (
    <div className="flex flex-col items-center justify-center h-full px-4 py-12">
      <div className="w-14 h-14 rounded-2xl bg-[#2f2f2f] flex items-center justify-center text-[#b4b4b4] mb-5">
        <MessageSquare size={28} />
      </div>
      <h2 className="text-lg font-medium text-[#ececec] mb-1.5">{TITLE}</h2>
      <p className="text-sm text-[#8e8e8e] mb-8 text-center max-w-sm">{DESC}</p>

      <div className="grid gap-2 w-full max-w-md">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.text}
            onClick={() => onExampleClick?.(ex.text)}
            className="text-left px-4 py-3 rounded-xl border border-[#3f3f3f] text-sm text-[#b4b4b4] hover:bg-[#2f2f2f] hover:text-[#ececec] hover:border-[#5f5f5f] transition-all"
          >
            <span className="mr-2">{ex.icon}</span>
            <span className="text-[#8e8e8e] text-xs">{ex.label}</span>
            <br />
            <span>&ldquo;{ex.text}&rdquo;</span>
          </button>
        ))}
      </div>
    </div>
  )
}
```

Removed: `useChatStore` import, `configs` object, `Database`/`Library`/`FileText` icon imports.

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/EmptyState.tsx
git commit -m "refactor(EmptyState): unified examples for all worker types"
```

---

### Task 10: Refactor ChatView — connect decoupled ChatInput

**Files:**
- Modify: `web/src/components/ChatView.tsx`

**Interfaces:**
- Consumes: `useSendMessage` (Task 4), `ChatInput` (Task 8, new props), `MessageList`, `EmptyState` (Task 9)
- Produces: React component `<ChatView />` (unchanged)

- [ ] **Step 1: Rewrite ChatView.tsx**

```typescript
// web/src/components/ChatView.tsx
'use client'

import { useEffect, useRef } from 'react'
import { useChatStore } from '@/store/chat'
import { useSendMessage } from '@/hooks/useChat'
import MessageList from './MessageList'
import ChatInput from './ChatInput'
import EmptyState from './EmptyState'

export default function ChatView() {
  const messages = useChatStore((s) => s.currentMessages())
  const isLoading = useChatStore((s) => s.isLoading)
  const bottomRef = useRef<HTMLDivElement>(null)
  const { send } = useSendMessage()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, messages[messages.length - 1]?.content])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyState onExampleClick={send} />
        ) : (
          <MessageList messages={messages} isLoading={isLoading} />
        )}
        <div ref={bottomRef} />
      </div>

      <ChatInput onSend={send} isLoading={isLoading} />
    </div>
  )
}
```

Key change: `<ChatInput onSend={send} isLoading={isLoading} />` replaces `<ChatInput />`.

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/ChatView.tsx
git commit -m "refactor(ChatView): connect decoupled ChatInput via props"
```

---

### Task 11: Clean up Sidebar — remove ModeTabs

**Files:**
- Modify: `web/src/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `SessionList` (unchanged), `useChatStore` (no more mode)
- Produces: React component `<Sidebar collapsed={boolean} onToggle={() => void} />` (unchanged)

- [ ] **Step 1: Remove ModeTabs from Sidebar**

```typescript
// web/src/components/Sidebar.tsx
'use client'

import { Plus, PanelLeftClose, PanelLeft } from 'lucide-react'
import { useChatStore } from '@/store/chat'
import SessionList from './SessionList'

interface Props {
  collapsed: boolean
  onToggle: () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  const newSession = useChatStore((s) => s.newSession)

  if (collapsed) {
    return (
      <aside className="w-0 shrink-0 overflow-hidden md:w-12 md:flex md:flex-col md:items-center md:py-3 md:border-r md:border-[#3f3f3f] bg-[#171717]">
        <button
          onClick={onToggle}
          className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
          aria-label="展开侧边栏"
        >
          <PanelLeft size={18} />
        </button>
      </aside>
    )
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col bg-[#171717] border-r border-[#3f3f3f]">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b border-[#2f2f2f]">
        <span className="text-sm font-semibold tracking-wide text-[#ececec]">Agent AI</span>
        <div className="flex items-center gap-0.5">
          <button
            onClick={() => newSession()}
            className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
            aria-label="新对话"
            title="新对话"
          >
            <Plus size={16} />
          </button>
          <button
            onClick={onToggle}
            className="p-1.5 rounded-md hover:bg-[#2f2f2f] transition-colors text-[#b4b4b4]"
            aria-label="收起侧边栏"
            title="收起"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <p className="text-[11px] text-[#8e8e8e] px-2 mb-1.5 uppercase tracking-wide">历史会话</p>
        <SessionList />
      </div>
    </aside>
  )
}
```

Key changes:
- Removed `import ModeTabs from './ModeTabs'`
- Removed `<ModeTabs />` JSX + separator line

- [ ] **Step 2: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/Sidebar.tsx
git commit -m "refactor(Sidebar): remove ModeTabs"
```

---

### Task 12: Delete old route pages + cleanup types

**Files:**
- Delete: `web/src/app/sql/page.tsx`
- Delete: `web/src/app/rag/page.tsx`
- Delete: `web/src/app/report/page.tsx`
- Delete: `web/src/components/ModeTabs.tsx`
- Modify: `web/src/lib/types.ts`
- Modify: `web/src/app/layout.tsx`

**Interfaces:**
- Consumes: nothing new
- Produces: cleaned project without dead code

- [ ] **Step 1: Delete dead files**

```bash
rm web/src/app/sql/page.tsx
rm web/src/app/rag/page.tsx
rm web/src/app/report/page.tsx
rm web/src/components/ModeTabs.tsx
```

- [ ] **Step 2: Clean up types.ts — remove MODE_LABELS**

In `web/src/lib/types.ts`, delete:

```typescript
// DELETE these lines:
export const MODE_LABELS: Record<ChatMode, string> = {
  chat: '对话',
  sql: 'SQL',
  rag: '知识库',
  report: '报告',
}
```

Keep `ChatMode` type for internal use:

```typescript
export type ChatMode = 'chat' | 'sql' | 'rag' | 'report'
```

- [ ] **Step 3: Clean up layout.tsx — remove unused imports**

In `web/src/app/layout.tsx`, remove any ModeTabs-related imports if present (it's imported in Sidebar, not layout, so this may not need changes). Verify the file still compiles.

- [ ] **Step 4: Clean up empty parent directories**

```bash
# Remove empty sql/rag/report directories under app/
rmdir web/src/app/sql 2>/dev/null || true
rmdir web/src/app/rag 2>/dev/null || true
rmdir web/src/app/report 2>/dev/null || true
```

- [ ] **Step 5: Verify TypeScript compilation**

Run: `cd web && npx tsc --noEmit`
Expected: No errors. Fix any remaining references to deleted files.

- [ ] **Step 6: Smoke test — start dev server**

Run: `cd web && npm run dev`
Expected: Server starts, http://localhost:3000 loads the unified chat page. Enter a question, verify SSE streaming with Worker icons visible in ThinkingPanel.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete sql/rag/report pages and ModeTabs, clean up types"
```

---

## Verification Checklist

After all tasks are complete, verify end-to-end:

- [ ] `npm run dev` starts without errors
- [ ] Home page loads at `http://localhost:3000`
- [ ] EmptyState shows 4 unified examples with Worker icons
- [ ] Type a question → SSE streaming starts → ThinkingPanel shows Worker icons (📊/📚/📄/🧠)
- [ ] StatusBar shows real-time progress
- [ ] Final answer renders as Markdown
- [ ] Session list in sidebar works (create/rename/delete/switch)
- [ ] Collapsed sidebar shows expand button
- [ ] TypeScript: `npx tsc --noEmit` passes
- [ ] Build: `npm run build` succeeds
- [ ] Old URLs (/sql, /rag, /report) return 404 (expected)
