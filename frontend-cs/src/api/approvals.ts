/**
 * 工具审批 service
 *
 * 后端为 backend/app/api/routes/approvals.py（前缀 /approvals，
 * 经 next.config.js rewrite 由 /api/approvals 代理）。
 * reviewer 缺省时不传——后端取网关注入的 X-User-Name / X-User-Id
 * （P3 修复：审计字段不允许默认身份），都拿不到记 "unknown"。
 */

import { request } from '@/lib/fetcher'

const BASE = '/api/approvals'

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'executed'

/** 审批单（字段与后端 tool_approval store 的 record 对齐） */
export interface ApprovalRequest {
  id: string
  tool_name: string
  action: string
  /** 操作指纹（同指纹 pending 去重键），展示用可不关心 */
  fingerprint: string
  /** 工具调用参数（后端 JSON，结构随工具而定） */
  detail: Record<string, unknown>
  user_id: string
  status: ApprovalStatus
  reviewer: string | null
  reason: string | null
  created_at: string
  decided_at: string | null
  executed_at: string | null
}

export interface ApprovalListResponse {
  items: ApprovalRequest[]
  total: number
}

export interface ApprovalDecisionPayload {
  /** 审批理由：前端强制必填（写操作审计红线） */
  reason: string
  /** 不传则由后端取网关身份头 */
  reviewer?: string
}

export const approvalService = {
  list(status: ApprovalStatus | '', limit = 50): Promise<ApprovalListResponse> {
    const qs = new URLSearchParams({ limit: String(limit) })
    if (status) qs.set('status', status)
    return request<ApprovalListResponse>(`${BASE}?${qs.toString()}`)
  },

  approve(requestId: string, payload: ApprovalDecisionPayload): Promise<{ ok: boolean; request: ApprovalRequest }> {
    return request(`${BASE}/${encodeURIComponent(requestId)}/approve`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  reject(requestId: string, payload: ApprovalDecisionPayload): Promise<{ ok: boolean; request: ApprovalRequest }> {
    return request(`${BASE}/${encodeURIComponent(requestId)}/reject`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
}
