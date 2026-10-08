import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const apiMocks = vi.hoisted(() => ({
  listMyConversations: vi.fn(async () => []),
  listMyTickets: vi.fn(async () => []),
  getMyPendingAction: vi.fn(),
  confirmAction: vi.fn(),
  notifyUserTyping: vi.fn(async () => undefined),
  requestHandoff: vi.fn(),
}))

vi.mock('@/api/cs', () => apiMocks)
vi.mock('@/hooks/useCSHandoffSync', () => ({ useCSHandoffSync: vi.fn() }))
vi.mock('@/components/cs/CSWelcome', () => ({ default: () => <div /> }))
vi.mock('@/components/cs/CSMessageList', () => ({ default: () => <div /> }))
vi.mock('@/components/cs/CSStatusBar', () => ({ default: () => <div /> }))
vi.mock('@/components/cs/CSHandoffCard', () => ({ default: () => <div /> }))
vi.mock('@/components/cs/CSSatisfactionCard', () => ({ default: () => <div /> }))

import { ApiError } from '@/api/client'
import { useCSChatStore } from '@/store/csChat'
import type { PendingActionSnapshot } from '@/api/cs'
import CSDrawer from './CSDrawer'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const pending: PendingActionSnapshot = {
  proposal_id: 'proposal-1', version: 3, action_type: 'refund',
  masked_target: '订单尾号 ****1234', summary: '申请退款', expires_at: null, state: 'pending',
}
const latest: PendingActionSnapshot = { ...pending, version: 4, summary: '最新退款摘要' }

function resetStore(handoffState: 'none' | 'requested' | 'waiting' | 'active' = 'none') {
  useCSChatStore.setState({
    sessions: [{
      id: 'cs1', title: '订单咨询', createdAt: 1, updatedAt: 1,
      messages: [{ id: 'm1', role: 'assistant', content: '需要确认', timestamp: 1 }],
    }],
    currentId: 'cs1', currentStatus: '', deltaText: '', nodeLabels: {}, isLoading: false,
    error: null, currentRequestId: null, intentDetected: null, confirmationState: 'pending',
    handoffState, currentNode: null, csTimeline: [], pendingBySession: { cs1: pending },
    candidateOptions: null, handoffMeta: null, agentTyping: false,
  })
}

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => root.render(<CSDrawer open onClose={vi.fn()} />))
  return { container, root }
}

describe('CSDrawer pending confirmation', () => {
  let mounted: ReturnType<typeof mount> | undefined

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount())
      mounted!.container.remove()
      mounted = undefined
    }
    vi.clearAllMocks()
    resetStore()
  })

  it('同步拦截双击，确认请求只发一次', async () => {
    resetStore()
    apiMocks.getMyPendingAction.mockResolvedValue(pending)
    let resolveConfirm: ((value: unknown) => void) | undefined
    apiMocks.confirmAction.mockReturnValue(new Promise((resolve) => { resolveConfirm = resolve }))
    mounted = mount()
    await act(async () => { await Promise.resolve() })

    const button = mounted.container.querySelector('[data-testid="cs-confirm-submit"]') as HTMLButtonElement
    act(() => { button.click(); button.click() })

    expect(apiMocks.confirmAction).toHaveBeenCalledTimes(1)
    expect(apiMocks.confirmAction).toHaveBeenCalledWith(
      'cs1', pending, 'confirm', expect.any(String),
    )
    await act(async () => {
      resolveConfirm?.({ answer: '已提交', status: 'success' })
      await Promise.resolve()
    })
    expect(useCSChatStore.getState().pendingBySession.cs1).toBeNull()
  })

  it('409 后刷新权威快照并向用户解释状态已更新', async () => {
    resetStore()
    apiMocks.getMyPendingAction
      .mockResolvedValueOnce(pending) // 抽屉打开时恢复
      .mockResolvedValueOnce(latest) // 409 后读取最新快照
    apiMocks.confirmAction.mockRejectedValue(new ApiError('版本冲突', 409))
    mounted = mount()
    await act(async () => { await Promise.resolve() })

    const button = mounted.container.querySelector('[data-testid="cs-confirm-submit"]') as HTMLButtonElement
    await act(async () => { button.click(); await Promise.resolve(); await Promise.resolve() })

    expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(latest)
    expect(useCSChatStore.getState().currentMessages().at(-1)?.content)
      .toContain('确认信息已更新')
  })

  it('切换客服会话后展示并读取目标会话的 Pending', async () => {
    resetStore()
    useCSChatStore.setState({
      sessions: [
        useCSChatStore.getState().sessions[0],
        {
          id: 'cs2', title: '第二个客服会话', createdAt: 2, updatedAt: 2,
          messages: [{ id: 'm2', role: 'assistant', content: '第二个待确认', timestamp: 2 }],
        },
      ],
      pendingBySession: { cs1: pending, cs2: latest },
    })
    apiMocks.getMyPendingAction.mockImplementation(async (sessionId: string) =>
      sessionId === 'cs2' ? latest : pending,
    )
    mounted = mount()
    await act(async () => { await Promise.resolve() })

    const selector = mounted.container.querySelector(
      'select[aria-label="切换客服会话"]',
    ) as HTMLSelectElement | null
    expect(selector).not.toBeNull()
    await act(async () => {
      selector!.value = 'cs2'
      selector!.dispatchEvent(new Event('change', { bubbles: true }))
      await Promise.resolve()
    })

    expect(useCSChatStore.getState().currentId).toBe('cs2')
    expect(useCSChatStore.getState().pendingBySession.cs2).toEqual(latest)
    expect(mounted.container.textContent).toContain('最新退款摘要')
  })

  it.each(['waiting', 'active'] as const)('%s 时禁用确认卡和 AI 输入', async (state) => {
    resetStore(state)
    apiMocks.getMyPendingAction.mockResolvedValue({ ...pending, state: 'paused_handoff' })
    mounted = mount()
    await act(async () => { await Promise.resolve() })

    expect((mounted.container.querySelector('[data-testid="cs-confirm-submit"]') as HTMLButtonElement).disabled)
      .toBe(true)
    expect((mounted.container.querySelector('textarea') as HTMLTextAreaElement).disabled)
      .toBe(true)
  })
})
