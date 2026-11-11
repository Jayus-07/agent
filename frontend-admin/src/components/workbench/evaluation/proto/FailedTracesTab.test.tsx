import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  listTracesPage: vi.fn(),
  listV2Datasets: vi.fn(),
  createV2Candidate: vi.fn(),
}))

vi.mock('@/api/observability', () => ({ listTracesPage: mocks.listTracesPage }))
vi.mock('@/api/evaluation', () => ({ evaluationService: {
  listV2Datasets: mocks.listV2Datasets,
  createV2Candidate: mocks.createV2Candidate,
} }))
vi.mock('@/api/client', () => ({ errMessage: (error: unknown) => error instanceof Error ? error.message : '请求失败' }))
vi.mock('@/lib/auth', () => ({ atLeast: () => true }))

import FailedTracesTab from './FailedTracesTab'

beforeEach(() => {
  mocks.listTracesPage.mockResolvedValue({
    traces: [{
      id: 'trace-error-1', request_id: 'req-1', timestamp: '2026-11-12T09:00:00Z', session_id: 'session-1',
      question: '退款多久到账？', answer_preview: '通常一天到账。', answer_len: 9, duration_ms: 850,
      model: { name: 'doubao', provider: 'provider' }, usage: {}, cost: {}, error: { message: '回答与证据不一致' },
      metadata: {}, spans: [], workflow_name: 'rag_agent', status: 'error', source: 'ai_assistant',
    }],
    total: 1, offset: 0, limit: 10, has_more: false, retention_limit: 5000,
  })
  mocks.listV2Datasets.mockResolvedValue([{
    dataset_id: 'dataset-rag-1', dataset_key: 'rag-support', name: '客服知识库回答集', domain: 'rag',
  }])
  mocks.createV2Candidate.mockResolvedValue({ candidate_id: 'candidate-1' })
})

afterEach(() => vi.clearAllMocks())

async function flushQueries() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

it('读取真实异常 Trace 并按数据集创建未核验候选，不把实际回答当作真值', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><FailedTracesTab /></QueryClientProvider>) })
    await flushQueries()
    expect(container.textContent).toContain('退款多久到账？')
    expect(mocks.listTracesPage).toHaveBeenCalledWith(expect.objectContaining({ status: 'error', limit: 10, offset: 0 }))

    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('查看 / 收录'))
    await act(async () => { open!.click() })
    await flushQueries()
    expect(container.textContent).toContain('不能作为标准答案')

    const submit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '创建待核验候选')
    await act(async () => { submit!.click() })
    await flushQueries()
    expect(mocks.createV2Candidate).toHaveBeenCalledWith(expect.objectContaining({
      source_kind: 'manual',
      idempotency_key: 'trace-trace-error-1-dataset-rag-1',
      payload: expect.objectContaining({
        input: { query: '退款多久到账？' },
        observed_answer: '通常一天到账。',
        metadata: expect.objectContaining({ source: 'production_trace', ground_truth_verified: false, dataset_key: 'rag-support' }),
        source_ref: expect.objectContaining({ trace_id: 'trace-error-1', dataset_key: 'rag-support' }),
      }),
    }))
    expect(container.textContent).toContain('已创建待核验候选')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
  }
})
