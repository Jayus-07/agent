'use client'

import { useMemo } from 'react'
import type { SSEStreamEvent } from '@/lib/types'

interface Props {
  /** SSE v2 流式事件列表，从中提取 token 用量和延迟 */
  streamEvents?: SSEStreamEvent[]
}

interface TokenData {
  inputTokens?: number
  outputTokens?: number
  totalTokens?: number
  latencySec?: number
}

/** 从 streamEvents 中提取 token 用量 */
function extractTokenData(events?: SSEStreamEvent[]): TokenData | null {
  if (!events?.length) return null

  let tokenData: TokenData = {}

  // 从 done 事件提取耗时
  const doneEvent = events.find(e => e.event === 'done')
  if (doneEvent && doneEvent.data) {
    tokenData.latencySec = (doneEvent.data as import('@/lib/types').DoneEvent).elapsed
  }

  // 从 log 事件提取 token 信息（LLM worker 或 reporter 的 payload）
  for (const e of events) {
    if (e.event !== 'log') continue
    const p = (e.data as import('@/lib/types').LogEvent).payload || {}
    const pt = Number(p.prompt_tokens || p.input_tokens) || 0
    const ct = Number(p.completion_tokens || p.output_tokens) || 0
    const tt = Number(p.total_tokens) || 0
    if (pt || ct || tt) {
      tokenData.inputTokens = (tokenData.inputTokens || 0) + pt
      tokenData.outputTokens = (tokenData.outputTokens || 0) + ct
      tokenData.totalTokens = (tokenData.totalTokens || 0) + (tt || pt + ct)
    }
  }

  // 至少要有时长或 token 数据才展示
  if (!tokenData.latencySec && !tokenData.totalTokens) return null
  return tokenData
}

export default function TokenInfo({ streamEvents }: Props) {
  const t = useMemo(() => extractTokenData(streamEvents), [streamEvents])

  if (!t) return null

  return (
    <div className="flex items-center gap-3 mt-1.5 text-[10px] text-text-muted">
      {t.inputTokens != null && <span>输入 {t.inputTokens.toLocaleString()}</span>}
      {t.outputTokens != null && <span>输出 {t.outputTokens.toLocaleString()}</span>}
      {t.totalTokens != null && <span>共计 {t.totalTokens.toLocaleString()} tokens</span>}
      {t.latencySec != null && <span className="ml-auto">{t.latencySec.toFixed(1)}s</span>}
    </div>
  )
}
