import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import MessageActions from './MessageActions'

vi.mock('@/api/feedback', () => ({
  feedbackService: { send: vi.fn() },
}))

import { feedbackService } from '@/api/feedback'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const mounted: { container: HTMLElement; root: Root }[] = []

function mount(props: Partial<React.ComponentProps<typeof MessageActions>> = {}) {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(
    <MessageActions
      content="答案"
      isUser={false}
      isLast
      sessionId="session-1"
      msgId="message-1"
      onRegenerate={vi.fn()}
      budgetBlocked
      {...props}
    />,
  ))
  mounted.push({ container, root })
  return container
}

afterEach(() => {
  vi.clearAllMocks()
  for (const { container, root } of mounted.splice(0)) {
    act(() => root.unmount())
    container.remove()
  }
})

describe('MessageActions 预算阻断', () => {
  it('硬额度下保留复制，但所有写副作用按钮不可执行', () => {
    const onRegenerate = vi.fn()
    const onEdit = vi.fn()
    const onResend = vi.fn()
    const container = mount({ isUser: true, onRegenerate, onEdit, onResend })

    const buttons = Array.from(container.querySelectorAll('button'))
    const copy = buttons.find((button) => button.title === '复制')
    const edit = buttons.find((button) => button.title === '编辑')
    const resend = buttons.find((button) => button.title === '重发')

    expect(copy?.disabled).toBe(false)
    expect(edit?.disabled).toBe(true)
    expect(resend?.disabled).toBe(true)

    act(() => {
      edit?.click()
      resend?.click()
    })
    expect(onEdit).not.toHaveBeenCalled()
    expect(onResend).not.toHaveBeenCalled()
  })

  it('硬额度下不允许重新生成、正负反馈，也不打开负反馈表单', () => {
    const onRegenerate = vi.fn()
    const container = mount({ onRegenerate })
    const buttons = Array.from(container.querySelectorAll('button'))
    const regenerate = buttons.find((button) => button.title === '重新生成')
    const positive = buttons.find((button) => button.title === '有用')
    const negative = buttons.find((button) => button.title === '无用')

    expect(regenerate?.disabled).toBe(true)
    expect(positive?.disabled).toBe(true)
    expect(negative?.disabled).toBe(true)

    act(() => {
      regenerate?.click()
      positive?.click()
      negative?.click()
    })
    expect(onRegenerate).not.toHaveBeenCalled()
    expect(feedbackService.send).not.toHaveBeenCalled()
    expect(container.textContent).not.toContain('告诉我们哪里需要改进')
  })
})
