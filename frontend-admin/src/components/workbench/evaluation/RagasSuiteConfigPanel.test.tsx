import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import RagasSuiteConfigPanel, { DEFAULT_RAGAS_SUITE_CONFIG, type RagasSuiteConfig } from './RagasSuiteConfigPanel'

beforeAll(() => {
  ;(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

describe('RagasSuiteConfigPanel', () => {
  it('正确展示核心五维指标卡片与默认关闭的 Ragas 深度评测', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    const onChange = vi.fn()

    act(() => {
      root.render(<RagasSuiteConfigPanel value={DEFAULT_RAGAS_SUITE_CONFIG} onChange={onChange} />)
    })

    expect(container.textContent).toContain('评测指标与评估套件')
    expect(container.textContent).toContain('RAG 五维核心黄金量化指标')
    expect(container.textContent).toContain('联合 Ragas 深度评测套件')
    expect(container.textContent).not.toContain('评测深度等级')

    act(() => root.unmount())
    container.remove()
  })

  it('开启 Ragas 深度评测后展开等级切换并响应事件', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)

    let config: RagasSuiteConfig = {
      ...DEFAULT_RAGAS_SUITE_CONFIG,
      enableRagas: true,
      ragasLevel: 'standard',
    }
    const onChange = vi.fn((next: RagasSuiteConfig) => {
      config = next
    })

    act(() => {
      root.render(<RagasSuiteConfigPanel value={config} onChange={onChange} />)
    })

    expect(container.textContent).toContain('评测深度等级')
    expect(container.textContent).toContain('标准 (Standard)')
    expect(container.textContent).toContain('全量 (Full)')

    // 点击切换到 full
    const fullBtn = Array.from(container.querySelectorAll('button')).find(btn =>
      btn.textContent?.includes('全量 (Full)'),
    )
    expect(fullBtn).toBeDefined()

    act(() => {
      fullBtn?.click()
    })

    expect(onChange).toHaveBeenCalled()
    expect(config.ragasLevel).toBe('full')

    act(() => root.unmount())
    container.remove()
  })
})
