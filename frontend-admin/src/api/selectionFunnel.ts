/**
 * 选品漏斗（selection_funnel）service
 *
 * 后端为 backend/app/api/routes/selection_funnel.py（前缀 /selection-funnel，
 * 经 BFF catch-all 代理 /api/selection-funnel，服务端注入 X-API-Key）。
 *
 * 三类上传数据（kind）：products 商品榜 / keywords 关键词榜 / reviews 差评
 */

import { request } from '@/api/client'

const BASE = '/api/selection-funnel'

export type ImportKind = 'products' | 'keywords' | 'reviews'

export interface ImportResult {
  batch_id: string
  count: number
  notes: string[]
}

export interface ImportCandidate {
  id: number
  batch_id: string
  title: string
  platform: string
  price: number | null
  original_price: number | null
  rating: number | null
  review_count: number | null
  sales: number | null
  category: string
  url: string
  promo_text: string
  highlights: string
}

export interface KeywordStat {
  keyword: string
  search_pop: number | null
  click_rate: number | null
  pay_rate: number | null
  competition: number | null
}

export interface MarketSnapshot {
  count: number
  top: KeywordStat[]
  opportunities: KeywordStat[]
  hint?: string
}

export interface PainPointItem {
  title: string
  review_total: number
  negative: number
  pains: [string, number][]
}

export const selectionFunnelService = {
  /** 上传表格文件（CSV/TSV/XLSX，首行表头） */
  importFile(file: File, kind: ImportKind, category = '', platform = '') {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('kind', kind)
    fd.append('category', category)
    fd.append('platform', platform)
    return request<ImportResult>(`${BASE}/import/file`, {
      method: 'POST',
      body: fd,
    })
  },

  /** 粘贴表格文本（Excel Ctrl+C 复制的 TSV / CSV） */
  importText(content: string, kind: ImportKind, category = '', platform = '') {
    return request<ImportResult>(`${BASE}/import/text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, kind, category, platform }),
    })
  },

  /** 导入候选列表（粗过滤） */
  listCandidates(category = '', platform = '') {
    const q = new URLSearchParams({ category, platform })
    return request<{ count: number; items: ImportCandidate[] }>(
      `${BASE}/import/candidates?${q}`,
    )
  },

  /** 赛道画像（关键词榜统计：top 词 + 机会词） */
  marketSnapshot(category = '') {
    const q = new URLSearchParams({ category })
    return request<MarketSnapshot>(`${BASE}/market/snapshot?${q}`)
  },

  /** 痛点机会（差评聚类；titles 用 | 分隔） */
  painPoints(category: string, titles: string[]) {
    const q = new URLSearchParams({ category, titles: titles.join('|') })
    return request<{ count: number; items: PainPointItem[] }>(
      `${BASE}/reviews/pain-points?${q}`,
    )
  },

  /** 清除指定导入批次（三类数据共用批次号前缀区分：imp-/kw-/rv-） */
  clearBatch(batchId: string) {
    return request<{ batch_id: string; removed: number }>(
      `${BASE}/import/batch/${batchId}`,
      { method: 'DELETE' },
    )
  },
}
