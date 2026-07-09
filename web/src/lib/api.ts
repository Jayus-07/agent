import type { ChatRequest, SSEStreamEvent } from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

// ========================================
// SSE 流式对话 (POST /chat/stream)
// ========================================

/** SSE v2 解析器：支持 event: 字段分流 */
export async function* streamChat(
  req: ChatRequest,
  signal?: AbortSignal,
): AsyncGenerator<SSEStreamEvent> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent = ''   // 暂存 event: 行

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      // Flush remaining buffer
      const parsed = _tryParseEvent(currentEvent, buffer.trim())
      if (parsed) yield parsed
      break
    }

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed === '') {
        // 空行 = 事件帧结束
        // currentEvent 已被上一轮设置，buffer 在上一行 data: 中暂存
        // 此处不需要额外处理 — 帧解析在 data: 行完成
        continue
      }
      if (trimmed.startsWith('event: ')) {
        currentEvent = trimmed.slice(7)
        continue
      }
      if (trimmed.startsWith('data: ')) {
        const jsonStr = trimmed.slice(6)
        const parsed = _tryParseEvent(currentEvent, jsonStr)
        if (parsed) yield parsed
        currentEvent = ''
      }
    }
  }
}

/** 安全解析单个 SSE 事件帧 */
function _tryParseEvent(evtType: string, jsonStr: string): SSEStreamEvent | null {
  if (!evtType || !jsonStr) return null
  try {
    const data = JSON.parse(jsonStr)
    return { event: evtType, data } as SSEStreamEvent
  } catch {
    return null
  }
}

/** 发送中止信号到后端 */
export async function abortChat(sessionId: string, requestId: string): Promise<void> {
  await fetch(`${API_BASE}/chat/abort`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, request_id: requestId }),
  }).catch(() => { /* 中止请求本身失败不影响主流程 */ })
}

// ========================================
// LLM 切换 + 余额查询
// ========================================

export interface LLMModel {
  provider: string
  name: string
  display: string
  description: string
}

export interface LLMBalance {
  ok: boolean
  provider?: string
  balance?: string
  currency?: string
  note?: string
  error?: string
}

/** GET /llm/models — 列出可用模型 */
export async function listLLMModels(): Promise<{ models: LLMModel[]; current: string }> {
  const res = await fetch(`${API_BASE}/llm/models`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** GET /llm/current — 获取当前模型 */
export async function getCurrentLLM(): Promise<{ model: string; provider: string }> {
  const res = await fetch(`${API_BASE}/llm/current`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

/** POST /llm/switch — 切换当前模型 */
export async function switchLLM(model: string): Promise<{ ok: boolean; model?: string; provider?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/llm/switch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // 400/503: detail 字段含 {ok:false, error}
    const errMsg = (data.detail && (data.detail.error || data.detail)) || `HTTP ${res.status}`
    throw new Error(errMsg)
  }
  return data
}

/** GET /llm/balance — 查询余额 */
export async function getLLMBalance(provider?: string): Promise<LLMBalance> {
  const qs = provider ? `?provider=${encodeURIComponent(provider)}` : ''
  const res = await fetch(`${API_BASE}/llm/balance${qs}`)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    return data.detail || { ok: false, error: `HTTP ${res.status}` }
  }
  return data
}
