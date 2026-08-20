/** chat store 回归测试 — 会话恢复 + 终态状态清理（2026-08-21 浏览器实测整改） */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('@/lib/api/memory', () => ({
  getSessionMessages: vi.fn(),
}))

import { useChatStore } from '@/store/chat'
import { getSessionMessages } from '@/lib/api/memory'

const mockedGetMessages = getSessionMessages as ReturnType<typeof vi.fn>

function resetStore() {
  useChatStore.setState({
    sessions: [{ id: 'local1', title: '新对话', mode: 'chat', messages: [], createdAt: 1, updatedAt: 1 }],
    currentId: 'local1',
    streamEvents: [],
    currentStatus: '',
    deltaText: '',
    nodeLabels: {},
    isLoading: false,
    error: null,
    historyError: null,
    currentRequestId: null,
  })
}

beforeEach(() => {
  resetStore()
  mockedGetMessages.mockReset()
})

describe('loadHistory — 会话恢复', () => {
  it('远程会话不在本地 store 时新建会话实体（修复恢复失效根因）', async () => {
    mockedGetMessages.mockResolvedValue([
      { role: 'user', content: '退货政策是什么？' },
      { role: 'assistant', content: '七天内可退。' },
    ])

    await useChatStore.getState().loadHistory('remote-session-1')

    const state = useChatStore.getState()
    const restored = state.sessions.find((s) => s.id === 'remote-session-1')
    expect(restored).toBeDefined()
    expect(restored!.messages).toHaveLength(2)
    expect(restored!.messages[0].content).toBe('退货政策是什么？')
    expect(restored!.title).toBe('退货政策是什么？')
    expect(state.currentId).toBe('remote-session-1')
    expect(state.historyError).toBeNull()
  })

  it('已存在的会话只替换消息，不重复创建', async () => {
    mockedGetMessages.mockResolvedValue([{ role: 'user', content: '旧问题' }])

    await useChatStore.getState().loadHistory('local1')

    const state = useChatStore.getState()
    expect(state.sessions.filter((s) => s.id === 'local1')).toHaveLength(1)
    expect(state.sessions.find((s) => s.id === 'local1')!.messages).toHaveLength(1)
  })

  it('超长首条消息截断为标题', async () => {
    const longQ = '这是一个非常非常长的提问内容'.repeat(10)
    mockedGetMessages.mockResolvedValue([{ role: 'user', content: longQ }])

    await useChatStore.getState().loadHistory('remote-long')

    const restored = useChatStore.getState().sessions.find((s) => s.id === 'remote-long')
    expect(restored!.title.length).toBeLessThanOrEqual(33) // 30 + '...'
    expect(restored!.title.endsWith('...')).toBe(true)
  })

  it('后端返回空消息时不创建空会话', async () => {
    mockedGetMessages.mockResolvedValue([])

    await useChatStore.getState().loadHistory('remote-empty')

    expect(useChatStore.getState().sessions.find((s) => s.id === 'remote-empty')).toBeUndefined()
  })

  it('加载失败记录 historyError 而非静默', async () => {
    mockedGetMessages.mockRejectedValue(new Error('PG 连接失败'))

    await useChatStore.getState().loadHistory('remote-broken')

    expect(useChatStore.getState().historyError).toBe('PG 连接失败')
  })
})

describe('addStreamEvent — 终态清理（StatusBar 残留修复）', () => {
  it('error 终态事件清空 currentStatus', () => {
    useChatStore.setState({ currentStatus: 'reporter' })
    useChatStore.getState().addStreamEvent(
      { event: 'error', data: { message: '失败' } } as any,
      'local1',
    )
    expect(useChatStore.getState().currentStatus).toBe('')
  })

  it('done 终态事件清空 currentStatus', () => {
    useChatStore.setState({ currentStatus: 'reporter' })
    useChatStore.getState().addStreamEvent(
      { event: 'done', data: { sources: [] } } as any,
      'local1',
    )
    expect(useChatStore.getState().currentStatus).toBe('')
  })

  it('status 事件正常更新 currentStatus', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'status', data: { node: 'planner' } } as any,
      'local1',
    )
    expect(useChatStore.getState().currentStatus).toBe('planner')
  })
})
