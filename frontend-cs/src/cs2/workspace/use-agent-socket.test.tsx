// cs-workstation-v2 P1 T2：坐席 WS 生命周期（ticket 鉴权/保活/事件分发/seq 游标/重连退避/重连对账）。
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

const api = vi.hoisted(() => ({ issueWsTicket: vi.fn() }))
vi.mock('./api', () => api)

let socketLog: { events: Array<{ type: string; seq: number | null }>; reconnects: number }
const events: Array<{ type: string; seq: number | null }> = []

class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []
  readyState = FakeWebSocket.OPEN
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((m: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED
  }
  open() {
    this.onopen?.()
  }
  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }
  drop() {
    this.onclose?.()
  }
}

vi.stubGlobal('WebSocket', FakeWebSocket)

import { useAgentSocket } from './useAgentSocket'

function Harness() {
  useAgentSocket(
    (evt) => {
      events.push({ type: evt.type, seq: evt.seq })
    },
    { onReconnect: () => socketLog.reconnects += 1 },
  )
  return null
}

beforeAll(() => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true
  api.issueWsTicket.mockResolvedValue({ ticket: 't-1', ws_path: '/ws/cs/agent', ttl: 60 })
})

let mounted: { container: HTMLElement; root: Root } | null = null

afterEach(() => {
  if (mounted) {
    act(() => mounted?.root.unmount())
    mounted.container.remove()
    mounted = null
  }
  FakeWebSocket.instances = []
  events.length = 0
  socketLog = { events, reconnects: 0 }
  vi.useRealTimers()
  vi.clearAllMocks()
  api.issueWsTicket.mockResolvedValue({ ticket: 't-1', ws_path: '/ws/cs/agent', ttl: 60 })
})

async function mount() {
  socketLog = { events, reconnects: 0 }
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  await act(async () => {
    root.render(<Harness />)
  })
  mounted = { container, root }
  return FakeWebSocket.instances
}

describe('useAgentSocket（cs2 坐席 WS）', () => {
  it('ticket 鉴权后建立连接，业务事件分发且 seq 游标推进', async () => {
    const instances = await mount()
    expect(api.issueWsTicket).toHaveBeenCalled()
    expect(instances[0].url).toContain('/ws/cs/agent?ticket=t-1')
    instances[0].emit({ type: 'message.created', seq: 5, event_id: 'e5', payload: { conversation_id: 'c1' }, ts: 'x' })
    expect(events).toEqual([{ type: 'message.created', seq: 5 }])
  })

  it('连接层帧（hello/pong/heartbeat）不分发', async () => {
    const instances = await mount()
    instances[0].emit({ type: 'hello', connections: 1 })
    instances[0].emit({ type: 'pong' })
    expect(events).toEqual([])
  })

  it('20s 保活 ping；断线后按 1s 退避重连并换新 ticket，重连触发对账回调', async () => {
    vi.useFakeTimers()
    const instances = await mount()
    instances[0].open()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000)
    })
    expect(instances[0].sent).toContain('ping')

    instances[0].drop()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(api.issueWsTicket).toHaveBeenCalledTimes(2)
    expect(FakeWebSocket.instances.length).toBe(2)
    FakeWebSocket.instances[1].open()
    expect(socketLog.reconnects).toBe(1)
  })

  it('事件 seq 只前进不回退', async () => {
    const instances = await mount()
    instances[0].emit({ type: 'message.created', seq: 9, payload: {} })
    instances[0].emit({ type: 'message.created', seq: 3, payload: {} })
    expect(events.map((e) => e.seq)).toEqual([9, 3])
  })
})
