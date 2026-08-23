/**
 * 智能选品 service
 *
 * 后端为 backend/app/api/routes/selection.py（前缀 /selection，
 * 经 next.config.js rewrite 由 /api/selection 代理）。
 * 必须使用相对路径 + request()，禁止 NEXT_PUBLIC_API_URL（会绕过代理）。
 */

import { request } from '@/lib/fetcher'

const BASE = '/selection'

// ── 类型定义 ──────────────────────────────────

/** 评分结果（与后端 score_product 输出一致） */
export interface ScoreResult {
  total: number
  breakdown: {
    reputation: number
    heat: number
    price: number
    differentiation: number
    stability: number
  }
  notes: string[]
}

/** 推荐列表项 */
export interface RecommendationItem {
  url: string
  title: string
  platform: string
  latest_price: number | null
  currency: string
  rating: number | null
  review_count: number | null
  score: ScoreResult
  llm_reason: string
  llm_risks: string
  latest_crawled_at: string | null
  scored_at: string
}

/** 趋势聚合响应 */
export interface TrendsData {
  days: number
  platform: string | null
  items: {
    url: string
    name: string
    platform: string
    latest_price: number | null
    rating: number | null
    review_count: number | null
    highlights: string
    latest_crawled_at: string | null
  }[]
  price_quantiles: { date: string; p25: number; p50: number; p75: number }[]
  review_growth: { url: string; name: string; daily_delta: number }[]
  highlight_freq: { keyword: string; count: number }[]
  sources: { snapshot_count: number; rag_hits: number }
}

/** 对比项 */
export interface CompareItem {
  url: string
  name: string
  price: number | null
  original_price: number | null
  currency: string
  rating: number | null
  review_count: number | null
  promo_text: string
  in_stock: boolean
  highlights: string
  crawled_at: string | null
}

// ── Service ──────────────────────────────────

export const selectionService = {
  /** 推荐列表 */
  getRecommendations: (params?: { platform?: string; limit?: number; min_score?: number }) => {
    const qs = new URLSearchParams()
    if (params?.platform) qs.set('platform', params.platform)
    if (params?.limit) qs.set('limit', String(params.limit))
    if (params?.min_score) qs.set('min_score', String(params.min_score))
    const suffix = qs.toString() ? `?${qs}` : ''
    return request<{ items: RecommendationItem[]; total: number; generated_at: string }>(
      `${BASE}/recommendations${suffix}`,
      { timeout: 120_000 }, // LLM 理由生成可能较慢
    )
  },

  /** 趋势聚合 */
  getTrends: (days = 30, platform?: string) => {
    const qs = new URLSearchParams({ days: String(days) })
    if (platform) qs.set('platform', platform)
    return request<TrendsData>(`${BASE}/trends?${qs}`)
  },

  /** 单品评分 */
  score: (url: string, forceRefresh = false) =>
    request<RecommendationItem>(
      `${BASE}/score`,
      { method: 'POST', body: JSON.stringify({ url, force_refresh: forceRefresh }), timeout: 60_000 },
    ),

  /** 批量评分缓存 */
  batchScores: (urls: string[]) => {
    const qs = urls.map((u) => `urls=${encodeURIComponent(u)}`).join('&')
    return request<{ scores: Record<string, ScoreResult>; generated_at: string }>(
      `${BASE}/scores/batch?${qs}`,
    )
  },

  /** 多品对比 */
  compare: (urls: string[]) => {
    const qs = urls.map((u) => `urls=${encodeURIComponent(u)}`).join('&')
    return request<{ items: CompareItem[]; diff_fields: string[]; generated_at: string }>(
      `${BASE}/compare?${qs}`,
    )
  },

  /** 读取权重 */
  getWeights: () =>
    request<{ weights: Record<string, number>; default: Record<string, number> }>(
      `${BASE}/weights`,
    ),

  /** 更新权重 */
  putWeights: (weights: Record<string, number>) =>
    request<{ weights: Record<string, number> }>(
      `${BASE}/weights`,
      { method: 'PUT', body: JSON.stringify({ weights }) },
    ),

  /** 选品报告 */
  generateReport: (days = 30) =>
    request<{ report: string }>(
      `${BASE}/report?days=${days}`,
      { method: 'POST', timeout: 120_000 },
    ),
}
