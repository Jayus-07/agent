// Feedback service — 用户 👍/👎 反馈（2026-08-11 P1 反馈循环）
const BASE = '/api/feedback'

export interface FeedbackPayload {
  session_id: string
  vote: 'positive' | 'negative'
  msg_id?: string
  question?: string
  answer_preview?: string
  reason?: string
}

export const feedbackService = {
  send: async (payload: FeedbackPayload) => {
    try {
      const res = await fetch(BASE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      return await res.json()
    } catch (e) {
      return { ok: false, error: String(e) }
    }
  },
  stats: async (days: number = 7) => {
    try {
      const res = await fetch(`${BASE}/stats?days=${days}`)
      return await res.json()
    } catch (e) {
      return { total: 0, positive: 0, negative: 0, error: String(e) }
    }
  },
}
