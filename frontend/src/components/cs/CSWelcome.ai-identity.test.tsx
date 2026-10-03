import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import CSWelcome from './CSWelcome'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(props: Partial<Parameters<typeof CSWelcome>[0]> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() =>
    root.render(
      <CSWelcome
        onQuickPrompt={vi.fn()}
        onRequestHandoff={vi.fn()}
        {...props}
      />,
    ),
  )
  mounted.push({ container, root })
  return container
}

// C12 AI 身份告知：开场显著标识 AI 属性 + 转人工入口常驻（生成式 AI 管理办法）
describe('CSWelcome AI 身份告知', () => {
  it('开场页显著标识「AI 智能客服」并带生成内容免责说明', () => {
    const container = mount()
    const badge = container.querySelector('[data-testid="cs-ai-badge"]')
    expect(badge).not.toBeNull()
    expect(badge?.textContent).toContain('AI 智能客服')
    expect(container.textContent).toContain('我是 AI 客服助手')
    expect(container.textContent).toContain('回复内容由 AI 生成')
  })

  it('转人工入口常驻可见（欢迎页即可达，禁用态有引导说明）', () => {
    const container = mount({ handoffDisabled: true })
    const handoff = container.querySelector('button[aria-label="转接人工客服"]') as HTMLButtonElement | null
    expect(handoff).not.toBeNull()
    expect(handoff?.disabled).toBe(true)
    expect(handoff?.title).toContain('请先发送一条消息')
  })

  it('可转人工时入口可用', () => {
    const container = mount({ handoffDisabled: false })
    const handoff = container.querySelector('button[aria-label="转接人工客服"]') as HTMLButtonElement | null
    expect(handoff).not.toBeNull()
    expect(handoff?.disabled).toBe(false)
  })
})

afterEach(() => {
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})
