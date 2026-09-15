/** csChat store 回归测试 — 共享流式归约 + CS 私有字段 + 流式期间 sessions 稳定 */
import { describe, it, expect, beforeEach } from 'vitest'

import { useCSChatStore } from '@/store/csChat'

function resetStore() {
  useCSChatStore.setState({
    sessions: [{ id: 'cs1', title: '新客服会话', messages: [], createdAt: 1, updatedAt: 1 }],
    currentId: 'cs1',
    currentStatus: '',
    deltaText: '',
    nodeLabels: {},
    isLoading: false,
    error: null,
    currentRequestId: null,
    intentDetected: null,
    confirmationState: 'none',
    handoffState: 'none',
    currentNode: null,
    csTimeline: [],
  })
}

beforeEach(resetStore)

describe('addStreamEvent — 共享归约（stream-reduce）', () => {
  it('delta 事件累积 deltaText，且流式期间 sessions 引用不变（不再逐事件重建）', () => {
    useCSChatStore.getState().addMessage('user', '退货政策', 'cs1')
    useCSChatStore.getState().addMessage('assistant', '', 'cs1')
    const sessionsBefore = useCSChatStore.getState().sessions

    useCSChatStore.getState().addStreamEvent({ event: 'delta', data: { content: '七天内' } } as any, 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'delta', data: { content: '可退。' } } as any, 'cs1')

    const state = useCSChatStore.getState()
    expect(state.deltaText).toBe('七天内可退。')
    expect(state.sessions).toBe(sessionsBefore)
  })

  it('meta 事件下发 nodeLabels', () => {
    useCSChatStore.getState().addStreamEvent(
      { event: 'meta', data: { node_labels: { router: '🎯' } } } as any, 'cs1',
    )
    expect(useCSChatStore.getState().nodeLabels).toEqual({ router: '🎯' })
  })

  it('error 终态事件清空 currentStatus（StatusBar 残留修复）', () => {
    useCSChatStore.setState({ currentStatus: 'cs_knowledge', currentNode: 'cs_knowledge' })
    useCSChatStore.getState().addStreamEvent({ event: 'error', data: { message: '失败' } } as any, 'cs1')
    const state = useCSChatStore.getState()
    expect(state.currentStatus).toBe('')
    expect(state.currentNode).toBeNull()
  })
})

describe('addStreamEvent — CS 私有字段', () => {
  it('cs_ 节点追加 csTimeline 并识别业务意图', () => {
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_knowledge' } } as any, 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_business_query' } } as any, 'cs1')

    const state = useCSChatStore.getState()
    expect(state.csTimeline).toEqual(['cs_knowledge', 'cs_business_query'])
    expect(state.intentDetected).toBe('业务查询')
    expect(state.currentNode).toBe('cs_business_query')
  })

  it('cs_knowledge 不触发意图识别', () => {
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_knowledge' } } as any, 'cs1')
    expect(useCSChatStore.getState().intentDetected).toBeNull()
  })
})

describe('replaceLastAssistant — 终态写入', () => {
  it('写入完整内容 + csNodes（时间线在终态一次性落消息）', () => {
    useCSChatStore.getState().addMessage('user', '退货政策', 'cs1')
    useCSChatStore.getState().addMessage('assistant', '', 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_knowledge' } } as any, 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'delta', data: { content: '七天内可退。' } } as any, 'cs1')

    useCSChatStore.getState().replaceLastAssistant('七天内可退。', 'cs1')

    const msgs = useCSChatStore.getState().sessions.find((s) => s.id === 'cs1')!.messages
    expect(msgs[1].content).toBe('七天内可退。')
    expect(msgs[1].csNodes).toEqual(['cs_knowledge'])
  })
})

describe('resetStream', () => {
  it('清空流式字段但保留会话', () => {
    useCSChatStore.setState({ deltaText: 'abc', currentStatus: 'cs_knowledge', csTimeline: ['cs_knowledge'] })
    useCSChatStore.getState().resetStream()
    const state = useCSChatStore.getState()
    expect(state.deltaText).toBe('')
    expect(state.currentStatus).toBe('')
    expect(state.csTimeline).toEqual([])
    expect(state.sessions).toHaveLength(1)
  })
})
