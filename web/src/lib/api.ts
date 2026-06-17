import type { ChatRequest, ChatResponse, SSEEvent } from './types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL || ''

// ========================================
// 普通对话 (POST /chat)
// ========================================

export async function sendChat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ========================================
// SQL 查询 (POST /sql)
// ========================================

export async function sendSQL(question: string, currentUserId?: number) {
  const res = await fetch(`${API_BASE}/sql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, current_user_id: currentUserId ?? null }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ========================================
// RAG 检索 (POST /rag)
// ========================================

export async function sendRAG(question: string, sessionId: string) {
  const res = await fetch(`${API_BASE}/rag`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ========================================
// 报告生成 (POST /report)
// ========================================

export async function sendReport(reportType: string, filters: Record<string, unknown> = {}, userId = 'default') {
  const res = await fetch(`${API_BASE}/report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_type: reportType, filters, user_id: userId, polish: false }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// ========================================
// SSE 流式对话 (POST /chat/stream)
// ========================================

export async function* streamChat(req: ChatRequest): AsyncGenerator<SSEEvent> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('data: ')) {
        try {
          yield JSON.parse(trimmed.slice(6)) as SSEEvent
        } catch {
          // 忽略解析失败的行
        }
      }
    }
  }
}
