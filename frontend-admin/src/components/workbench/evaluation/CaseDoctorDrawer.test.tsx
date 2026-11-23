import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import CaseDoctorDrawer, { type CaseDiagnosticData } from './CaseDoctorDrawer'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

describe('CaseDoctorDrawer', () => {
  it('正确渲染用例问题、期望答案、实际回答与知识切片', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    const mockCase: CaseDiagnosticData = {
      caseId: 'CASE-001',
      status: 'fail',
      durationMs: 350,
      question: '退换货时限是几天？',
      expectedAnswer: '自签收之日起 7 天内无理由退货。',
      actualAnswer: '平台支持 15 天内退货。',
      contexts: [
        {
          fileName: 'TEST-CS-001_虚构客服退换与工单处理规则.md',
          text: '根据规定，普通商品支持 7 天无理由退货...',
          score: 0.912,
        },
      ],
      metrics: {
        context_recall: 0.95,
        faithfulness: 0.45,
        answer_correctness: 0.5,
      },
    }

    const onClose = vi.fn()

    act(() => {
      root.render(<CaseDoctorDrawer caseData={mockCase} onClose={onClose} />)
    })

    expect(container.textContent).toContain('用例诊断 · CASE-001')
    expect(container.textContent).toContain('退换货时限是几天？')
    expect(container.textContent).toContain('自签收之日起 7 天内无理由退货。')
    expect(container.textContent).toContain('平台支持 15 天内退货。')
    expect(container.textContent).toContain('TEST-CS-001_虚构客服退换与工单处理规则.md')
    expect(container.textContent).toContain('忠实度 / 无幻觉率')
    expect(container.textContent).toContain('45.0%')

    act(() => root.unmount())
    container.remove()
  })

  it('在无数据时不挂载任何节点', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    act(() => {
      root.render(<CaseDoctorDrawer caseData={null} onClose={vi.fn()} />)
    })

    expect(container.innerHTML).toBe('')

    act(() => root.unmount())
    container.remove()
  })
})
