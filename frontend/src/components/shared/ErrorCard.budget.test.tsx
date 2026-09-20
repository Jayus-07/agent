import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import ErrorCard from './ErrorCard'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('ErrorCard 预算阻断', () => {
  it('硬额度下不显示重试和转人工写入口', () => {
    const container = document.createElement('div')
    document.body.appendChild(container)
    const root = createRoot(container)
    act(() => root.render(
      <ErrorCard
        error={{ status: 429, detail: { retryable: true, handoff_available: true } }}
        onRetry={vi.fn()}
        onHandoff={vi.fn()}
        actionsDisabled
      />,
    ))
    mounted.push({ container, root })

    expect(container.textContent).not.toContain('重试')
    expect(container.textContent).not.toContain('转人工')
  })
})
