import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import type { TraceRecord } from '@/types/trace'
import TraceOverviewCard from './TraceOverviewCard'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
})

function makeTrace(tags: Record<string, string>): TraceRecord {
  return {
    id: 'trace-1',
    request_id: 'request-1',
    timestamp: new Date().toISOString(),
    session_id: '',
    question: '',
    answer_preview: '',
    answer_len: 0,
    duration_ms: 125,
    model: { name: 'model', provider: 'provider' },
    usage: {},
    cost: {},
    error: {},
    metadata: {},
    spans: [],
    tags,
    status: 'success',
  }
}

async function renderCard(trace: TraceRecord): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => { root.render(<TraceOverviewCard trace={trace} />) })
  return container
}

describe('TraceOverviewCard CS 路由诊断', () => {
  it('恢复灰度、目标路由、实际 Expert、路由不一致与转人工状态', async () => {
    const container = await renderCard(makeTrace({
      cs_variant: 'treatment',
      cs_target: 'cs_knowledge',
      cs_expert_final: 'cs_query_expert',
      cs_handoff_state: 'human_active',
    }))

    expect(container.textContent).toContain('CS Treatment')
    expect(container.textContent).toContain('路由 → cs_knowledge')
    expect(container.textContent).toContain('路由不一致')
    expect(container.textContent).toContain('cs_query_expert')
    expect(container.textContent).toContain('人工处理中')
  })

  it('拒绝展示非结构化标签内容', async () => {
    const container = await renderCard(makeTrace({
      cs_variant: 'treatment',
      cs_target: 'operator@example.com',
      cs_expert_final: 'operator@example.com',
      cs_handoff_state: 'person@example.com',
    }))

    expect(container.textContent).toContain('CS Treatment')
    expect(container.textContent).not.toContain('operator@example.com')
    expect(container.textContent).not.toContain('person@example.com')
  })
})
