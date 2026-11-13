import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { evaluationService } from '@/api/evaluation'
import EvaluationV2RunCaseDetailButton from '../EvaluationV2RunCaseDetailButton'
import RunsTab from './RunsTab'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: async () => ({ knowledge_bases: [{ kb_id: 'kb', active: true }] }) }))
vi.mock('@/api/prompts', () => ({ promptsService: { list: async () => [] } }))

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
}

async function flushQueries() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

it('从 RAG 切换到客服后隐藏方式并固定提交 offline，再切回保留 RAG 选择', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue(['rag', 'cs', 'travel', 'travel-provider'].map(module => ({
    name: module, module, version: '1', suite_version_id: module,
    source: 'evaluation_v2', status: 'published', case_count: 1,
  })) as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const create = vi.spyOn(evaluationService, 'createV2Run').mockResolvedValue({ run: { run_id: 'run' } } as Awaited<ReturnType<typeof evaluationService.createV2Run>>)
  vi.spyOn(evaluationService, 'getV2Run').mockResolvedValue({ fingerprint: null } as unknown as Awaited<ReturnType<typeof evaluationService.getV2Run>>)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><RunsTab /></QueryClientProvider>) })
    await flushQueries()
    expect(container.querySelector('[aria-label="评测方式"]')).not.toBeNull()
    const suite = container.querySelector('[aria-label="数据集"]') as HTMLSelectElement
    await act(async () => { suite.value = 'cs'; suite.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="评测方式"]')).toBeNull()
    expect(container.textContent).toContain('离线评测（按方案执行）')
    expect(container.textContent).not.toContain('在线模式将真实调用模型')
    await act(async () => { Array.from(container.querySelectorAll('button')).find(item => item.textContent?.includes('开始运行'))!.click() })
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ suite_version_id: 'cs', evaluation_mode: 'offline' }))
    for (const module of ['travel', 'travel-provider']) {
      await act(async () => { suite.value = module; suite.dispatchEvent(new Event('change', { bubbles: true })) })
      expect(container.querySelector('[aria-label="评测方式"]')).toBeNull()
      expect(container.textContent).not.toContain('contract_only')
      expect(container.textContent).toContain('离线评测（按方案执行）')
    }
    await act(async () => { suite.value = 'rag'; suite.dispatchEvent(new Event('change', { bubbles: true })) })
    expect((container.querySelector('[aria-label="评测方式"]') as HTMLSelectElement).value).toBe('self+ragas')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('从数据集配置入口进入运行页时预选刚发布的方案版本', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([
    { name: '旧方案', module: 'rag', version: '1.0.0', suite_version_id: 'suite-old', source: 'evaluation_v2', status: 'published', case_count: 1 },
    { name: '新方案', module: 'rag', version: '1.0.0', suite_version_id: 'suite-new', source: 'evaluation_v2', status: 'published', case_count: 1 },
  ] as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><RunsTab focusSuiteVersionId="suite-new" /></QueryClientProvider>) })
    await flushQueries()
    expect((container.querySelector('[aria-label="数据集"]') as HTMLSelectElement).value).toBe('suite-new')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('展示线上采样与 Gate 裁决，并提交最新 PASS 的 Gate 审批', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const run = {
    run_id: 'run-online-1', suite_version_id: 'suite-1', suite_name: '线上评测', suite_key: 'online-suite', suite_version: '1',
    job_status: 'completed', source: 'online', gate_verdict: 'PASS', gate_reason: [{ metric: 'pass_rate', value: 1 }],
    total_cases: 1, completed_cases: 1, passed_cases: 1, evaluation_mode: 'offline', cost_amount: 0,
    started_at: null, finished_at: null,
  } as Awaited<ReturnType<typeof evaluationService.listV2Runs>>[number]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([run])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([])
  vi.spyOn(evaluationService, 'getV2Gate').mockResolvedValue({
    run_id: run.run_id, job_status: 'completed', verdict: 'PASS', reasons: [],
    decisions: [{ decision_id: 'gate-decision-1', run_id: run.run_id, policy_hash: 'hash', verdict: 'PASS', reasons_json: [], decided_by: 'admin', decided_at: '2026-11-05T00:00:00Z' }],
    approvals: [],
  })
  const createApproval = vi.spyOn(evaluationService, 'createV2GateApproval').mockResolvedValue({
    approval_id: 'approval-1', run_id: run.run_id, gate_decision_id: 'gate-decision-1', decision: 'approved',
    release_reference: 'release/2026.11', rationale: '通过门禁并完成发布核验', decided_by: 'admin', decided_at: '2026-11-05T00:00:00Z',
  })
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><RunsTab /></QueryClientProvider>) })
    const historyTab = Array.from(container.querySelectorAll<HTMLButtonElement>('button[role="tab"]')).find(button => button.textContent?.includes('运行记录'))
    await act(async () => { historyTab!.click() })
    await flushQueries()
    expect(container.textContent).toContain('线上采样')
    expect(container.textContent).toContain('PASS')

    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('Gate 审批'))
    expect(open).toBeDefined()
    await act(async () => { open!.click() })
    expect(evaluationService.getV2Gate).toHaveBeenCalledWith(run.run_id)

    const reference = container.querySelector('[aria-label="Gate 发布引用"]') as HTMLInputElement
    const rationale = container.querySelector('[aria-label="Gate 审批理由"]') as HTMLTextAreaElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(reference, 'release/2026.11')
      reference.dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(rationale, '通过门禁并完成发布核验')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const submit = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '提交 Gate 审批')
    expect(submit).toBeDefined()
    await act(async () => { submit!.click() })
    expect(createApproval).toHaveBeenCalledWith(run.run_id, expect.objectContaining({
      gate_decision_id: 'gate-decision-1', decision: 'approved', release_reference: 'release/2026.11', rationale: '通过门禁并完成发布核验',
    }))
    expect(container.textContent).toContain('Gate 发布意见已记录：批准')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('运行记录进入详情页面，单条用例查看打开可关闭的右侧抽屉', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const run = {
    run_id: 'run-detail-1', suite_version_id: 'suite-1', suite_name: '客服基线', suite_key: 'cs-baseline', suite_version: '2',
    job_status: 'completed', total_cases: 1, completed_cases: 1, passed_cases: 0, evaluation_mode: null,
    snapshot_json: { evaluation_mode: 'semantic' },
    cost_amount: '0.01', cost_currency: 'USD', started_at: '2026-11-12T01:00:00Z', finished_at: '2026-11-12T01:00:03Z',
  } as Awaited<ReturnType<typeof evaluationService.listV2Runs>>[number]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([run])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([])
  vi.spyOn(evaluationService, 'getV2Run').mockResolvedValue({ run, fingerprint: { runner: 'eval-v2', generation_model: 'doubao' } })
  vi.spyOn(evaluationService, 'listV2RunResults').mockResolvedValue([{
    result_id: 'result-1', case_id: 'case-1', case_revision: 3, status: 'fail', scores: { overall: 0.4 }, failure_stage: 'answer_quality', latency_ms: 230,
  }])
  vi.spyOn(evaluationService, 'getV2RunCaseDetail').mockResolvedValue({
    run_id: run.run_id, case_id: 'case-1', case_revision: 3,
    result: {
      status: 'fail',
      expected: { query: '退款多久到账？', reference_answer: '3–7 个工作日', evidence: [{ doc_id: 'refund-policy', quote: '退款处理需要 3–7 个工作日。' }] },
      actual: {
        generated_answer: '两个工作日',
        pipeline: { query_type: 'multi_hop_specific' },
        details: [{ title: '退款规则', source: 'refund-policy.md', snippet: '退款处理需要 3–7 个工作日。', rerank_score: 0.91 }],
      },
      metrics: { answer_quality: 0.4, 'recall@5': 0.5, multi_hop_success: 0.5 },
      duration_ms: 230,
    },
    trace_id: 'trace-case-1', latency_ms: 230, cost_amount: '0.01', cost_currency: 'USD', cost_status: 'available',
  })

  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => { root.render(<QueryClientProvider client={queryClient}><RunsTab /></QueryClientProvider>) })
    const historyTab = Array.from(container.querySelectorAll<HTMLButtonElement>('button[role="tab"]')).find(button => button.textContent?.includes('运行记录'))
    await act(async () => { historyTab!.click() })
    await flushQueries()
    const openRun = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '运行详情')
    expect(openRun).toBeDefined()
    await act(async () => { openRun!.click() })
    await flushQueries()
    expect(container.textContent).toContain('运行环境指纹')
    expect(container.textContent).toContain('运行方式在线自研')
    const openCase = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '查看')
    expect(openCase).toBeDefined()
    await act(async () => { openCase!.click() })
    await flushQueries()
    const drawer = container.querySelector('[role="dialog"]')
    expect(drawer).not.toBeNull()
    expect(drawer?.textContent).toContain('退款多久到账？')
    expect(drawer?.textContent).toContain('两个工作日')
    expect(drawer?.textContent).toContain('RAG 题型：多跳具体')
    expect(drawer?.textContent).toContain('退款处理需要 3–7 个工作日。')
    expect(drawer?.textContent).toContain('检索结果与命中证据')
    expect(drawer?.textContent).toContain('检索质量评分')
    expect(drawer?.textContent).toContain('答案质量评分')
    expect(drawer?.textContent).toContain('answer_quality')
    expect(drawer?.textContent).toContain('recall@5')
    expect(drawer?.textContent).toContain('multi_hop_success')
    expect(drawer?.textContent).toContain('0.5000')
    expect(drawer?.textContent).toContain('标准证据')
    expect(drawer?.textContent).toContain('trace-case-1')

    const close = Array.from(container.querySelectorAll('button')).find(button => button.getAttribute('aria-label') === '关闭用例详情')
    await act(async () => { close!.click() })
    expect(drawer?.parentElement?.getAttribute('aria-hidden')).toBe('true')
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})

it('逐题报告缺失时给出明确提示并隐藏无效重试', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const message = '该运行的逐题报告文件未找到，无法查看完整回答和证据。请重新运行评测生成新报告。'
  vi.spyOn(evaluationService, 'getV2RunCaseDetail').mockRejectedValue(new ApiError(message, 404))

  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  try {
    await act(async () => {
      root.render(<QueryClientProvider client={queryClient}>
        <EvaluationV2RunCaseDetailButton runId="run-missing-artifact" caseId="case-1" />
      </QueryClientProvider>)
    })
    const open = Array.from(container.querySelectorAll('button')).find(button => button.textContent?.trim() === '查看')
    await act(async () => { open!.click() })
    await flushQueries()

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(message)
    expect(Array.from(container.querySelectorAll('button')).some(button => button.textContent?.includes('重新读取'))).toBe(false)
  } finally {
    await act(async () => root.unmount())
    queryClient.clear()
    container.remove()
    vi.restoreAllMocks()
  }
})
