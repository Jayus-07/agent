import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import CSMessageBubble from './CSMessageBubble'
import type { CSMessage } from '@/store/csChat'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(message: CSMessage) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<CSMessageBubble message={message} />))
  mounted.push({ container, root })
  return container
}

// C12 AI 身份告知：assistant 气泡必须显著标识 AI 生成，与 agent 气泡「人工客服」标签对位
describe('CSMessageBubble AI 身份标识', () => {
  it('assistant 气泡带 AI 客服标识', () => {
    const container = mount({ id: 'm1', role: 'assistant', content: '您好，请问有什么可以帮您？', timestamp: 0 })
    const label = container.querySelector('[data-testid="cs-ai-label"]')
    expect(label).not.toBeNull()
    expect(label?.textContent).toContain('AI 客服')
    expect(label?.textContent).toContain('人工智能生成')
  })

  it('agent 气泡不带 AI 标识，保留人工客服标识', () => {
    const container = mount({ id: 'm2', role: 'agent', content: '人工客服为您服务', timestamp: 0 })
    expect(container.querySelector('[data-testid="cs-ai-label"]')).toBeNull()
    expect(container.textContent).toContain('人工客服')
  })

  it('user 气泡不带 AI 标识', () => {
    const container = mount({ id: 'm3', role: 'user', content: '查订单', timestamp: 0 })
    expect(container.querySelector('[data-testid="cs-ai-label"]')).toBeNull()
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})
