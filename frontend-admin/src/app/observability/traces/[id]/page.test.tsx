import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  getTraceDetail: vi.fn(),
  intakeV2TraceCandidate: vi.fn(),
  navigation: {
    pathname: '/observability/traces/trace-err-1',
    params: new URLSearchParams('returnTo=%2Fobservability%2Fmonitoring%3Ftab%3Dtraces'),
    router: { push: vi.fn(), replace: vi.fn() },
    id: 'trace-err-1',
  },
}))

vi.mock('@/api/observability', () => ({ getTraceDetail: mocks.getTraceDetail }))
vi.mock('@/api/evaluation', () => ({
  evaluationService: { intakeV2TraceCandidate: mocks.intakeV2TraceCandidate },
}))
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: mocks.navigation.id }),
  useRouter: () => mocks.navigation.router,
  useSearchParams: () => mocks.navigation.params,
}))
vi.mock('next/link', () => ({ default: ({ href, children }: { href: string; children: React.ReactNode }) => <a href={href}>{children}</a> }))

import TraceDetailPage from './page'

let mounted: { root: Root; container: HTMLDivElement } | null = null

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(async () => {
  if (mounted) await act(async () => { mounted?.root.unmount() })
  mounted?.container.remove()
  mounted = null
  mocks.getTraceDetail.mockReset()
  mocks.intakeV2TraceCandidate.mockReset()
})

const BASE = '2026-10-30T02:21:17.112Z'

function errorTrace() {
  return {
    id: 'trace-err-1',
    request_id: 'req-1',
    timestamp: BASE,
    session_id: 'sess-1',
    question: '退款流程走哪一步？大概要几个工作日',
    answer_preview: '抱歉，退款进度查询暂时不可用',
    answer_len: 40,
    duration_ms: 8100,
    model: { name: 'doubao-seed-2.0-mini', provider: 'volcengine' },
    usage: { total_tokens: 1618 },
    cost: {},
    cost_cny: 0.0006,
    error: { code: 'TOOL_EXEC_FAILED', message: 'calculate_tool 执行失败：模拟上游响应超时', class: 'tool_failure', error_node: 'calculate_tool', retry_count: 2 },
    metadata: {},
    status: 'error',
    workflow_name: 'rag_agent',
    sla: { threshold_ms: 10000, breached: false },
    spans: [
      { id: 'root', type: 'agent', name: 'RAG 链', parent_id: null, status: 'error', start_time: BASE, duration_ms: 8100, duration_ratio: 1, attributes: {}, metrics: { covered_ms: 4402, uncovered_ms: 3698 }, children: [], events: [], warnings: [], errors: [] },
      { id: 'tool', type: 'tool_call', name: 'calculate_tool', parent_id: 'root', status: 'error', start_time: BASE, duration_ms: 2, duration_ratio: 0, attributes: {}, metrics: { error_code: 'SIMULATED_TIMEOUT' }, children: [], events: [], warnings: [], errors: ['模拟上游响应超时'] },
      { id: 'llm', type: 'llm_call', name: '兜底回答生成', parent_id: 'root', status: 'success', start_time: BASE, duration_ms: 975, duration_ratio: 0, attributes: {}, metrics: {}, children: [], events: [], warnings: [], errors: [], llm_call: { model: 'doubao-seed-2.0-mini', temperature: 0, prompt_text: '兜底提示', response_text: '抱歉，退款进度查询暂时不可用', prompt_tokens: 655, completion_tokens: 355, cost_usd: 0, cost_cny: 0.0004 } },
    ],
    children_ids: [],
    source: '',
  }
}

async function mountDetail(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<TraceDetailPage />)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
  return container
}

/** 概要侧卡「Prompt 版本」行文本（行内 flex 布局，label 前缀 + 值）。 */
async function mountWithMetadata(metadata: Record<string, unknown>): Promise<string | undefined> {
  const t = errorTrace() as unknown as Record<string, unknown>
  t.metadata = metadata
  mocks.getTraceDetail.mockResolvedValue(t)
  const container = await mountDetail()
  return Array.from(container.querySelectorAll('div.flex.justify-between'))
    .find((el) => el.textContent?.startsWith('Prompt 版本'))
    ?.textContent
}

describe('Trace 详情页（四问布局重构版）', () => {
  it('展示问题、结果、错误码与错误消息，失败运行 SLA 显示未参与判定', async () => {
    mocks.getTraceDetail.mockResolvedValue(errorTrace())
    const container = await mountDetail()

    expect(container.textContent).toContain('退款流程走哪一步？大概要几个工作日')
    expect(container.textContent).toContain('抱歉，退款进度查询暂时不可用')
    expect(container.textContent).toContain('TOOL_EXEC_FAILED')
    expect(container.textContent).toContain('calculate_tool 执行失败：模拟上游响应超时')
    expect(container.textContent).toContain('SLA 未参与判定')
    // 未埋点黑洞可见
    expect(container.textContent).toContain('未埋点黑洞')
    // metadata 缺 prompt_versions → 如实「未采集」
    expect(Array.from(container.querySelectorAll('div.flex.justify-between'))
      .find((el) => el.textContent?.startsWith('Prompt 版本'))?.textContent).toBe('Prompt 版本未采集')
  })

  it('Prompt 版本兼容 map 快照形态（按 key 数展示）', async () => {
    expect(await mountWithMetadata({ prompt_versions: { 'rag.qa': 3, 'rag.contextualize': 1 } })).toBe('Prompt 版本2 个')
  })

  it('Prompt 版本兼容 list 形态（按元素数展示）', async () => {
    expect(await mountWithMetadata({ prompt_versions: [{ key: 'rag.qa', version: 3 }] })).toBe('Prompt 版本1 个')
  })

  it('Prompt 版本空 map / 空 list 如实显示未采集', async () => {
    expect(await mountWithMetadata({ prompt_versions: {} })).toBe('Prompt 版本未采集')
    expect(await mountWithMetadata({ prompt_versions: [] })).toBe('Prompt 版本未采集')
  })

  it('「加入候选评测集」调用 intake 端点一次并展示待审核回执', async () => {
    mocks.getTraceDetail.mockResolvedValue(errorTrace())
    mocks.intakeV2TraceCandidate.mockResolvedValue({ candidate_id: 'v2-cand-8f21', review_status: 'pending_review' })
    const container = await mountDetail()

    const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('将本题加入候选评测集'))
    expect(button).toBeTruthy()
    await act(async () => {
      button?.click()
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    expect(mocks.intakeV2TraceCandidate).toHaveBeenCalledTimes(1)
    expect(mocks.intakeV2TraceCandidate).toHaveBeenCalledWith('trace-err-1', 'trace-trace-err-1')
    expect(container.textContent).toContain('已创建评测候选 v2-cand-8f21')
    expect(container.textContent).toContain('待评测中心「候选审核」')
  })

  it('LLM 调用区渲染 llm_call span（名称/模型/Prompt）', async () => {
    mocks.getTraceDetail.mockResolvedValue(errorTrace())
    const container = await mountDetail()

    expect(container.textContent).toContain('兜底回答生成')
    expect(container.textContent).toContain('doubao-seed-2.0-mini')
    expect(container.textContent).toContain('兜底提示')
  })

  it('成功 Trace 不显示候选入口与错误卡，SLA 正常判定', async () => {
    const ok = errorTrace() as unknown as Record<string, unknown>
    ok.status = 'success'
    ok.error = {}
    ok.sla = { threshold_ms: 10000, breached: false }
    mocks.getTraceDetail.mockResolvedValue(ok)
    const container = await mountDetail()

    expect(container.textContent).not.toContain('将本题加入候选评测集')
    expect(container.textContent).toContain('✅ SLA 合规')
  })
})
