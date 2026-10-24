import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { evaluationService, type EvaluationV2Run } from '@/api/evaluation'
import EvaluationV2RunsPanel from './EvaluationV2RunsPanel'

vi.mock('@/lib/auth', () => ({ atLeast: () => true }))
vi.mock('@/api/knowledge', () => ({ listKbAuthority: vi.fn(async () => ({ knowledge_bases: [] })) }))

let mounted: { root: Root; container: HTMLDivElement } | null = null
beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })
beforeEach(() => {
  const runs: EvaluationV2Run[] = [
    {
      run_id: 'run-unattributed', suite_version_id: 'suite-v1', job_status: 'completed',
      gate_verdict: 'PASS', total_cases: 1, completed_cases: 1, passed_cases: 1,
      cost_amount: null, cost_currency: null,
    },
    {
      run_id: 'run-zero-cost', suite_version_id: 'suite-v1', job_status: 'completed',
      gate_verdict: 'PASS', total_cases: 1, completed_cases: 1, passed_cases: 1,
      cost_amount: '0', cost_currency: 'USD',
    },
  ]
  vi.spyOn(evaluationService, 'listV2Runs').mockResolvedValue(runs)
  vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([])
  vi.spyOn(evaluationService, 'listV2RunResults').mockResolvedValue([])
})
afterEach(() => {
  mounted?.root.unmount()
  mounted?.container.remove()
  mounted = null
  vi.restoreAllMocks()
})

describe('EvaluationV2RunsPanel', () => {
  it('区分未归因费用和确认为零的费用', async () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => {
      root.render(<EvaluationV2RunsPanel />)
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(container.querySelector('th')?.textContent).toBe('Suite')
    expect(container.textContent).toContain('费用')
    expect(container.querySelector('td[aria-label="费用：未归因"]')?.textContent).toBe('未归因')
    expect(container.querySelector('td[aria-label="费用：0 USD"]')?.textContent).toBe('0 USD')
  })

  it('非 RAG Suite 不要求知识库，并明确显示契约检查语义', async () => {
    const suite = {
      name: '客服契约方案', module: 'cs', version: '1.0.0',
      suite_version_id: 'suite-cs-v1', dataset_version: '1.0.0', kb_id: '',
      fixture_set: '', case_count: 1, case_ids: [], trigger_classes: [],
      thresholds: {}, prompt_mapping: {}, tool_mapping: {},
      source: 'evaluation_v2' as const, status: 'published',
    }
    vi.spyOn(evaluationService, 'listSuites').mockResolvedValue([suite])
    const createRun = vi.spyOn(evaluationService, 'createV2Run').mockResolvedValue({
      run: {
        run_id: 'run-cs', suite_version_id: 'suite-cs-v1', job_status: 'queued',
        total_cases: 1, completed_cases: 0, passed_cases: 0,
      },
      task: {}, reused: false,
    })
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    mounted = { root, container }
    await act(async () => {
      root.render(<EvaluationV2RunsPanel />)
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(container.textContent).toContain('Gate 固定为 INVALID')
    expect(container.querySelector('select[aria-label="目标知识库"]')).toBeNull()
    const button = [...container.querySelectorAll('button')].find(item => item.textContent === '▶ 创建并运行实验')
    expect(button?.hasAttribute('disabled')).toBe(false)
    await act(async () => {
      button?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    expect(createRun).toHaveBeenCalledWith(expect.objectContaining({
      suite_version_id: 'suite-cs-v1',
    }))
    expect(createRun.mock.calls[0][0]).not.toHaveProperty('kb_id')
  })
})
