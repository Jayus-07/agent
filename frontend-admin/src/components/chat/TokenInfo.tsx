'use client'

import { useMemo } from 'react'
import type { SSEStreamEvent, TokenUsage } from '@/lib/types'

interface Props {
  /** SSE v2 流式事件列表，从中提取 token 用量和延迟（旧链路兜底） */
  streamEvents?: SSEStreamEvent[]
  /** 本轮请求 token 用量（done 事件写入 Message.usage，首选数据源） */
  usage?: TokenUsage
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

export default function TokenInfo({ streamEvents, usage }: Props) {
  const fromUsage = useMemo(() => {
    if (!usage || !usage.total_tokens) return null
    return {
      inputTokens: usage.prompt_tokens,
      outputTokens: usage.completion_tokens,
      totalTokens: usage.total_tokens,
      cachedTokens: usage.cached_tokens || 0,
      reasoningTokens: usage.reasoning_tokens || 0,
      calls: usage.calls || 0,
      latencySec: undefined as number | undefined,
    }
  }, [usage])

  const t = useMemo(
    () => fromUsage ?? extractTokenData(streamEvents),
    [fromUsage, streamEvents],
  )

  if (!t) return null

  const detail = t as typeof t & { cachedTokens?: number; reasoningTokens?: number; calls?: number }

  return (
    <div className="flex items-center flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-[10px] text-text-muted">
      {detail.inputTokens != null && <span>输入 {detail.inputTokens.toLocaleString()}</span>}
      {detail.outputTokens != null && <span>输出 {detail.outputTokens.toLocaleString()}</span>}
      {detail.totalTokens != null && <span>共计 {detail.totalTokens.toLocaleString()} tokens</span>}
      {!!detail.cachedTokens && <span title="缓存命中（计入输入）">缓存 {detail.cachedTokens.toLocaleString()}</span>}
      {!!detail.reasoningTokens && <span title="推理 token">推理 {detail.reasoningTokens.toLocaleString()}</span>}
      {!!detail.calls && detail.calls > 1 && <span>{detail.calls} 次调用</span>}
      {t.latencySec != null && <span className="ml-auto">{t.latencySec.toFixed(1)}s</span>}
    </div>
  )
}
