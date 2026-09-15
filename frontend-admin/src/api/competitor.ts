/**
 * 竞品监控 service
 *
 * 后端为 backend/app/api/routes/competitor.py（前缀 /competitor，
 * 经 next.config.js rewrite 由 /api/competitor 代理）。
 */

import { request } from '@/lib/fetcher'

const BASE = '/competitor'

// ── 类型定义 ──────────────────────────────────────

/** 监控列表项（含最新快照信息） */
export interface WatchItem {
  id: number
  name: string
  url: string
  platform: string
  my_sku: string
  frequency: string
  enabled: boolean
  created_at: string
  /** 最新快照现价 */
  latest_price: number | null
  latest_original_price: number | null
  latest_currency: string
  latest_promo: string | null
  latest_review_count: number | null
  latest_stock: boolean | null
  /** 最新快照抓取时间 */
  latest_crawled_at: string | null
  /** 快照总数 */
  snapshot_count: number
  /** 最新快照抽取方式（login_blocked 表示登录拦截） */
  latest_extract_method: string | null
}

/** 价格快照 */
export interface PriceSnapshot {
  id: number
  url: string
  platform: string
  title: string
  price: number | null
  original_price: number | null
  currency: string
  promo_text: string
  rating: number | null
  review_count: number | null
  in_stock: boolean
  highlights: string
  extract_method: string
  crawled_at: string
}

/** 价格变化 */
export interface PriceChange {
  diff: number
  pct: number
  latest: number
  oldest: number
}

/** 概览统计 */
export interface CompetitorStats {
  total: number
  enabled: number
  scanned_today: number
  price_drops: number
}

/** 添加监控项请求 */
export interface AddWatchParams {
  url: string
  name?: string
  platform?: string
  my_sku?: string
  frequency?: string
}

/** 单个平台的 Cookie 配置 */
export interface CookieItem {
  platform: string
  source: string
  saved_at: string
  preview: string
}

/** Cookie 配置状态（多平台列表） */
export interface CookieStatus {
  configured: boolean
  items: CookieItem[]
}

/** Cookie 测试结果 */
export interface CookieTestResult {
  ok: boolean
  login_intercepted: boolean
  has_price: boolean
  content_length: number
  preview: string
  message: string
  error?: string
}

/** QR 登录启动结果 */
export interface QrLoginResult {
  ok: boolean
  platform: string
  token: string
  qr_url: string
  /** 内部会话 Cookie，用于后续轮询（不直接展示给用户） */
  session_cookies: string
  expires_in: number
  error?: string
}

/** QR 扫码轮询结果 */
export interface QrPollResult {
  ok: boolean
  status: 'new' | 'scanned' | 'confirmed' | 'expired' | 'error'
  saved?: boolean
  cookie_length?: number
  error?: string
}

/** 重试被拦截 URL 的单项结果 */
export interface RetryItem {
  url: string
  name: string
  ok: boolean
  method?: string | null
  error?: string
}

/** 重试被拦截 URL 的汇总结果 */
export interface RetryResult {
  retried: number
  succeeded: number
  results: RetryItem[]
}

// ── Service ──────────────────────────────────────

export const competitorService = {
  /** 概览统计 */
  getStats: () =>
    request<{ stats: CompetitorStats }>(`${BASE}/stats`),

  /** 监控列表（含最新价格） */
  getWatchlist: (enabledOnly = false) =>
    request<{ items: WatchItem[]; total: number }>(
      `${BASE}/watchlist?enabled_only=${enabledOnly}`,
    ),

  /** 添加监控项 */
  addWatch: (params: AddWatchParams) =>
    request<{ item: WatchItem; baseline: { price: number | null; currency: string; crawled_at: string } | null }>(
      `${BASE}/watchlist`,
      {
        method: 'POST',
        body: JSON.stringify({
          url: params.url,
          name: params.name || '',
          platform: params.platform || 'auto',
          my_sku: params.my_sku || '',
          frequency: params.frequency || 'daily',
        }),
      },
    ),

  /** 移除监控项 */
  removeWatch: (url: string) =>
    request<{ removed: boolean; url: string }>(
      `${BASE}/watchlist?url=${encodeURIComponent(url)}`,
      { method: 'DELETE' },
    ),

  /** 启用/停用监控项 */
  toggleWatch: (url: string, enabled: boolean) =>
    request<{ item: WatchItem }>(
      `${BASE}/watchlist`,
      {
        method: 'PATCH',
        body: JSON.stringify({ url, enabled }),
      },
    ),

  /** 价格历史 */
  getHistory: (url: string, days = 0) =>
    request<{
      url: string
      name: string
      platform: string
      snapshots: PriceSnapshot[]
      price_change: PriceChange | null
    }>(
      `${BASE}/history?url=${encodeURIComponent(url)}&days=${days}`,
    ),

  /** 立即分析竞品 */
  analyze: (url: string, useLlm = true) =>
    request<{ result: string; url: string }>(
      `${BASE}/analyze`,
      {
        method: 'POST',
        body: JSON.stringify({ url, use_llm: useLlm }),
        timeout: 120_000, // 抓取可能较慢
      },
    ),

  /** 全量巡检 */
  scanAll: () =>
    request<{ report: string }>(
      `${BASE}/scan`,
      {
        method: 'POST',
        timeout: 300_000, // 多项巡检可能很慢
      },
    ),

  /** 查询 Cookie 配置状态 */
  getCookieStatus: () =>
    request<CookieStatus>(`${BASE}/cookies`),

  /** 保存某平台 Cookie（立即生效，无需重启） */
  saveCookies: (cookies: string, platform: string) =>
    request<{ saved: boolean; platform: string; length: number }>(
      `${BASE}/cookies`,
      { method: 'POST', body: JSON.stringify({ cookies, platform }) },
    ),

  /** 清除 Cookie（指定平台或全部） */
  clearCookies: (platform?: string) =>
    request<{ cleared: boolean }>(
      `${BASE}/cookies${platform ? `?platform=${encodeURIComponent(platform)}` : ''}`,
      { method: 'DELETE' },
    ),

  /** 测试 Cookie 是否生效 */
  testCookies: (url?: string) =>
    request<CookieTestResult>(
      `${BASE}/test-cookies`,
      {
        method: 'POST',
        body: JSON.stringify({ url: url || '' }),
        timeout: 120_000,
      },
    ),

  /** 启动扫码登录 */
  startQrLogin: (platform: string) =>
    request<QrLoginResult>(
      `${BASE}/qr-login/start`,
      {
        method: 'POST',
        body: JSON.stringify({ platform }),
        timeout: 90_000, // 抖音弹窗 QR 异步渲染，后端最坏 ~61s（goto 30s 上限 + 等待 + QR 25s）
      },
    ),

  /** 轮询扫码状态 */
  pollQrLogin: (platform: string, token: string, sessionCookies: string) =>
    request<QrPollResult>(
      `${BASE}/qr-login/poll`,
      {
        method: 'POST',
        body: JSON.stringify({
          platform,
          token,
          session_cookies: sessionCookies,
        }),
        timeout: 15_000,
      },
    ),

  /** 重试所有被登录拦截的监控项 */
  retryBlocked: () =>
    request<RetryResult>(
      `${BASE}/retry-blocked`,
      { method: 'POST', timeout: 300_000 },
    ),
}
