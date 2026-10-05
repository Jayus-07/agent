/**
 * handoff 交接契约（多域隔离收官 M1，2026-10-06）
 *
 * 与后端 backend/orchestration/contracts/handoff.py::HandoffPayloadV1 同构。
 * **唯一契约样例 = backend/tests/fixtures/handoff_payload_v1.json**，
 * 双端测试消费同一份 JSON 防字段漂移（本文件 handoff.test.ts ↔
 * backend/tests/orchestration/test_handoff_contract.py）。
 *
 * 消费点：SSE `handoff` AUX 帧（events.py 下发）→ chat store.handoff →
 * HandoffCard 渲染引导卡，点击带参跳转专属页。
 */

export type HandoffTargetDomain =
  | 'travel'
  | 'customer_service'
  | 'selection_funnel'

export interface TravelHandoffParams {
  destination?: string
  days?: number | null
  party_size?: number | null
  budget_cny?: number | null
  must_go?: string[]
}

export interface SelectionFunnelHandoffParams {
  category?: string
  platform?: string
}

export interface CustomerServiceHandoffParams {
  prefill_question?: string
}

/** 参数包按域声明 schema（后端 extra=forbid 严格校验，前端按需读取） */
export type HandoffParams = TravelHandoffParams &
  SelectionFunnelHandoffParams &
  CustomerServiceHandoffParams

export interface HandoffPayloadV1 {
  v: 1
  target_domain: HandoffTargetDomain
  reason: string
  params: HandoffParams
  text: string
}

/** SSE handoff 帧 data（后端 _build_handoff_events 在契约体上附加 ts） */
export interface HandoffEvent extends HandoffPayloadV1 {
  ts: number
}

/** 判断任意 SSE data 是否为合法 handoff 帧（容忍字段缺失，形状不对返回 null） */
export function parseHandoffEvent(data: unknown): HandoffEvent | null {
  if (typeof data !== 'object' || data === null) return null
  const d = data as Record<string, unknown>
  if (d.v !== 1) return null
  if (
    d.target_domain !== 'travel' &&
    d.target_domain !== 'customer_service' &&
    d.target_domain !== 'selection_funnel'
  ) {
    return null
  }
  return {
    v: 1,
    target_domain: d.target_domain,
    reason: typeof d.reason === 'string' ? d.reason : '',
    params:
      typeof d.params === 'object' && d.params !== null
        ? (d.params as HandoffParams)
        : {},
    text: typeof d.text === 'string' ? d.text : '',
    ts: typeof d.ts === 'number' ? d.ts : 0,
  }
}
