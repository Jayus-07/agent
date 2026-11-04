import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import RunsTab from './RunsTab'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: async () => ({ knowledge_bases: [{ kb_id: 'kb', active: true }] }) }))
vi.mock('@/api/prompts', () => ({ promptsService: { list: async () => [] } }))

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
  try {
    await act(async () => { root.render(<RunsTab />) })
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
  try {
    await act(async () => { root.render(<RunsTab />) })
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
    container.remove()
    vi.restoreAllMocks()
  }
})
