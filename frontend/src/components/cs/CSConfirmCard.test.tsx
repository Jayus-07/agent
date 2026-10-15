import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import CSConfirmCard from './CSConfirmCard'
import type { PendingActionSnapshot } from '@/api/cs'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const snapshot: PendingActionSnapshot = {
  proposal_id: 'proposal-1',
  version: 2,
  action_type: 'refund',
  masked_target: '订单尾号 ****1234',
  summary: '为订单申请退款',
  expires_at: '2026-10-09T12:00:00Z',
  state: 'pending',
}

describe('CSConfirmCard', () => {
  let container: HTMLDivElement
  let root: Root

  afterEach(() => {
    if (root) act(() => root.unmount())
    container?.remove()
  })

  it('展示权威摘要、掩码目标与期限，并在处理中禁止重复提交', () => {
    const onConfirm = vi.fn()
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    act(() => root.render(
      <CSConfirmCard pending={snapshot} busy onConfirm={onConfirm} onCancel={vi.fn()} />,
    ))

    expect(container.textContent).toContain('为订单申请退款')
    expect(container.textContent).toContain('订单尾号 ****1234')
    expect(container.textContent).toContain('有效期至')
    const button = container.querySelector('[data-testid="cs-confirm-submit"]') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    act(() => button.click())
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('人工接管或过期时不允许操作', () => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    const onConfirm = vi.fn()

    act(() => root.render(
      <CSConfirmCard
        pending={{ ...snapshot, state: 'paused_handoff' }}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    ))

    expect(container.textContent).toContain('人工客服接管中')
    expect((container.querySelector('[data-testid="cs-confirm-submit"]') as HTMLButtonElement).disabled)
      .toBe(true)
    expect((container.querySelector('[data-testid="cs-confirm-cancel"]') as HTMLButtonElement).disabled)
      .toBe(true)
  })

  it('退款申请可单独修改原因，不直接改订单或金额', async () => {
    const onUpdateReason = vi.fn(async () => undefined)
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)

    act(() => root.render(
      <CSConfirmCard
        pending={{ ...snapshot, action_type: 'refund_request', reason: '商品有问题' }}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
        onUpdateReason={onUpdateReason}
      />,
    ))

    const edit = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('修改原因'))
    expect(edit).toBeDefined()
    await act(async () => { edit?.click() })
    const textarea = container.querySelector('textarea') as HTMLTextAreaElement
    expect(textarea.value).toBe('商品有问题')
    expect(container.textContent).toContain('订单与金额不会改变')

    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype, 'value',
    )?.set
    setter?.call(textarea, '包装破损')
    act(() => textarea.dispatchEvent(new Event('input', { bubbles: true })))
    const save = Array.from(container.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('保存原因'))
    await act(async () => { save?.click(); await Promise.resolve() })
    expect(onUpdateReason).toHaveBeenCalledWith('包装破损')
  })
})
