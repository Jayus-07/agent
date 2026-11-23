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

function clickText(container: HTMLElement, text: string, exact = false) {
  const button = Array.from(container.querySelectorAll('button')).find(b => exact ? b.textContent?.trim() === text : b.textContent?.includes(text))
  if (!button) throw new Error(`button "${text}" not found`)
  return act(async () => { button.click() })
}

it('发起评测默认渲染确认单，数据集方案入口冻结', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    expect(container.querySelector('[aria-label="发布评测 Prompt"]')).not.toBeNull()
    expect(container.querySelector('[aria-label="发起原因（可选）"]')).not.toBeNull()
    expect(container.querySelector('[aria-label="数据集"]')).toBeNull()
    expect(container.textContent).not.toContain('数据集方案评测')
    expect(container.textContent).not.toContain('选择已发布数据集方案')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('SQL Prompt 展示自动推导口径与锁定文案，提交 offline + 发起原因进审计', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([] as unknown as Awaited<ReturnType<typeof evaluationService.listSuites>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'list').mockResolvedValue([
    { key: 'sql.generator', name: 'SQL 生成 Prompt', latest_version_number: 3 },
    { key: 'rag.qa', name: 'RAG 问答 Prompt', latest_version_number: 1 },
  ] as Awaited<ReturnType<typeof promptsService.list>>)
  const create = vi.spyOn(promptsService, 'createRelease').mockResolvedValue({
    release_id: 'rel-new-1234567890', status: 'pending',
  } as Awaited<ReturnType<typeof promptsService.createRelease>>)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    const keySelect = container.querySelector('[aria-label="发布评测 Prompt"]') as HTMLSelectElement
    const selectSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!
    // RAG：四选评测方式真实生效
    await act(async () => { selectSetter.call(keySelect, 'rag.qa'); keySelect.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="发布评测方式"]')).not.toBeNull()
    // SQL：方式锁定 + 确认单摘要条
    await act(async () => { selectSetter.call(keySelect, 'sql.generator'); keySelect.dispatchEvent(new Event('change', { bubbles: true })) })
    expect(container.querySelector('[aria-label="发布评测方式"]')).toBeNull()
    expect(container.textContent).toContain('SQL 走 canonical 双跑对比门')
    const brief = container.querySelector('[aria-label="自动推导口径"]')
    expect(brief).not.toBeNull()
    expect(brief!.textContent).toContain('对比基线：现网 v3')
    expect(brief!.textContent).toContain('sql_canonical')
    const versionInput = container.querySelector('[aria-label="发布评测候选版本"]') as HTMLInputElement
    const inputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    await act(async () => { inputSetter.call(versionInput, '4'); versionInput.dispatchEvent(new Event('input', { bubbles: true })) })
    const noteInput = container.querySelector('[aria-label="发起原因（可选）"]') as HTMLInputElement
    await act(async () => { inputSetter.call(noteInput, '修复退款率口径回归'); noteInput.dispatchEvent(new Event('input', { bubbles: true })) })
    await clickText(container, '开始评测')
    expect(create).toHaveBeenCalledWith('sql.generator', 4, expect.objectContaining({
      executor: 'local', evaluation_mode: 'offline', suite: 'sql_canonical',
      dataset_version: expect.objectContaining({ note: '修复退款率口径回归' }),
    }))
    // 提交成功自动切执行记录（引导项）
    await flushQueries()
    expect((container.querySelectorAll('button[role="tab"]')[1] as HTMLButtonElement).getAttribute('aria-selected')).toBe('true')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('domain 过滤：SQL 域只显示 sql.* Prompt，全部域按域分组渲染', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
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

it('执行记录统一列表：三来源混排按时间倒序 + 来源徽标', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const v2Run = {
    run_id: 'run-v2-1', suite_version_id: 'suite-1', suite_name: '客服基线', suite_key: 'cs-baseline', suite_version: '2',
    job_status: 'completed', total_cases: 4, completed_cases: 4, passed_cases: 2, evaluation_mode: 'offline',
    module: 'cs', prompt_versions: {}, created_by: 'user:3', created_at: '2026-11-22T08:00:00Z', finished_at: '2026-11-22T08:01:00Z',
  } as Awaited<ReturnType<typeof evaluationService.listV2Runs>>[number]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([v2Run])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([
    { run_id: '2026-11-22T04-00', module: 'sql', pass_rate: 0.4, case_count: 15, status: 'completed', timestamp: '2026-11-22T12:05:00Z', trigger: 'manual' },
    { run_id: '2026-11-22T07-11', module: 'rag', pass_rate: 0.0, case_count: 8, status: 'completed', timestamp: '2026-11-22T15:13:00Z', trigger: 'prompt_publish' },
  ] as Awaited<ReturnType<typeof evaluationService.listRuns>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    {
      release_id: 'rel-b4677e33', prompt_key: 'sql.generator', version: 4,
      target_env: 'production', status: 'passed', eval_suite: 'sql_canonical',
      executor: 'local', dataset_provenance: {}, metrics: { candidate_pass_rate: 0.533, current_pass_rate: 0.4 },
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T13:47:00Z',
    },
    {
      release_id: 'rel-rag-run', prompt_key: 'rag.qa', version: 2,
      target_env: 'production', status: 'running', eval_suite: 'desktop_upload_smoke',
      executor: 'local', dataset_provenance: {}, metrics: {},
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T15:11:00Z',
    },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    // 三来源徽标全部出现
    expect(container.textContent).toContain('发布评测')
    expect(container.textContent).toContain('CLI 台账')
    expect(container.textContent).toContain('V2 运行')
    // 时间倒序：15:11 的 rag.qa release 排在 13:47 的 sql release 之前
    const body = container.querySelector('tbody')
    const rowTexts = Array.from(body?.querySelectorAll('tr') ?? []).map(tr => tr.textContent || '')
    const ragIdx = rowTexts.findIndex(t => t.includes('rag.qa') && t.includes('desktop_upload_smoke'))
    const sqlIdx = rowTexts.findIndex(t => t.includes('sql.generator') && t.includes('sql_canonical'))
    expect(ragIdx).toBeGreaterThanOrEqual(0)
    expect(sqlIdx).toBeGreaterThan(ragIdx)
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('SQL 域过滤：执行记录只显示 sql.* 行', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([
    { run_id: '2026-11-22T04-00', module: 'sql', pass_rate: 0.4, case_count: 15, status: 'completed', timestamp: '2026-11-22T12:05:00Z', trigger: 'manual' },
    { run_id: '2026-11-22T07-11', module: 'rag', pass_rate: 0.0, case_count: 8, status: 'completed', timestamp: '2026-11-22T15:13:00Z', trigger: 'prompt_publish' },
  ] as Awaited<ReturnType<typeof evaluationService.listRuns>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    {
      release_id: 'rel-b4677e33', prompt_key: 'sql.generator', version: 4,
      target_env: 'production', status: 'passed', eval_suite: 'sql_canonical',
      executor: 'local', dataset_provenance: {}, metrics: { candidate_pass_rate: 0.533, current_pass_rate: 0.4 },
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T13:47:00Z',
    },
    {
      release_id: 'rel-rag-run', prompt_key: 'rag.qa', version: 2,
      target_env: 'production', status: 'running', eval_suite: 'desktop_upload_smoke',
      executor: 'local', dataset_provenance: {}, metrics: {},
      failure_reason: '', approved_by: 'user:3', published_by: '',
      created_at: '2026-11-22T15:11:00Z',
    },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab({ domain: 'sql' })
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    // sql release 与 sql 台账行可见；rag 来源行被域过滤排除
    expect(container.textContent).toContain('sql.generator')
    expect(container.textContent).toContain('sql_canonical')
    expect(container.textContent).toContain('40.0% · 15 题')
    expect(container.textContent).not.toContain('rag.qa')
    expect(container.textContent).not.toContain('0.0% · 8 题')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('门禁详情行内展开人类可读，失败徽标提示原因', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    {
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
    },
    {
      release_id: 'rel-local-fail', prompt_key: 'sql.generator', version: 3,
      target_env: 'production', status: 'failed', eval_suite: 'sql_canonical',
      executor: 'local', dataset_provenance: {}, metrics: {},
      failure_reason: '评测执行超时：canonical 双跑 worker 丢失',
      approved_by: '', published_by: '',
      created_at: '2026-11-22T10:07:00Z',
    },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    // 精确点 sql_canonical_r3 行（有 gate 数据的那条）的门禁详情
    const gateBtn = Array.from(container.querySelectorAll('tr'))
      .find(tr => tr.textContent?.includes('sql_canonical_r3'))
      ?.querySelector('button')
    expect(gateBtn).not.toBeNull()
    await act(async () => { gateBtn!.click() })
    expect(container.textContent).toContain('不适用（未接入 RAGAS）')
    expect(container.textContent).toContain('候选 40.0% vs 现网 40.0%')
    expect(container.textContent).toContain('回归通过')
    expect(container.textContent).toContain('tier_gate_mode: display_only')
    expect(container.textContent).not.toContain('"display_only"')
    expect(container.textContent).not.toContain('[object Object]')
    // 失败行 title 提示失败原因
    expect(container.querySelector('span[title*="评测执行超时"]')).not.toBeNull()
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('CLI 台账行点逐题打开右侧抽屉，可关闭返回列表', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([
    { run_id: '2026-11-22T04-00', module: 'sql', pass_rate: 0.4, case_count: 15, status: 'completed', timestamp: '2026-11-22T12:05:00Z', trigger: 'manual' },
  ] as Awaited<ReturnType<typeof evaluationService.listRuns>>)
  vi.spyOn(evaluationService, 'getRun').mockResolvedValue({
    run_id: '2026-11-22T04-00',
    report: {
      timestamp: '2026-11-22T12:05:00Z', module: 'sql', mode: 'live', smoke: false, tier: 'default',
      summaries: [{ module: 'sql', total: 15, passed: 6, failed: 9, errors: 0, skipped: 0, pass_rate: 0.4, metrics: {} }],
      results: [
        { case_id: 'SC-001', module: 'sql', status: 'fail', expected: { gold_sql: 'SELECT 1' }, actual: { sql: 'SELECT 2' }, metrics: { execution_match: 0 }, duration_ms: 100, error_msg: '生成 SQL 结果集与 gold_sql 不一致', error_stage: null },
        { case_id: 'SC-004', module: 'sql', status: 'pass', expected: {}, actual: {}, metrics: {}, duration_ms: 50, error_msg: null, error_stage: null },
      ],
      tier_summaries: [],
      metadata: {},
      prompt_versions: {},
    },
    meta: {},
    run_status: { status: 'completed' },
  } as Awaited<ReturnType<typeof evaluationService.getRun>>)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    await clickText(container, '逐题')
    await flushQueries()
    const drawer = container.querySelector('[role="dialog"]')
    expect(drawer).not.toBeNull()
    expect(drawer?.textContent).toContain('评测详情')
    expect(drawer?.textContent).toContain('SC-001')
    expect(drawer?.textContent).toContain('6/15 通过')
    // 点击抽屉遮罩关闭（天然返回列表）
    const overlay = Array.from(container.querySelectorAll('button')).find(b => b.getAttribute('aria-label') === '关闭详情抽屉')
    expect(overlay).not.toBeNull()
    await act(async () => { overlay!.click() })
    expect(container.querySelector('[role="dialog"]')).toBeNull()
    expect(container.textContent).toContain('执行记录')
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
    started_at: null, finished_at: null, created_at: '2026-11-22T09:00:00Z',
  } as Awaited<ReturnType<typeof evaluationService.listV2Runs>>[number]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([run])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
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
    await clickText(container, '执行记录')
    await flushQueries()
    expect(container.textContent).toContain('线上采样')
    expect(container.textContent).toContain('PASS')
    await clickText(container, 'Gate 审批')
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

it('运行记录进入 V2 详情页，单条用例查看打开可关闭的右侧抽屉', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const run = {
    run_id: 'run-detail-1', suite_version_id: 'suite-1', suite_name: '客服基线', suite_key: 'cs-baseline', suite_version: '2',
    job_status: 'completed', total_cases: 1, completed_cases: 1, passed_cases: 0, evaluation_mode: null,
    snapshot_json: { evaluation_mode: 'semantic' },
    cost_amount: '0.01', cost_currency: 'USD', started_at: '2026-11-12T01:00:00Z', finished_at: '2026-11-12T01:00:03Z',
    created_at: '2026-11-12T01:00:00Z', module: 'cs', prompt_versions: {},
  } as Awaited<ReturnType<typeof evaluationService.listV2Runs>>[number]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([run])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
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
    await clickText(container, '执行记录')
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

// ── 批次 6c：台账去重 + 发布评测对比入口 ───────────────

const RELEASE_WITH_RUN = {
  release_id: 'rel-a', prompt_key: 'sql.generator', version: 4,
  target_env: 'production', status: 'passed', eval_suite: 'sql_canonical',
  executor: 'local', dataset_provenance: {}, metrics: {},
  failure_reason: '', approved_by: 'user:3', published_by: '',
  created_at: '2026-11-22T13:47:00Z', eval_run_id: 'run-a',
}

it('台账去重：prompt_publish 触发的台账默认隐藏，开关可显示全部', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([
    { run_id: '2026-11-22T04-00', module: 'sql', pass_rate: 0.4, case_count: 15, status: 'completed', timestamp: '2026-11-22T12:05:00Z', trigger: 'manual' },
    { run_id: '2026-11-22T07-11', module: 'sql', pass_rate: 0.4, case_count: 15, status: 'completed', timestamp: '2026-11-22T12:03:00Z', trigger: 'prompt_publish' },
  ] as Awaited<ReturnType<typeof evaluationService.listRuns>>)
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([])
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    // 默认：manual 行保留（发起方=manual），prompt_publish 行隐藏（与发布评测来源重复）
    expect(container.textContent).toContain('manual')
    expect(container.textContent).not.toContain('prompt_publish')
    // 勾选「显示全部台账」后两条都出现
    const checkbox = Array.from(container.querySelectorAll('input[type=checkbox]')).find(box => box.closest('label')?.textContent?.includes('显示全部台账')) as HTMLInputElement
    expect(checkbox).toBeTruthy()
    await act(async () => { checkbox.click() })
    expect(container.textContent).toContain('prompt_publish')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('发布评测行在同 prompt_key 有两条可选记录时出现「对比」入口', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listRuns').mockResolvedValue([])
  const { promptsService } = await import('@/api/prompts')
  vi.spyOn(promptsService, 'listAllReleases').mockResolvedValue([
    RELEASE_WITH_RUN,
    { ...RELEASE_WITH_RUN, release_id: 'rel-b', eval_run_id: 'run-b', version: 3 },
    // 另一个 prompt_key 只有单条：不出现对比按钮
    { ...RELEASE_WITH_RUN, release_id: 'rel-solo', prompt_key: 'rag.qa', eval_run_id: 'run-solo', version: 2 },
  ] as never)
  const { root, container, queryClient } = await mountRunsTab()
  try {
    await clickText(container, '执行记录')
    await flushQueries()
    // 同 key 两条记录（rel-a/rel-b）各带一个"对比"入口；rag.qa 单条记录无入口
    const compareButtons = Array.from(container.querySelectorAll('button')).filter(button => button.textContent?.trim() === '对比')
    expect(compareButtons).toHaveLength(2)
    await clickText(container, '对比')
    await flushQueries()
    const drawer = container.querySelector('[role="dialog"]')
    expect(drawer).not.toBeNull()
    expect(drawer?.textContent).toContain('发布评测对比')
  } finally {
    await cleanup(root, container, queryClient)
  }
})

it('发起评测表单挂载 RAG 五维黄金指标与联合 Ragas 可选评测配置', async () => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  const { root, container, queryClient } = await mountRunsTab({
    promptPrefill: { key: 'rag.qa', version: '2' },
    domain: 'rag',
  })
  try {
    expect(container.textContent).toContain('评测指标与评估套件')
    expect(container.textContent).toContain('RAG 五维核心黄金量化指标')
    expect(container.textContent).toContain('联合 Ragas 深度评测套件')
  } finally {
    await cleanup(root, container, queryClient)
  }
})
