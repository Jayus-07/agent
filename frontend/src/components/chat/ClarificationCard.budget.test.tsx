import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import ClarificationCard from './ClarificationCard'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

describe('ClarificationCard 预算阻断', () => {
  it('硬额度下选项和转人工入口均不可执行', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    act(() => root.render(
      <ClarificationCard
        event={{
          question: '请选择处理方式',
          options: [{ id: 'a', label: '继续' }],
          handoff_available: true,
          ts: 1,
        }}
        onSelect={vi.fn()}
        onHandoff={vi.fn()}
        disabled
      />,
    ))
    mounted.push({ container, root })

    expect(Array.from(container.querySelectorAll('button')).every((button) => button.disabled)).toBe(true)
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})
