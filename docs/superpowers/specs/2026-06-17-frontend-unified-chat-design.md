# Frontend Refactor: Unified Chat Interface

**Date**: 2026-06-17
**Status**: Design approved, pending implementation plan
**Decision**: Scheme A — single-page unified chat, backend Multi-Agent auto-routing

## Goal

Merge four independent route pages (chat / sql / rag / report) into one chat-first interface. Backend `POST /chat/stream` SSE handles Worker routing; the frontend only needs a single input box. Users ask questions in natural language — the system figures out which Worker to invoke.

## File Changes

### Delete (4 files)

| File | Reason |
|---|---|
| `app/sql/page.tsx` (67 lines) | Independent SQL page, ~90% JSX duplicated with rag/report |
| `app/rag/page.tsx` (65 lines) | Independent RAG page, same pattern |
| `app/report/page.tsx` (72 lines) | Independent report page, same pattern + `dangerouslySetInnerHTML` unsanitized |
| `components/ModeTabs.tsx` (46 lines) | No longer needed; mode switching removed |

### Refactor (9 files)

| File | Change |
|---|---|
| `store/chat.ts` | Add `replaceLastAssistant(content, thinking?)` action. Remove `setMode`. |
| `hooks/useSSE.ts` | Replace 12-line manual `store.getState()` → `store.setState()` with single `replaceLastAssistant` call |
| `hooks/useChat.ts` | Remove `sendSync`. Only SSE path remains. |
| `components/ChatInput.tsx` | Decouple: accept `onSend` + `isLoading` props instead of calling `useSendMessage` internally |
| `components/ChatView.tsx` | Connect `ChatInput` via `onSend={send}` prop |
| `components/ThinkingPanel.tsx` | Replace 126-line `if/else` stage chain with data-driven `STAGE_RENDERERS` lookup (~40 lines). Show Worker icons (`📊 SQL / 📚 RAG / 📄 Report`) |
| `components/MessageBubble.tsx` | Use shared `parseProgress()` instead of local `getRunningStep` |
| `components/StatusBar.tsx` | Same — use shared `parseProgress()` |
| `components/EmptyState.tsx` | Merge 4 mode-specific configs into one unified example list covering all Worker types |

### Add (1 file)

| File | Purpose |
|---|---|
| `lib/sse-parser.ts` | Shared `parseProgress(events: SSEEvent[]): Progress` — extracts running/completed/failed/isDone from SSE event array. Used by both `MessageBubble` and `StatusBar`. |

## Key Design Decisions

### 1. Store: `replaceLastAssistant` action

`useSSE.ts` currently bypasses the Zustand store API — it reads `store.getState().sessions`, manually rebuilds the array, and writes via `store.setState()`. The new action encapsulates this mutation inside the store, keeping the SSE hook a thin consumer.

### 2. Shared SSE progress parser

`MessageBubble` and `StatusBar` each implement identical logic: iterate SSE events → build a `Map<string, StepInfo>` → compute running/completed/failed counts. Extract into `lib/sse-parser.ts` as `parseProgress()`. Both components become single-call consumers.

### 3. Data-driven ThinkingPanel

Current 126-line component has 4 separate JSX blocks for `planning` / `executing` / `done` / `error` stages. New approach: `STAGE_RENDERERS` map stage → `{ icon, label, detail, isError }`, rendering through a single layout. Adding a stage becomes a one-line map entry.

### 4. ChatInput decoupling

Current `ChatInput` calls `useSendMessage()` internally, making it unusable outside the chat flow. Accept `onSend: (text: string) => void` as a prop. `ChatView` wires it to the store.

### 5. EmptyState unification

Replace 4 mode-specific configs (`configs: Record<ChatMode, ...>`) with a single flat example list covering SQL / RAG / Report / complex analysis. One welcome screen for all use cases.

## Component Tree (after refactor)

```
layout.tsx
├── Sidebar                          ← session list only, no ModeTabs
└── main
    └── ChatView                     ← sole container
        ├── EmptyState               ← 4 unified examples
        ├── MessageList
        │   └── MessageBubble
        │       ├── MarkdownContent
        │       └── ThinkingPanel    ← data-driven, shows Worker icons
        ├── StatusBar                ← shared parseProgress()
        └── ChatInput                ← onSend prop
```

## What Does NOT Change

- `lib/api.ts` — All API functions preserved (sql/rag/report functions remain available for future direct-call use cases)
- `lib/types.ts` — `ChatMode` type kept internally; `MODE_LABELS` removed
- `tailwind.config.ts` — Unchanged
- `app/layout.tsx` — Only change: remove ModeTabs import
- `app/globals.css` — Unchanged
- All remaining components not listed above — Unchanged

## Metrics

| Metric | Before | After |
|---|---|---|
| Source files | 21 | 17 |
| Lines of code | ~1400 | ~900 (-35%) |
| Route pages | 4 | 1 |
| Duplicate JSX patterns | 3 copies (sql/rag/report) | 0 |
| SSE progress parsers | 2 copies | 1 shared |
| useSSE store bypass | 12 lines manual | 1 line action call |
| ThinkingPanel | 126 lines if/else | ~40 lines data-driven |
