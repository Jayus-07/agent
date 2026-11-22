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
vi.mock('@/api/prompts', () => ({
  promptsService: {
    list: async () => [],
    listAllReleases: async () => [],
    createRelease: async () => ({ release_id: 'rel-new' }),
  },
}))

function createTestQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } })
}

async function flushQueries() {
  for (let index = 0; index < 4; index += 1) {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
  }
}

async function mountRunsTab(props: Record<string, unknown> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const queryClient = createTestQueryClient()
  await act(async () => { root.render(<QueryClientProvider client={queryClient}><RunsTab {...props} /></QueryClientProvider>) })
  await flushQueries()
  return { root, container, queryClient }
}

async function cleanup(root: ReturnType<typeof createRoot>, container: HTMLElement, queryClient: QueryClient) {
  await act(async () => root.unmount())
  queryClient.clear()
  container.remove()
  vi.restoreAllMocks()
}

it('发起评测默认渲染 Prompt 发布评测，数据集方案入口已冻结', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    expect(container.querySelector('[aria-label="发布评测 Prompt"]')).not.toBeNull()
    // 批次 2：V2 方案评测发起入口冻结，不再出现数据集选择与对应子 tab
    expect(container.querySelector('[aria-label="数据集"]')).toBeNull()
    expect(container.textContent).not.toContain('数据集方案评测')
    expect(container.textContent).not.toContain('选择已发布数据集方案')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('SQL Prompt 锁定双跑文案、不出现评测方式选择；RAG 保留四选', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'list').mockResolvedValue([
    { key: 'sql.generator', name: 'SQL 生成 Prompt' },
    { key: 'rag.qa', name: 'RAG 问答 Prompt' },
  ] as Awaited<ReturnType<typeof promptsService.list>>)
  const create = vi.spyOn(promptsService, 'createRelease').mockResolvedValue({
    release_id: 'rel-new-1234567890', status: 'pending',
  } as Awaited<ReturnType<typeof promptsService.createRelease>>)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    const keySelect = container.querySelector('[aria-label="发布评测 Prompt"]') as HTMLSelectElement
    const selectSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    // RAG：四选评测方式真实生效（默认 self+ragas）
    await act(async () => { selectSetter.call(keySelect, 'rag.qa'); keySelect.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="发布评测方式"]')).not.toBeNull()
    expect((container.querySelector('[aria-label="发布评测方式"]') as HTMLSelectElement).value).toBe('self+ragas')
    // SQL：方式选择不出现，显示双跑锁定文案
    await act(async () => { selectSetter.call(keySelect, 'sql.generator'); keySelect.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="发布评测方式"]')).toBeNull()
    expect(container.textContent).toContain('SQL 走 canonical 双跑对比门')
    const versionInput = container.querySelector('[aria-label="发布评测候选版本"]') as HTMLInputElement
    const inputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => { inputSetter.call(versionInput, '2'); versionInput.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('开始评测'))!.click() })
    // SQL 固定提交 offline（与 UI 锁定文案一致），suite 标记 canonical
    expect(create).toHaveBeenCalledWith('sql.generator', 2, expect.objectContaining({
      executor: 'local', evaluation_mode: 'offline', suite: 'sql_canonical',
    }))
    // 提交成功后自动切到运行记录的发布评测来源（G 项引导）
    await flushQueries()
    expect((container.querySelectorAll('button[role="tab"]')[1] as HTMLButtonElement).getAttribute('aria-selected')).toBe('true')
    expect(container.textContent).toContain('该域暂无发布评测记录')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('domain 过滤：SQL 域只显示 sql.* Prompt，全部域按域分组渲染', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'list').mockResolvedValue([
    { key: 'sql.generator', name: 'SQL 生成 Prompt' },
    { key: 'rag.qa', name: 'RAG 问答 Prompt' },
    { key: 'router.llm', name: 'LLM 路由 Prompt' },
  ] as Awaited<ReturnType<typeof promptsService.list>>)
  const { root, container, queryClient } = await mountRunsTab({ domain: 'sql' })
  try {
    const keySelect = container.querySelector('[aria-label="发布评测 Prompt"]') as HTMLSelectElement
    const values = Array.from(keySelect.querySelectorAll('option')).map(option => option.value)
    expect(values).toContain('sql.generator')
    expect(values).not.toContain('rag.qa')
    expect(values).not.toContain('router.llm')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('发布评测运行记录按域过滤并显示域徽标', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    {
      release_id: 'rel-sql-1', prompt_key: 'sql.generator', version: 2,
      target_env: 'production', status: 'passed', eval_suite: 'sql_canonical_r3',
      executor: 'local', dataset_provenance: {}, metrics: { candidate_pass_rate: 0.4, current_pass_rate: 0.4 },
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T03:16:00Z',
    },
    {
      release_id: 'rel-rag-1', prompt_key: 'rag.qa', version: 2,
      target_env: 'production', status: 'running', eval_suite: 'desktop_upload_smoke',
      executor: 'local', dataset_provenance: {}, metrics: {},
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T07:11:00Z',
    },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab({ domain: 'sql' })
  try {
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('运行记录'))!.click() })
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('发布评测'))!.click() })
    await flushQueries()
    expect(container.textContent).toContain('sql.generator')
    expect(container.textContent).not.toContain('rag.qa')
    expect(container.textContent).toContain('SQL')
    expect(container.textContent).toContain('候选 40.0% · 现网 40.0%')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('门禁详情人类可读：ragas_applicable 显示不适用，candidate_regression 展开对比摘要', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([{
    release_id: 'rel-cbc7e63de348', prompt_key: 'sql.generator', version: 2,
    target_env: 'production', status: 'passed', eval_suite: 'sql_canonical_r3',
    executor: 'local', dataset_provenance: {}, metrics: {
      candidate_pass_rate: 0.4, current_pass_rate: 0.4,
      gate: {
        tier_pass: true, sample_pass: true, tier_gate_mode: 'display_only',
        ragas_applicable: false, absolute_floor_pass: true,
        candidate_regression: { threshold: 0.05, regression_pass: true, current_pass_rate: 0.4, candidate_pass_rate: 0.4 },
        candidate_regression_pass: true, blocked_rules: [],
      },
    },
    failure_reason: '', approved_by: 'user:3', published_by: '',
    created_at: '2026-11-22T03:16:00Z',
  } as never])
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('运行记录'))!.click() })
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('发布评测'))!.click() })
    await flushQueries()
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('门禁详情'))!.click() })
    expect(container.textContent).toContain('不适用（未接入 RAGAS）')
    expect(container.textContent).toContain('候选 40.0% vs 现网 40.0%')
    expect(container.textContent).toContain('回归通过')
    // 字符串值不再带 JSON 引号
    expect(container.textContent).toContain('tier_gate_mode: display_only')
    expect(container.textContent).not.toContain('"display_only"')
    expect(container.textContent).not.toContain('[object Object]')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('失败发布评测在状态徽标提示失败原因（含 CLI 本地执行失败区分）', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    {
      release_id: 'rel-local-fail', prompt_key: 'sql.generator', version: 3,
      target_env: 'production', status: 'failed', eval_suite: 'sql_canonical',
      executor: 'local', dataset_provenance: {}, metrics: {},
      failure_reason: '评测执行超时：canonical 双跑 worker 丢失',
      approved_by: '', published_by: '',
      created_at: '2026-11-22T10:07:00Z',
    },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab({ domain: 'sql' })
  try {
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('运行记录'))!.click() })
    await act(async () => { Array.from(container.querySelectorAll('button')).find(button => button.textContent?.includes('发布评测'))!.click() })
    await flushQueries()
    const badge = container.querySelector('span[title*="评测执行超时"]')
    expect(badge).not.toBeNull()
    expect(container.textContent).toContain('失败')
  } finally {
    await cleanup(root, container, queryClient)
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
  const { root, container, queryClient } = await mountRunsTab()
  try {
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
    await cleanup(root, container, queryClient)
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

  const { root, container, queryClient } = await mountRunsTab()
  try {
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
    await cleanup(root, container, queryClient)
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
    await cleanup(root, container, queryClient)
  }
})
