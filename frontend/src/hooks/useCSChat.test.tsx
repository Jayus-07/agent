import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const streamMock = vi.hoisted(() => ({ streamChat: vi.fn(), abortChat: vi.fn(async () => undefined) }))
const pendingMock = vi.hoisted(() => ({ getMyPendingAction: vi.fn() }))

vi.mock('@/api/chat', () => streamMock)
vi.mock('@/api/cs', () => pendingMock)

import { useCSChatStore } from '@/store/csChat'
import type { PendingActionSnapshot } from '@/api/cs'
import { useCSChat } from './useCSChat'

beforeAll(() => { (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true })

const original: PendingActionSnapshot = {
  proposal_id: 'proposal-old', version: 2, action_type: 'refund',
  masked_target: '订单 ****1234', summary: '旧退款摘要', expires_at: null, state: 'pending',
}
const latest: PendingActionSnapshot = {
  ...original, proposal_id: 'proposal-new', version: 3, summary: '新退款摘要',
}

function resetStore() {
  useCSChatStore.setState({
    sessions: [{ id: 'cs1', title: '客服会话', messages: [], createdAt: 1, updatedAt: 1 }],
    currentId: 'cs1', currentStatus: '', deltaText: '', nodeLabels: {}, isLoading: false,
    error: null, currentRequestId: null, intentDetected: null, confirmationState: 'none',
    handoffState: 'none', currentNode: null, csTimeline: [], pendingBySession: { cs1: original },
  })
}

function mount() {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  const api: { current: ReturnType<typeof useCSChat> | null } = { current: null }
  act(() => root.render(<Harness onApi={(value) => { api.current = value }} />))
  return { container, root, api }
}

function Harness({ onApi }: { onApi: (api: ReturnType<typeof useCSChat>) => void }) {
  onApi(useCSChat())
  return null
}

function streamDone(data: Record<string, unknown>) {
  streamMock.streamChat.mockImplementationOnce(async function* () {
    yield { event: 'done', data } as never
  })
}

describe('useCSChat pending SSE semantics', () => {
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

  it('done 缺少 pending_action 时保留当前会话状态', async () => {
    resetStore()
    mounted = mount()
    streamDone({ elapsed: 1 })

    await act(async () => { await mounted!.api.current!.startStream('查订单', 'cs1') })

    expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(original)
    expect(pendingMock.getMyPendingAction).not.toHaveBeenCalled()
  })

  it('done 显式 null 时仅清除当前会话状态', async () => {
    resetStore()
    useCSChatStore.getState().setPendingAction('cs2', latest)
    mounted = mount()
    streamDone({ elapsed: 1, pending_action: null })

    await act(async () => { await mounted!.api.current!.startStream('取消', 'cs1') })

    expect(useCSChatStore.getState().pendingBySession.cs1).toBeNull()
    expect(useCSChatStore.getState().pendingBySession.cs2).toEqual(latest)
  })

  it('done 带待确认信号时从服务端恢复版本化快照', async () => {
    resetStore()
    mounted = mount()
    pendingMock.getMyPendingAction.mockResolvedValue(latest)
    streamDone({ elapsed: 1, pending_action: { proposal_text: '显示用', action_type: 'refund' } })

    await act(async () => { await mounted!.api.current!.startStream('确认退款', 'cs1') })

    expect(pendingMock.getMyPendingAction).toHaveBeenCalledWith('cs1')
    expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(latest)
  })
})
