import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import WelcomeState from './WelcomeState'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<WelcomeState onExampleClick={vi.fn()} budgetBlocked />))
  mounted.push({ container, root })
  return container
}

describe('WelcomeState 预算阻断', () => {
  it('硬额度下示例入口不可发起模型调用', () => {
    const container = mount()
    expect(Array.from(container.querySelectorAll('button')).every((button) => button.disabled)).toBe(true)
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})
