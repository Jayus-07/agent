import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeAll, expect, it, vi } from 'vitest'
import { ApiError, request } from '@/api/client'
import ConversationsAdminPage from './ConversationsAdminPage'

vi.mock('@/api/client', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/client')>(),
  request: vi.fn(),
}))

const container = document.createElement('div')
let root: ReturnType<typeof createRoot> | undefined

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
})

afterEach(() => {
  act(() => root?.unmount())
  container.remove()
  vi.clearAllMocks()
})

it('对人工处理中的会话重派，展示满载原因并允许重试', async () => {
  const conversation = {
    conversation_id: 'test-conversation', user_id: 'test-user',
    summary: '派单验收', conversation_status: 'open', handling_mode: 'human_active',
    handoff_id: 'test-handoff', messages: [],
  }
  const reassign = vi.fn()
    .mockRejectedValueOnce(new ApiError('目标坐席已达到同时接单上限', 409))
    .mockResolvedValueOnce({ handoff_state: 'agent_offered' })
  vi.mocked(request).mockImplementation(async (path, options) => {
    if (path.startsWith('/api/cs/conversations?')) return { items: [conversation] }
    if (path === '/api/cs/conversations/test-conversation') return conversation
    if (path === '/api/cs/agents') return { agents: [{
      agent_id: 'test-agent', display_name: '验收坐席', skill: 'general',
      status: 'online', accepting: true, transferable: true,
    }] }
    if (path === '/api/cs/handoffs/test-handoff/reassign') return reassign(options)
    throw new Error(`意外请求 ${path}`)
  })

  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => { root?.render(<ConversationsAdminPage />) })
  const click = async (label: string) => {
    const button = Array.from(container.querySelectorAll('button'))
      .find((item) => item.textContent?.trim().startsWith(label))
    expect(button).toBeDefined()
    await act(async () => { button?.click() })
  }
  await click('派单验收')
  await click('重派')
  const dialog = container.querySelector('[role="dialog"]')!
  const reason = dialog.querySelector('textarea')!
  const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!
  await act(async () => {
    dialog.querySelector('input')?.click()
    setValue.call(reason, '专用会话验收')
    reason.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await click('发起重派')
  expect(reassign).toHaveBeenCalledWith(expect.objectContaining({
    method: 'POST',
    body: JSON.stringify({ target_agent_id: 'test-agent', reason: '专用会话验收' }),
  }))
  expect(dialog.textContent).toContain('目标坐席已达到同时接单上限')
  await click('发起重派')
  expect(container.querySelector('[role="dialog"]')).toBeNull()
  expect(container.textContent).toContain('已向指定坐席发出接单邀请')
})
