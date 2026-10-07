/**
 * EvaluationResultsPanel 回归测试（Phase 2 §15/§16/§17/§36）。
 *
 * - §15 详情隔离：点击 B 加载失败时，页面必须显示 B 的失败态，
 *   绝不能残留上一条 run（A）的详情内容（「RAG 行头 + SQL 详情」事故）。
 * - §16 KPI 来源：KPI 取过滤后最新 VALID completed run 并显示来源行。
 * - §36 INVALID：环境无效 run 显示「环境无效」，不是红色 0%。
 *
 * 写法与 EvaluationCenter.test.tsx 一致：createRoot + act，不引 testing-library。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

// vi.mock 工厂会被提升到文件顶部——mock 函数必须经 vi.hoisted 创建
const getRunMock = vi.hoisted(() => vi.fn())
// toast 必须是稳定引用——每次渲染返回新对象会让 loadRuns 的 useCallback
// 身份漂移、useEffect 无限重跑（生产实现是稳定句柄）
const toastMock = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('@/api/evaluation', () => ({
  evaluationService: {
    listRuns: vi.fn(async () => [
      {
        run_id: 'run-sql-1', module: 'sql', status: 'completed',
        pass_rate: 0.9333, top1_accuracy: 0, faithfulness: 0,
        answer_correctness: 0, recall_at_5: 0, reject_accuracy: 0,
        mrr: 0, ndcg_at_10: 0, timestamp: '2026-10-06T02:22:53',
        validity: 'VALID', summary_source: 'postgres', suite: 'live',
      },
      {
        run_id: 'run-budget-1', module: 'sql', status: 'completed',
        pass_rate: 0, top1_accuracy: 0, faithfulness: 0,
        answer_correctness: 0, recall_at_5: 0, reject_accuracy: 0,
        mrr: 0, ndcg_at_10: 0, timestamp: '2026-10-06T12:06:13',
        validity: 'INVALID_BUDGET', invalid_reason: 'request_budget_exhausted',
        summary_source: 'postgres',
      },
      {
        run_id: 'run-rag-1', module: 'rag', status: 'completed',
        pass_rate: 1.0, top1_accuracy: 0.85, faithfulness: 0,
        answer_correctness: 0, recall_at_5: 0.92, reject_accuracy: 1,
        mrr: 0.89, ndcg_at_10: 0.9, timestamp: '2026-10-05T10:00:00',
        validity: 'VALID', summary_source: 'postgres', suite: 'ci_golden',
      },
    ]),
    getRun: getRunMock,
    runEval: vi.fn(),
    cancelRun: vi.fn(),
  },
}))

vi.mock('@/components/shared/Toast', () => ({ useToast: () => toastMock }))
vi.mock('@/components/shared/EmptyState', () => ({
  default: ({ title }: { title: string }) => <div>{title}</div>,
}))
vi.mock('@/components/evaluations/CaseSampleDetail', () => ({
  default: () => <div>case-sample</div>,
}))
vi.mock('next/dynamic', () => ({
  default: () => () => <div data-trend-charts />,
}))

import EvaluationResultsPanel from './EvaluationResultsPanel'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  document.body.innerHTML = ''
  getRunMock.mockReset()
})

function detailOf(runId: string, caseId: string) {
  return {
    run_id: runId,
    report: {
      results: [{
        case_id: caseId, module: 'sql', status: 'fail',
        expected: {}, actual: {}, metrics: {}, duration_ms: 12,
      }],
      tier_summaries: [],
    },
    meta: {},
    run_status: { status: 'completed' },
  }
}

function mount(): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<EvaluationResultsPanel />))
  return { root, container }
}

/** 等 loadRuns/handleSelectRun 的 async 链与重渲染跑完（微任务+定时器双保险） */
async function flush() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0))
    await new Promise(resolve => setTimeout(resolve, 0))
  })
}

async function clickRunRow(container: HTMLDivElement, runId: string) {
  const row = Array.from(container.querySelectorAll('button'))
    .find(b => b.textContent?.includes(runId))
  expect(row, `run row ${runId} 应存在`).toBeTruthy()
  await act(async () => {
    row!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    // 让 handleSelectRun 的 async 链路跑完
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('EvaluationResultsPanel（Phase 2 数据源收口）', () => {
  it('§16 KPI 跟随过滤条件取最新 VALID completed run，并显示来源', async () => {
    const { container } = mount()
    await flush()

    // 最新 VALID completed = run-sql-1（budget run 是 INVALID 被跳过）
    expect(container.textContent).toContain('当前指标来源')
    expect(container.textContent).toContain('SQL')
    expect(container.textContent).toContain('93.3%')
  })

  it('§36 INVALID run 显示「环境无效」而非红色 0%', async () => {
    const { container } = mount()
    await flush()
    expect(container.textContent).toContain('环境无效')
    expect(container.textContent).toContain('request_budget_exhausted')
  })

  it('§15 详情隔离：B 加载失败显示 B 的失败态，绝不残留 A 的内容', async () => {
    getRunMock.mockImplementation(async (runId: string) => {
      if (runId === 'run-sql-1') return detailOf(runId, 'CASE-SQL-A')
      throw new Error('report file corrupted')
    })
    const { container } = mount()
    await flush()

    // 1) 点开 A：正常展示 A 的 case
    await clickRunRow(container, 'run-sql-1')
    await flush()
    expect(container.textContent).toContain('CASE-SQL-A')

    // 2) 点开 B：请求失败
    await clickRunRow(container, 'run-budget-1')
    await flush()

    // 3) B 的失败态必须可见，且 A 的内容必须消失
    expect(container.textContent).toContain('加载 run-budget-1 详情失败')
    expect(container.textContent).not.toContain('CASE-SQL-A')
  })

  it('§15 竞态守卫：慢响应的 A 在切到 B 后被丢弃', async () => {
    let resolveA: (v: unknown) => void = () => {}
    getRunMock.mockImplementation(async (runId: string) => {
      if (runId === 'run-sql-1') {
        return new Promise(resolve => { resolveA = resolve })
      }
      return detailOf(runId, 'CASE-RAG-B')
    })
    const { container } = mount()
    await flush()

    await clickRunRow(container, 'run-sql-1')   // A 挂起
    await clickRunRow(container, 'run-rag-1')   // 切到 B，正常返回
    await flush()
    expect(container.textContent).toContain('CASE-RAG-B')

    // A 的慢响应此时才回来——必须被丢弃，不得覆盖 B
    await act(async () => {
      resolveA(detailOf('run-sql-1', 'CASE-SQL-A-LATE'))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(container.textContent).toContain('CASE-RAG-B')
    expect(container.textContent).not.toContain('CASE-SQL-A-LATE')
  })
})
