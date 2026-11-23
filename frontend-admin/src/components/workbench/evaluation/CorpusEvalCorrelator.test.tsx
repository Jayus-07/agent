import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import CorpusEvalCorrelator, { CORPUS_36_DATA } from './CorpusEvalCorrelator'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

describe('CorpusEvalCorrelator', () => {
  it('正确展示 36 份基准语料概览与总卡片', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    act(() => {
      root.render(<CorpusEvalCorrelator />)
    })

    expect(container.textContent).toContain('已入库基准语料')
    expect(container.textContent).toContain('36 份')
    expect(container.textContent).toContain('TEST-CS-001_虚构客服退换与工单处理规则.md')
    expect(container.textContent).toContain('contract_gf2017_0201_cn.pdf')

    act(() => root.unmount())
    container.remove()
  })

  it('支持分类筛选与按需勾选发起评测', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    const onLaunchEval = vi.fn()

    act(() => {
      root.render(<CorpusEvalCorrelator onLaunchEval={onLaunchEval} />)
    })

    // 勾选第一个卡片
    const cards = container.querySelectorAll('.grid > div.cursor-pointer')
    expect(cards.length).toBe(36)

    act(() => {
      ;(cards[0] as HTMLElement).click()
    })

    expect(container.textContent).toContain('1 / 36 份')

    // 点击基于所选发起评测
    const launchBtn = Array.from(container.querySelectorAll('button')).find(btn =>
      btn.textContent?.includes('基于所选发起评测'),
    )
    expect(launchBtn).toBeDefined()

    act(() => {
      launchBtn?.click()
    })

    expect(onLaunchEval).toHaveBeenCalledWith([CORPUS_36_DATA[0].id])

    act(() => root.unmount())
    container.remove()
  })
})
