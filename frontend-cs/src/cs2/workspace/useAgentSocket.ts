'use client'

// cs-workstation-v2 P1：坐席 WS 封装（cs2 自有实现，不复用旧命名空间的 WS 模块）。
// 机制与既有契约对齐：ticket 鉴权 → /ws/cs/agent → 封套 {type,event_id,seq,ts}；
// 断线指数退避重连（每次换新 ticket），重连成功触发 onReconnect 供上层对账补发。
import { useEffect, useRef, useState } from 'react'
import { issueWsTicket } from './api'

export interface AgentSocketEvent {
  type: string
  seq: number | null
  eventId?: string
  payload: Record<string, unknown>
  ts?: string
}

interface Options {
  onReconnect?: () => void
}

const PING_INTERVAL_MS = 20_000
const MAX_BACKOFF_MS = 30_000

export function useAgentSocket(
  onEvent: (evt: AgentSocketEvent) => void,
  opts: Options = {},
): { connected: boolean; lastSeq: number } {
  const [connected, setConnected] = useState(false)
  const [lastSeq, setLastSeq] = useState(0)
  const eventRef = useRef(onEvent)
  eventRef.current = onEvent
  const reconnectRef = useRef(opts.onReconnect)
  reconnectRef.current = opts.onReconnect
  const seqRef = useRef(0)

  useEffect(() => {
    let disposed = false
    let ws: WebSocket | null = null
    let pingTimer: ReturnType<typeof setInterval> | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let retry = 0
    let everConnected = false

    const clearPing = () => {
      if (pingTimer) clearInterval(pingTimer)
      pingTimer = null
    }

    const connect = async () => {
      if (disposed) return
      let ticket: string
      let wsPath: string
      try {
        const t = await issueWsTicket()
        ticket = t.ticket
        wsPath = t.ws_path || '/ws/cs/agent'
      } catch {
        scheduleRetry()
        return
      }
      if (disposed) return
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const url = `${proto}://${location.host}${wsPath}?ticket=${encodeURIComponent(ticket)}`
      ws = new WebSocket(url)

      ws.onopen = () => {
        retry = 0
        setConnected(true)
        clearPing()
        pingTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping')
        }, PING_INTERVAL_MS)
        if (everConnected) reconnectRef.current?.()
        everConnected = true
      }

      ws.onmessage = (msg) => {
        if (typeof msg.data !== 'string' || !msg.data.startsWith('{')) return
        try {
          const data = JSON.parse(msg.data) as Record<string, unknown>
          const type = String(data.type ?? '')
          if (type === 'hello' || type === 'pong' || type === 'heartbeat') return
          const seq = typeof data.seq === 'number' ? data.seq : null
          if (seq !== null && seq > seqRef.current) {
            seqRef.current = seq
            setLastSeq(seq)
          }
          eventRef.current({
            type,
            seq,
            eventId: typeof data.event_id === 'string' ? data.event_id : undefined,
            payload: (data.payload ?? {}) as Record<string, unknown>,
            ts: typeof data.ts === 'string' ? data.ts : undefined,
          })
        } catch {
          /* 非 JSON 帧忽略 */
        }
      }

      ws.onclose = () => {
        setConnected(false)
        clearPing()
        scheduleRetry()
      }
      ws.onerror = () => {
        ws?.close()
      }
    }

    const scheduleRetry = () => {
      if (disposed) return
      const delay = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry)
      retry += 1
      retryTimer = setTimeout(() => void connect(), delay)
    }

    void connect()

    return () => {
      disposed = true
      clearPing()
      if (retryTimer) clearTimeout(retryTimer)
      if (ws) {
        ws.onclose = null
        ws.close()
      }
    }
  }, [])

  return { connected, lastSeq }
}
