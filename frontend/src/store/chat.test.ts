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
    thinkingText: '',
    thinkingSeconds: null,
    thinkingStartAt: 0,
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

describe('addStreamEvent — thinking 思考链归约', () => {
  it('thinking 事件累积 thinkingText，不污染 deltaText', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '用户在问什么', ts: 1 } } as any,
      'local1',
    )
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '，应该查知识库', ts: 2 } } as any,
      'local1',
    )
    const state = useChatStore.getState()
    expect(state.thinkingText).toBe('用户在问什么，应该查知识库')
    expect(state.deltaText).toBe('')
  })

  it('首块 delta 到达后思考耗时定格（秒，下限 1s）', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '思考中', ts: 1 } } as any,
      'local1',
    )
    useChatStore.getState().addStreamEvent(
      { event: 'delta', data: { content: '回答', ts: 2 } } as any,
      'local1',
    )
    const state = useChatStore.getState()
    expect(state.thinkingSeconds).toBe(1)
    expect(state.deltaText).toBe('回答')
  })

  it('后续 delta 不再重复定格耗时', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '思考中', ts: 1 } } as any,
      'local1',
    )
    useChatStore.getState().addStreamEvent(
      { event: 'delta', data: { content: '回', ts: 2 } } as any,
      'local1',
    )
    useChatStore.getState().addStreamEvent(
      { event: 'delta', data: { content: '答', ts: 3 } } as any,
      'local1',
    )
    expect(useChatStore.getState().thinkingSeconds).toBe(1)
  })

  it('无思考链直接出回答时 thinkingSeconds 保持 null', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'delta', data: { content: '直接回答', ts: 1 } } as any,
      'local1',
    )
    const state = useChatStore.getState()
    expect(state.thinkingSeconds).toBeNull()
    expect(state.thinkingText).toBe('')
  })

  it('resetStream 清空思考链状态', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '思考中', ts: 1 } } as any,
      'local1',
    )
    useChatStore.getState().resetStream()
    const state = useChatStore.getState()
    expect(state.thinkingText).toBe('')
    expect(state.thinkingSeconds).toBeNull()
    expect(state.thinkingStartAt).toBe(0)
  })

  it('非当前会话的 thinking 事件不累积', () => {
    useChatStore.getState().addStreamEvent(
      { event: 'thinking', data: { content: '别的会话', ts: 1 } } as any,
      'other-session',
    )
    expect(useChatStore.getState().thinkingText).toBe('')
  })
})

describe('removeLastAssistant / replaceLastAssistant — 重新生成支撑', () => {
  function seedTurn() {
    useChatStore.getState().addMessage('user', '问题', 'local1')
    useChatStore.getState().addMessage('assistant', '旧回答', 'local1')
  }

  it('removeLastAssistant 只移除尾部 assistant，保留 user 提问', () => {
    seedTurn()
    useChatStore.getState().removeLastAssistant('local1')
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 'local1')!.messages
    expect(msgs).toHaveLength(1)
    expect(msgs[0].role).toBe('user')
  })

  it('尾部不是 assistant 时不动消息（幂等）', () => {
    useChatStore.getState().addMessage('user', '问题', 'local1')
    useChatStore.getState().removeLastAssistant('local1')
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 'local1')!.messages
    expect(msgs).toHaveLength(1)
  })

  it('replaceLastAssistant 固化 thinking 与耗时', () => {
    seedTurn()
    useChatStore.getState().replaceLastAssistant('新回答', 'local1', undefined, undefined, '思考全文', 8)
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 'local1')!.messages
    expect(msgs[1].content).toBe('新回答')
    expect(msgs[1].thinking).toBe('思考全文')
    expect(msgs[1].thinkingSeconds).toBe(8)
  })

  it('replaceLastAssistant 不传 thinking 时保留原值（error 中断路径不丢思考链）', () => {
    seedTurn()
    useChatStore.getState().replaceLastAssistant('新回答', 'local1', undefined, undefined, '思考全文', 8)
    useChatStore.getState().replaceLastAssistant('新回答 v2', 'local1')
    const msgs = useChatStore.getState().sessions.find((s) => s.id === 'local1')!.messages
    expect(msgs[1].thinking).toBe('思考全文')
    expect(msgs[1].thinkingSeconds).toBe(8)
  })
})
