// Feedback service — 用户 👍/👎 反馈（2026-08-11 P1 反馈循环）
import { mutationRequest, request } from '@/api/client'

const BASE = '/api/feedback'

export interface FeedbackPayload {
  session_id: string
  vote: 'positive' | 'negative'
  msg_id?: string
  question?: string
  answer_preview?: string
  reason?: string
  trace_id?: string
  correction_text?: string
  expected_answer?: string
}

export function buildFeedbackPayload(payload: FeedbackPayload): FeedbackPayload {
  return {
    ...payload,
    session_id: payload.session_id.trim(),
    msg_id: payload.msg_id?.trim() || undefined,
    question: payload.question?.trim() || undefined,
    answer_preview: payload.answer_preview?.trim() || undefined,
    reason: payload.reason?.trim() || undefined,
    trace_id: payload.trace_id?.trim() || undefined,
    correction_text: payload.correction_text?.trim() || undefined,
    expected_answer: payload.expected_answer?.trim() || undefined,
  }
}

export const feedbackService = {
  send: async (payload: FeedbackPayload) => {
    const body = buildFeedbackPayload(payload)
    return mutationRequest<{ ok: boolean; id?: number; candidate_id?: string; candidate_status?: string }>(BASE, {
      operation: `feedback:${body.session_id}:${body.msg_id ?? ''}:${body.vote}`,
      method: 'POST', body,
    })
  },
  stats: async (days: number = 7) => {
    return request<{ total: number; positive: number; negative: number }>(`${BASE}/stats?days=${days}`)
  },
}
