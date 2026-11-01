/**
 * 客服意图目录 API（capability-governance G4，只读）
 *
 * 后端：backend/app/api/routes/cs_inventory.py
 *   → GET /cs/capabilities/inventory（supervisor-only 运营视图）
 * 形状按 G5 域清单协议预期：意图全集 × 调用/转人工/未解决。
 */

import { request } from '@/lib/fetcher'

export interface IntentStats {
  /** 窗口内该意图用户消息数 */
  calls: number
  /** 该意图会话的人工接管率（无会话时为 null，不伪造 0） */
  handoff_rate: number | null
  /** 主导会话命中未解决问题登记的数量 */
  unresolved: number
}

export interface IntentEntry {
  intent: string
  description: string
  stats: IntentStats
}

export interface CsInventoryResponse {
  domain: string
  range: string
  total: number
  intents: IntentEntry[]
  totals: {
    calls: number
    intents_with_data: number
    unresolved: number
  }
}

export type InventoryRange = 'today' | 'd7' | 'd30'

export function getCsInventory(range: InventoryRange): Promise<CsInventoryResponse> {
  return request<CsInventoryResponse>(`/api/cs/capabilities/inventory?range=${range}`)
}
