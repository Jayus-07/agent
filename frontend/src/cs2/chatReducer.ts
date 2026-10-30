// cs-workstation-v2 P1：SSE 事件 → 会话草稿状态的纯归约器。
// 全事件类型消费（meta/status/log/delta/thinking/clarification/handoff/done/
// error/paused/usage/todo/file/context/ping），不依赖 React，便于 T2 断言。
import type { SSEStreamEvent } from '@/api/chat'
import {
  nodeLabel,
  type Cs2CandidateOption,
  type Cs2ConversationDraft,
  type Cs2Message,
} from './types'

export function makeId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `m-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function draftMessage(draft: Cs2ConversationDraft): Cs2Message | undefined {
  for (let i = draft.messages.length - 1; i >= 0; i -= 1) {
    if (draft.messages[i].role === 'assistant') return draft.messages[i]
  }
  return undefined
}

/** 用户发送后：置流式态并挂一个占位机器人气泡（typing 点点） */
export function beginTurn(draft: Cs2ConversationDraft): void {
  draft.streaming = true
  draft.liveTimeline = []
  draft.liveStep = null
  draft.errorText = null
  draft.pausedText = null
  draft.candidateOptions = null
  draft.clarificationQuestion = null
  draft.messages.push({
    id: makeId(),
    role: 'assistant',
    content: '',
    createdAt: Date.now(),
    streaming: true,
  })
}

/** 归约单条 SSE 事件；返回值仅用于测试观察，状态直接改在 draft 上 */
export function applySseEvent(
  draft: Cs2ConversationDraft,
  evt: SSEStreamEvent,
): string {
  switch (evt.event) {
    case 'meta':
      return 'meta'
    case 'status': {
      // CS 主链路 SSE 只下发主图节点名；cs_ 前缀节点进时间线
      if (evt.data.node?.startsWith('cs_')) {
        const label = nodeLabel(evt.data.node)
        if (draft.liveTimeline[draft.liveTimeline.length - 1] !== label) {
          draft.liveTimeline.push(label)
        }
        draft.liveStep = label
      }
      return 'status'
    }
    case 'log':
      // 调度/检索细节当前仅作状态栏提示，不进固化时间线
      if (evt.data.message) draft.liveStep = draft.liveStep ?? nodeLabel(evt.data.node ?? '')
      return 'log'
    case 'delta': {
      const m = draftMessage(draft)
      if (m) m.content += evt.data.content ?? ''
      return 'delta'
    }
    case 'thinking':
      return 'thinking'
    case 'clarification': {
      const opts: Cs2CandidateOption[] = (evt.data.options ?? []).map((o) => ({
        id: o.id,
        label: o.label,
      }))
      draft.candidateOptions = opts.length ? opts : null
      draft.clarificationQuestion = evt.data.question ?? null
      return 'clarification'
    }
    case 'handoff':
      // 域引导卡（非转人工）；P1 仅消费，不渲染
      return 'handoff'
    case 'done': {
      const answer = evt.data.answer
      const m = draftMessage(draft)
      if (m) {
        if (answer && !m.content) m.content = answer
        if (draft.liveTimeline.length) m.csNodes = [...draft.liveTimeline]
        m.streaming = false
      }
      draft.streaming = false
      draft.liveStep = null
      draft.liveTimeline = []
      const pa = evt.data.pending_action
      draft.pendingActionType = pa ? String(pa.action_type ?? '') : null
      return 'done'
    }
    case 'error': {
      const code = evt.data.code ?? ''
      const m = draftMessage(draft)
      if (m) {
        m.streaming = false
        if (code === 'REQUEST_CANCELLED') {
          m.interrupted = true
          if (!m.content) m.content = ''
        } else if (!m.content) {
          m.content = evt.data.message ?? '抱歉，本次处理出现问题，请重试。'
          m.role = 'system'
        }
      }
      draft.streaming = false
      draft.liveStep = null
      draft.errorText = evt.data.message ?? null
      return 'error'
    }
    case 'paused': {
      draft.pausedText = evt.data.message ?? null
      draft.streaming = false
      return 'paused'
    }
    case 'usage':
    case 'todo':
    case 'file':
    case 'context':
    case 'ping':
      return evt.event
    default:
      return 'unknown'
  }
}

/** 本地逐字揭示（CS 后端兜底单帧全量 delta 的打字机效果） */
export function createTyper(
  onTick: (shown: string) => void,
  intervalMs = 24,
): { run(full: string): Promise<void>; skip(): void } {
  let skipped = false
  return {
    run(full: string) {
      return new Promise((resolve) => {
        let shown = 0
        const timer = setInterval(() => {
          if (skipped || shown >= full.length) {
            clearInterval(timer)
            onTick(full)
            resolve()
            return
          }
          shown = Math.min(full.length, shown + 2)
          onTick(full.slice(0, shown))
        }, intervalMs)
      })
    },
    skip() {
      skipped = true
    },
  }
}
