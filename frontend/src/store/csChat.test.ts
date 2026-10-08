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
    pendingBySession: {},
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
    useCSChatStore.setState({ currentStatus: 'cs_graph_node', currentNode: 'cs_graph_node' })
    useCSChatStore.getState().addStreamEvent({ event: 'error', data: { message: '失败' } } as any, 'cs1')
    const state = useCSChatStore.getState()
    expect(state.currentStatus).toBe('')
    expect(state.currentNode).toBeNull()
  })
})

describe('addStreamEvent — CS 私有字段', () => {
  it('cs_ 节点追加 csTimeline 并识别业务意图（真实节点名）', () => {
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_graph_node' } } as any, 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_query_expert' } } as any, 'cs1')

    const state = useCSChatStore.getState()
    expect(state.csTimeline).toEqual(['cs_graph_node', 'cs_query_expert'])
    expect(state.intentDetected).toBe('业务查询')
    expect(state.currentNode).toBe('cs_query_expert')
  })

  it('cs_knowledge_expert 不触发意图识别', () => {
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_knowledge_expert' } } as any, 'cs1')
    expect(useCSChatStore.getState().intentDetected).toBeNull()
  })
})

describe('replaceLastAssistant — 终态写入', () => {
  it('写入完整内容 + csNodes（时间线在终态一次性落消息）', () => {
    useCSChatStore.getState().addMessage('user', '退货政策', 'cs1')
    useCSChatStore.getState().addMessage('assistant', '', 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'status', data: { node: 'cs_graph_node' } } as any, 'cs1')
    useCSChatStore.getState().addStreamEvent({ event: 'delta', data: { content: '七天内可退。' } } as any, 'cs1')

    useCSChatStore.getState().replaceLastAssistant('七天内可退。', 'cs1')

    const msgs = useCSChatStore.getState().sessions.find((s) => s.id === 'cs1')!.messages
    expect(msgs[1].content).toBe('七天内可退。')
    expect(msgs[1].csNodes).toEqual(['cs_graph_node'])
  })
})

describe('resetStream', () => {
  it('清空流式字段但保留会话', () => {
    useCSChatStore.setState({ deltaText: 'abc', currentStatus: 'cs_graph_node', csTimeline: ['cs_graph_node'] })
    useCSChatStore.getState().resetStream()
    const state = useCSChatStore.getState()
    expect(state.deltaText).toBe('')
    expect(state.currentStatus).toBe('')
    expect(state.csTimeline).toEqual([])
    expect(state.sessions).toHaveLength(1)
  })
})

describe('待确认操作的会话隔离', () => {
  const pending = {
    proposal_id: 'proposal-1',
    version: 3,
    action_type: 'refund',
    masked_target: '订单尾号 ****1234',
    summary: '为订单申请退款',
    expires_at: '2026-10-08T12:00:00Z',
    state: 'pending' as const,
  }

  it('不同会话各自恢复待确认状态，切换不会串卡', () => {
    useCSChatStore.getState().setPendingAction('cs1', pending)
    useCSChatStore.getState().setPendingAction('cs2', { ...pending, proposal_id: 'proposal-2' })
    useCSChatStore.getState().switchSession('cs2')

    expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(pending)
    expect(useCSChatStore.getState().pendingBySession.cs2?.proposal_id).toBe('proposal-2')
    expect(useCSChatStore.getState().pendingBySession[useCSChatStore.getState().currentId])
      .toMatchObject({ proposal_id: 'proposal-2' })
  })

  it('清理流式字段、创建新会话不丢已有会话的服务端待确认状态', () => {
    useCSChatStore.getState().setPendingAction('cs1', pending)
    useCSChatStore.getState().resetStream()
    useCSChatStore.getState().newSession()

    expect(useCSChatStore.getState().pendingBySession.cs1).toEqual(pending)
    expect(useCSChatStore.getState().pendingBySession[useCSChatStore.getState().currentId])
      .toBeUndefined()
  })

  it('显式 null 清除该会话状态，其他会话仍保留', () => {
    useCSChatStore.getState().setPendingAction('cs1', pending)
    useCSChatStore.getState().setPendingAction('cs2', pending)
    useCSChatStore.getState().setPendingAction('cs1', null)

    expect(useCSChatStore.getState().pendingBySession.cs1).toBeNull()
    expect(useCSChatStore.getState().pendingBySession.cs2).toEqual(pending)
  })
})
