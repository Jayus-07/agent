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

import FailedTracesTab, { traceToDomain } from './FailedTracesTab'

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

it('traceToDomain 纯函数能准确将 Trace 归入对应业务域', () => {
  expect(traceToDomain({ id: '1', request_id: 'r1', timestamp: '', workflow_name: 'rag_agent' } as any)).toBe('rag')
  expect(traceToDomain({ id: '2', request_id: 'r2', timestamp: '', workflow_name: 'knowledge_index' } as any)).toBe('rag')
  expect(traceToDomain({ id: '3', request_id: 'r3', timestamp: '', workflow_name: 'sql_eval' } as any)).toBe('sql')
  expect(traceToDomain({ id: '4', request_id: 'r4', timestamp: '', workflow_name: 'customer_service', source: 'cs' } as any)).toBe('cs')
  expect(traceToDomain({ id: '5', request_id: 'r5', timestamp: '', workflow_name: 'travel_planner', source: 'travel' } as any)).toBe('travel')
  expect(traceToDomain({ id: '6', request_id: 'r6', timestamp: '', workflow_name: 'agent', source: 'ai_assistant' } as any)).toBe('agent')
})

it('传入非 RAG 领域（如 CS）时，按对应 source 筛选并支持收录到 CS 业务域数据集', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  mocks.listTracesPage.mockResolvedValueOnce({
    traces: [{
      id: 'trace-cs-1', request_id: 'req-cs-1', timestamp: '2026-11-12T09:00:00Z', session_id: 'session-cs-1',
      question: '订单为什么被取消了？', answer_preview: '抱歉，系统检测到风控拦截。', answer_len: 15, duration_ms: 1200,
      model: { name: 'qwen', provider: 'provider' }, usage: {}, cost: {}, error: { message: '客服策略降级' },
      metadata: {}, spans: [], workflow_name: 'customer_service', status: 'degraded', source: 'cs',
    }],
    total: 1, offset: 0, limit: 10, has_more: false, retention_limit: 5000,
  })
  mocks.listV2Datasets.mockResolvedValueOnce([
    { dataset_id: 'dataset-rag-1', dataset_key: 'rag-support', name: 'RAG 数据集', domain: 'rag' },
    { dataset_id: 'dataset-cs-1', dataset_key: 'cs-rules', name: '客服决策层评测集', domain: 'cs' },
  ])
  mocks.createV2Candidate.mockResolvedValueOnce({ candidate_id: 'candidate-cs-88' })

  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><FailedTracesTab domain="cs" /></QueryClientProvider>) })
    await flushQueries()

    expect(mocks.listTracesPage).toHaveBeenCalledWith(expect.objectContaining({
      source: 'cs',
      limit: 10,
      offset: 0,
    }))
    expect(container.textContent).toContain('订单为什么被取消了？')

    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('查看 / 收录'))
    await act(async () => { open!.click() })
    await flushQueries()

    const submit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '创建待核验候选')
    await act(async () => { submit!.click() })
    await flushQueries()

    expect(mocks.createV2Candidate).toHaveBeenCalledWith(expect.objectContaining({
      source_kind: 'manual',
      payload: expect.objectContaining({
        input: { query: '订单为什么被取消了？' },
        observed_answer: '抱歉，系统检测到风控拦截。',
        metadata: expect.objectContaining({ module: 'cs', dataset_key: 'cs-rules' }),
        source_ref: expect.objectContaining({ trace_id: 'trace-cs-1', module: 'cs', dataset_key: 'cs-rules' }),
      }),
    }))
    expect(container.textContent).toContain('已创建待核验候选')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
  }
})
