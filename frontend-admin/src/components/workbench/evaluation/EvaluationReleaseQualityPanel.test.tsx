import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService } from '@/api/evaluation'
import EvaluationReleaseQualityPanel from './EvaluationReleaseQualityPanel'

const authState = vi.hoisted(() => ({ role: 'super_admin' }))
vi.mock('@/lib/auth', () => ({
  atLeast: (minimum: string) => minimum === 'admin'
    ? authState.role === 'admin' || authState.role === 'super_admin'
    : authState.role === 'super_admin',
}))

let mounted: { root: Root; container: HTMLDivElement } | null = null

const run = {
  run_id: 'run-pass',
  suite_version_id: 'suite-v1',
  job_status: 'completed' as const,
  gate_verdict: 'PASS' as const,
  total_cases: 4,
  completed_cases: 4,
  passed_cases: 4,
}
const gate = {
  run_id: 'run-pass',
  job_status: 'completed',
  verdict: 'PASS',
  reasons: [],
  decisions: [{
    decision_id: 'gate-pass-1',
    run_id: 'run-pass',
    policy_hash: 'a'.repeat(64),
    verdict: 'PASS' as const,
    reasons_json: [],
    decided_by: 'system',
    decided_at: '2026-10-24T00:00:00Z',
  }],
  approvals: [],
}

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  authState.role = 'super_admin'
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue([run])
  vi.spyOn(evaluationService, 'getV2Gate').mockResolvedValue(gate)
  vi.spyOn(evaluationService, 'listV2AuditEvents').mockResolvedValue([])
  vi.spyOn(evaluationService, 'createV2GateApproval').mockResolvedValue({
    approval_id: 'approval-1',
    run_id: 'run-pass',
    gate_decision_id: 'gate-pass-1',
    decision: 'approved',
    release_reference: 'release:v2.4.1',
    rationale: '已核验回归、安全样本与发布范围。',
    decided_by: 'user:alice',
    decided_at: '2026-10-24T00:00:00Z',
  })
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

async function renderPanel() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  mounted = { root, container }
  await act(async () => {
    root.render(<EvaluationReleaseQualityPanel />)
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  return container
}

describe('EvaluationReleaseQualityPanel', () => {
  it('仅向超级管理员展示 PASS Gate 审批表单', async () => {
    authState.role = 'admin'
    const container = await renderPanel()

    expect(container.textContent).toContain('需要超级管理员权限提交发布审批。')
    expect(container.querySelector('input[aria-label="发布引用"]')).toBeNull()
    expect(container.querySelector('button')?.textContent).toBe('刷新')
  })

  it('将审批绑定到最新 PASS 裁决并展示追加记录', async () => {
    const container = await renderPanel()
    const reference = container.querySelector('input[aria-label="发布引用"]') as HTMLInputElement
    const rationale = container.querySelector('textarea[aria-label="审批理由"]') as HTMLTextAreaElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(reference, 'release:v2.4.1')
      reference.dispatchEvent(new Event('input', { bubbles: true }))
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(rationale, '已核验回归、安全样本与发布范围。')
      rationale.dispatchEvent(new Event('input', { bubbles: true }))
    })
    const approve = Array.from(container.querySelectorAll('button')).find(button => button.textContent === '批准放行')
    await act(async () => {
      approve?.click()
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(evaluationService.createV2GateApproval).toHaveBeenCalledWith('run-pass', expect.objectContaining({
      gate_decision_id: 'gate-pass-1',
      decision: 'approved',
      release_reference: 'release:v2.4.1',
      rationale: '已核验回归、安全样本与发布范围。',
    }))
    expect(container.textContent).toContain('审批记录已追加：批准放行')
    expect(container.textContent).toContain('该操作没有触发 CI 或部署。')
    expect(container.textContent).toContain('user:alice')
  })

  it('非 PASS 决策不显示审批表单', async () => {
    vi.spyOn(evaluationService, 'getV2Gate').mockResolvedValue({ ...gate, verdict: 'FAIL' })
    const container = await renderPanel()

    expect(container.textContent).toContain('仅 Gate PASS 的已完成运行可以发起人工发布审批。')
    expect(container.querySelector('input[aria-label="发布引用"]')).toBeNull()
  })
})
