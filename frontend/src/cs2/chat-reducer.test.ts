// cs-workstation-v2 P1 T2：SSE 全事件类型归约断言。
import { describe, expect, it, vi } from 'vitest'
import { applySseEvent, beginTurn, createTyper, makeId } from './chatReducer'
import { newConversationDraft } from './types'
import type { SSEStreamEvent } from '@/api/chat'

function draftWithTurn() {
  const d = newConversationDraft('conv-1')
  beginTurn(d)
  return d
}

function evt(event: SSEStreamEvent['event'], data: Record<string, unknown>): SSEStreamEvent {
  return { event, data } as unknown as SSEStreamEvent
}

describe('applySseEvent 全事件消费', () => {
  it('meta 帧被消费且不产生副作用', () => {
    const d = draftWithTurn()
    expect(applySseEvent(d, evt('meta', { node_labels: {}, request_id: 'r', stream_id: 's', resume_supported: true }))).toBe('meta')
    expect(d.streaming).toBe(true)
    expect(d.messages).toHaveLength(1)
  })

  it('cs_ 前缀 status 节点进入 live 时间线并连续去重', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('status', { node: 'cs_graph_node', ts: 1 }))
    applySseEvent(d, evt('status', { node: 'cs_graph_node', ts: 2 }))
    applySseEvent(d, evt('status', { node: 'cs_supervisor', ts: 3 }))
    expect(d.liveTimeline).toEqual(['客服图执行', '客服调度'])
    expect(d.liveStep).toBe('客服调度')
  })

  it('非 cs_ 节点不进时间线', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('status', { node: 'router', ts: 1 }))
    expect(d.liveTimeline).toEqual([])
  })

  it('delta 累加到最后的 assistant 气泡', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('delta', { content: '您好', ts: 1 }))
    applySseEvent(d, evt('delta', { content: '，请问', ts: 2 }))
    expect(d.messages[d.messages.length - 1].content).toBe('您好，请问')
  })

  it('done：answer 兜底、时间线固化到气泡、streaming 结束、pending_action 信号', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('status', { node: 'cs_graph_node', ts: 1 }))
    applySseEvent(d, evt('status', { node: 'cs_reporter', ts: 2 }))
    applySseEvent(d, evt('done', { elapsed: 1.2, sources: [], answer: '查询完成', pending_action: { action_type: 'refund_request' } }))
    const m = d.messages[d.messages.length - 1]
    expect(m.content).toBe('查询完成')
    expect(m.csNodes).toEqual(['客服图执行', '客服汇总'])
    expect(m.streaming).toBe(false)
    expect(d.streaming).toBe(false)
    expect(d.pendingActionType).toBe('refund_request')
    expect(d.liveTimeline).toEqual([])
  })

  it('done 无 pending_action → 显式 null', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('done', { elapsed: 1, sources: [], answer: 'ok', pending_action: null }))
    expect(d.pendingActionType).toBeNull()
  })

  it('done 前已有 delta 内容时不被 answer 覆盖', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('delta', { content: '流式内容', ts: 1 }))
    applySseEvent(d, evt('done', { elapsed: 1, sources: [], answer: '全量', pending_action: null }))
    expect(d.messages[d.messages.length - 1].content).toBe('流式内容')
  })

  it('error REQUEST_CANCELLED 标记 interrupted，不冒充系统错误', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('error', { message: 'cancelled', code: 'REQUEST_CANCELLED', ts: 1 }))
    const m = d.messages[d.messages.length - 1]
    expect(m.interrupted).toBe(true)
    expect(m.role).toBe('assistant')
    expect(d.streaming).toBe(false)
  })

  it('error 其他 code → assistant 气泡转为系统错误提示', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('error', { message: '上游超时', code: 'UPSTREAM_TIMEOUT', retryable: true, ts: 1 }))
    const m = d.messages[d.messages.length - 1]
    expect(m.role).toBe('system')
    expect(m.content).toBe('上游超时')
  })

  it('paused 事件置 pausedText 并结束流式', () => {
    const d = draftWithTurn()
    expect(applySseEvent(d, evt('paused', { code: 'USER_BALANCE_INSUFFICIENT', message: '余额不足', action: 'top_up_and_resend', elapsed: 1, ts: 1 }))).toBe('paused')
    expect(d.pausedText).toBe('余额不足')
    expect(d.streaming).toBe(false)
  })

  it('clarification 存候选点选与问题', () => {
    const d = draftWithTurn()
    applySseEvent(d, evt('clarification', {
      question: '请选择订单',
      options: [{ id: 'option-1', label: 'DEMO-1012 耳机' }],
      handoff_available: true,
      source: 'refund_candidates',
      ts: 1,
    }))
    expect(d.candidateOptions).toEqual([{ id: 'option-1', label: 'DEMO-1012 耳机' }])
    expect(d.clarificationQuestion).toBe('请选择订单')
  })

  it('aux 事件（usage/todo/file/context/ping/handoff/thinking/log）消费不抛错', () => {
    const d = draftWithTurn()
    for (const name of ['usage', 'todo', 'file', 'context', 'ping', 'handoff', 'thinking'] as const) {
      expect(applySseEvent(d, evt(name, {}))).toBe(name)
    }
    expect(applySseEvent(d, evt('log', { level: 'info', node: 'router', step_id: 'understanding', message: '意图理解', payload: {}, ts: 1 }))).toBe('log')
  })
})

describe('beginTurn / createTyper', () => {
  it('beginTurn 清空候选并挂占位气泡', () => {
    const d = newConversationDraft()
    d.candidateOptions = [{ id: 'x', label: 'y' }]
    beginTurn(d)
    expect(d.streaming).toBe(true)
    expect(d.candidateOptions).toBeNull()
    expect(d.messages).toHaveLength(1)
    expect(d.messages[0].role).toBe('assistant')
  })

  it('makeId 生成非空唯一 id', () => {
    expect(makeId()).not.toBe(makeId())
  })

  it('createTyper 逐字揭示直到完成', async () => {
    vi.useFakeTimers()
    const seen: string[] = []
    const typer = createTyper((s) => seen.push(s), 10)
    const p = typer.run('abcd')
    await vi.advanceTimersByTimeAsync(100)
    await p
    expect(seen[seen.length - 1]).toBe('abcd')
    vi.useRealTimers()
  })

  it('createTyper skip 立即输出全文', async () => {
    vi.useFakeTimers()
    const seen: string[] = []
    const typer = createTyper((s) => seen.push(s), 10)
    const p = typer.run('abcdefgh')
    await vi.advanceTimersByTimeAsync(5)
    typer.skip()
    await vi.advanceTimersByTimeAsync(20)
    await p
    expect(seen[seen.length - 1]).toBe('abcdefgh')
    vi.useRealTimers()
  })
})
