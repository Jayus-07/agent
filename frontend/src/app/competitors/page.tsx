'use client'

/**
 * /competitors — 竞品监控页面
 *
 * 功能模块：
 *   - 概览统计卡片（总数 / 启用 / 今日巡检 / 降价提醒）
 *   - 监控列表（增删改查 + 启停开关 + 内联价格趋势弹窗）
 *   - 分析查询（按 URL 即时分析竞品）
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Plus, Trash2, RefreshCw, TrendingUp, Activity,
  AlertTriangle, X, Target, BarChart3, Search,
  Lock, KeyRound, CheckCircle, ExternalLink,
  Smartphone, Clock, Loader2, Database,
} from 'lucide-react'
import { competitorService, type WatchItem, type CompetitorStats, type CookieStatus, type CookieTestResult, type QrLoginResult, type QrPollResult, type RetryResult } from '@/services/competitor'
import PriceHistoryModal from '@/components/competitor/PriceHistoryModal'
import CompareModal from '@/components/selection/CompareModal'
import { selectionService, type ScoreResult } from '@/services/selection'
import { useToast } from '@/components/shared/Toast'
import { clsx } from 'clsx'

// ── 常量 ──────────────────────────────────────

const CURRENCY_SYMBOLS: Record<string, string> = { CNY: '¥', USD: '$', GBP: '£', EUR: '€' }
const FREQ_LABELS: Record<string, string> = { daily: '每日', '4h': '4小时', weekly: '每周' }

const PLATFORM_BADGE: Record<string, { label: string; cls: string }> = {
  jd:      { label: '京东', cls: 'bg-red-50 text-red-600 border-red-200' },
  tmall:   { label: '天猫', cls: 'bg-rose-50 text-rose-600 border-rose-200' },
  taobao:  { label: '淘宝', cls: 'bg-orange-50 text-orange-600 border-orange-200' },
  amazon:  { label: 'Amazon', cls: 'bg-amber-50 text-amber-600 border-amber-200' },
  pdd:     { label: '拼多多', cls: 'bg-pink-50 text-pink-600 border-pink-200' },
  douyin:  { label: '抖音', cls: 'bg-slate-900 text-white border-slate-700' },
  suning:  { label: '苏宁', cls: 'bg-yellow-50 text-yellow-600 border-yellow-200' },
  generic: { label: '通用', cls: 'bg-gray-50 text-gray-500 border-gray-200' },
}

// 手动输入 Cookie 可选的平台
const MANUAL_PLATFORMS = ['taobao', 'tmall', 'jd', 'douyin', 'pdd', 'suning', 'amazon'] as const

// 扫码登录可选的平台（与后端 qr_login._PLATFORM_CONFIG 对应）
const QR_PLATFORMS = [
  { id: 'taobao', label: '淘宝/天猫' },
  { id: 'jd', label: '京东' },
  { id: 'douyin', label: '抖音' },
] as const

const STAT_CARDS = [
  { key: 'total' as const,         label: '监控总数',   icon: <Target size={14} />,       cls: 'border-blue-200 bg-blue-50' },
  { key: 'enabled' as const,       label: '已启用',     icon: <Activity size={14} />,      cls: 'border-green-200 bg-green-50' },
  { key: 'scanned_today' as const, label: '今日巡检',   icon: <RefreshCw size={14} />,     cls: 'border-cyan-200 bg-cyan-50' },
  { key: 'price_drops' as const,   label: '降价提醒',   icon: <AlertTriangle size={14} />, cls: 'border-orange-200 bg-orange-50' },
]

// ── 主页面 ─────────────────────────────────────

export default function CompetitorsPage() {
  const toast = useToast()
  const [items, setItems] = useState<WatchItem[]>([])
  const [stats, setStats] = useState<CompetitorStats>({ total: 0, enabled: 0, scanned_today: 0, price_drops: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'list' | 'analyze'>('list')

  // 对话框与操作状态
  const [addOpen, setAddOpen] = useState(false)
  const [cookieOpen, setCookieOpen] = useState(false)
  const [chartItem, setChartItem] = useState<{ url: string; name: string } | null>(null)
  const [scanning, setScanning] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [cookieStatus, setCookieStatus] = useState<CookieStatus>({ configured: false, items: [] })

  // 选品对比状态
  const [selectedUrls, setSelectedUrls] = useState<string[]>([])
  const [compareOpen, setCompareOpen] = useState(false)
  const [scores, setScores] = useState<Record<string, ScoreResult>>({})

  const loadData = useCallback(async () => {
    try {
      const [statsRes, listRes, cookieRes] = await Promise.all([
        competitorService.getStats(),
        competitorService.getWatchlist(false),
        competitorService.getCookieStatus(),
      ])
      setStats(statsRes.stats)
      setItems(listRes.items)
      setCookieStatus(cookieRes)
      setError(null)
      // 潜力分批量读取（失败不影响列表展示）
      if (listRes.items.length > 0) {
        selectionService.batchScores(listRes.items.map((i) => i.url))
          .then((r) => setScores(r.scores))
          .catch(() => {})
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const refreshCookieStatus = useCallback(async () => {
    try {
      setCookieStatus(await competitorService.getCookieStatus())
    } catch { /* 静默失败 */ }
  }, [])

  async function handleToggle(url: string, enabled: boolean) {
    setToggling(url)
    try {
      await competitorService.toggleWatch(url, enabled)
      setItems(prev => prev.map(i => i.url === url ? { ...i, enabled } : i))
      toast.success(enabled ? '已启用' : '已停用')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    } finally {
      setToggling(null)
    }
  }

  async function handleRemove(url: string, name: string) {
    if (!confirm(`确定移除「${name}」的监控？（快照历史不会删除）`)) return
    try {
      await competitorService.removeWatch(url)
      setItems(prev => prev.filter(i => i.url !== url))
      toast.success('已移除监控')
      loadData()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '移除失败')
    }
  }

  function handleToggleSelect(url: string, checked: boolean) {
    if (checked && selectedUrls.length >= 5) {
      toast.error('最多选择 5 个商品进行对比')
      return
    }
    setSelectedUrls(prev => checked ? [...prev, url] : prev.filter(u => u !== url))
  }

  async function handleScan() {
    setScanning(true)
    try {
      await competitorService.scanAll()
      toast.success('巡检完成')
      loadData()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '巡检失败')
    } finally {
      setScanning(false)
    }
  }

  const fmtPrice = (v: number | null, cur?: string) =>
    v != null ? `${CURRENCY_SYMBOLS[cur || 'CNY'] || '¥'}${v.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}` : '-'

  // ── Render ────────────────────────────────────

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">竞品监控</h1>
            <p className="text-xs text-text-muted mt-0.5">价格追踪 · 竞品对比 · 自动巡检</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadData}
              className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors px-2 py-1.5"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
            </button>
            <button
              onClick={() => setAddOpen(true)}
              className="flex items-center gap-1.5 bg-accent text-white text-xs px-3 py-1.5 rounded-lg hover:bg-accent-hover transition-colors shadow-sm"
            >
              <Plus size={14} /> 添加竞品
            </button>
            <button
              onClick={() => setCookieOpen(true)}
              className={clsx(
                'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors border',
                cookieStatus.configured
                  ? 'border-green-200 text-green-600 bg-green-50'
                  : 'border-orange-200 text-orange-600 bg-orange-50 hover:bg-orange-100',
              )}
              title={cookieStatus.configured
                ? `已配置 ${cookieStatus.items.length} 个平台: ${cookieStatus.items.map(i => PLATFORM_BADGE[i.platform]?.label ?? i.platform).join('、')}`
                : '未配置 Cookie，淘宝/天猫/抖音等平台将无法抓取'}
            >
              <KeyRound size={14} />
              {cookieStatus.configured ? 'Cookie 已配置' : '配置 Cookie'}
            </button>
            <button
              onClick={handleScan}
              disabled={scanning || items.filter(i => i.enabled).length === 0}
              className={clsx(
                'flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors border',
                scanning
                  ? 'border-accent/30 text-accent/50 cursor-not-allowed'
                  : 'border-accent/30 text-accent hover:bg-accent/5',
              )}
            >
              <RefreshCw size={14} className={scanning ? 'animate-spin' : ''} />
              {scanning ? '巡检中...' : '全量巡检'}
            </button>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
          {STAT_CARDS.map(s => (
            <div key={s.key} className={clsx('rounded-xl border p-4', s.cls)}>
              <div className="flex items-center gap-2 text-xs mb-1">{s.icon} {s.label}</div>
              <div className="text-2xl font-semibold text-text-primary">{stats[s.key]}</div>
            </div>
          ))}
        </div>

        {/* Tab Switcher */}
        <div className="flex rounded-lg bg-black/5 p-0.5 mb-4 w-fit">
          {([
            { key: 'list' as const, label: '监控列表' },
            { key: 'analyze' as const, label: '分析查询' },
          ]).map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={clsx(
                'px-4 py-1.5 text-xs rounded-md transition-colors',
                tab === t.key ? 'bg-white text-text-primary shadow-sm' : 'text-text-muted hover:text-text-secondary',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab Content */}
        {tab === 'list' && (
          <>
            {items.length > 0 && (
              <div className="flex items-center justify-end mb-3">
                <button
                  onClick={() => setCompareOpen(true)}
                  disabled={selectedUrls.length < 2}
                  className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border border-accent/30 text-accent hover:bg-accent/5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  对比（{selectedUrls.length}）
                </button>
              </div>
            )}
            <WatchlistTable
              items={items}
              loading={loading}
              error={error}
              toggling={toggling}
              scores={scores}
              selectedUrls={selectedUrls}
              onToggleSelect={handleToggleSelect}
              onToggle={handleToggle}
              onRemove={handleRemove}
              onChart={setChartItem}
              onAdd={() => setAddOpen(true)}
              fmtPrice={fmtPrice}
            />
          </>
        )}

        {tab === 'analyze' && <AnalyzePanel onSuccess={loadData} onConfigCookie={() => setCookieOpen(true)} />}

        {/* Price History Modal */}
        {chartItem && (
          <PriceHistoryModal
            url={chartItem.url}
            name={chartItem.name}
            onClose={() => setChartItem(null)}
          />
        )}

        {/* Compare Modal */}
        {compareOpen && (
          <CompareModal urls={selectedUrls} onClose={() => setCompareOpen(false)} />
        )}

        {/* Add Competitor Dialog */}
        {addOpen && (
          <AddCompetitorDialog
            onClose={() => setAddOpen(false)}
            onSuccess={() => { setAddOpen(false); loadData() }}
          />
        )}

        {/* Cookie Config Modal */}
        {cookieOpen && (
          <CookieConfigModal
            currentStatus={cookieStatus}
            onClose={() => setCookieOpen(false)}
            onSuccess={() => { setCookieOpen(false); loadData() }}
            onRefresh={refreshCookieStatus}
          />
        )}
      </div>
    </div>
  )
}

// ── 监控列表 ──────────────────────────────────

interface TableProps {
  items: WatchItem[]
  loading: boolean
  error: string | null
  toggling: string | null
  scores: Record<string, ScoreResult>
  selectedUrls: string[]
  onToggleSelect: (url: string, checked: boolean) => void
  onToggle: (url: string, enabled: boolean) => void
  onRemove: (url: string, name: string) => void
  onChart: (item: { url: string; name: string }) => void
  onAdd: () => void
  fmtPrice: (v: number | null, cur?: string) => string
}

function WatchlistTable({ items, loading, error, toggling, scores, selectedUrls, onToggleSelect, onToggle, onRemove, onChart, onAdd, fmtPrice }: TableProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
        <span className="ml-2 text-xs text-text-muted">加载中...</span>
      </div>
    )
  }
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3">
        <p className="text-xs text-red-600">加载失败：{error}</p>
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <Target size={36} className="text-text-muted/30 mb-3" />
        <p className="text-sm text-text-secondary">暂无监控竞品</p>
        <p className="text-xs text-text-muted mt-1">点击「添加竞品」开始监控价格走势</p>
        <button
          onClick={onAdd}
          className="mt-4 flex items-center gap-1.5 bg-accent text-white text-xs px-4 py-2 rounded-lg hover:bg-accent-hover transition-colors"
        >
          <Plus size={14} /> 添加竞品
        </button>
      </div>
    )
  }

  return (
    <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
      {/* Desktop table */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface-elevated border-b border-border-subtle">
              <th className="w-8 px-3 py-2.5" />
              <th className="text-left px-4 py-2.5 font-medium text-text-muted">名称 / URL</th>
              <th className="text-left px-3 py-2.5 font-medium text-text-muted">平台</th>
              <th className="text-right px-3 py-2.5 font-medium text-text-muted">现价</th>
              <th className="text-center px-3 py-2.5 font-medium text-text-muted">潜力分</th>
              <th className="text-center px-3 py-2.5 font-medium text-text-muted">频率</th>
              <th className="text-center px-3 py-2.5 font-medium text-text-muted">状态</th>
              <th className="text-left px-3 py-2.5 font-medium text-text-muted">最后更新</th>
              <th className="text-center px-3 py-2.5 font-medium text-text-muted">操作</th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const plat = PLATFORM_BADGE[item.platform] || PLATFORM_BADGE.generic
              return (
                <tr key={item.id} className="border-b border-border-subtle/50 hover:bg-surface-hover/30 transition-colors">
                  <td className="px-3 py-3 text-center">
                    <input
                      type="checkbox"
                      checked={selectedUrls.includes(item.url)}
                      onChange={(e) => onToggleSelect(item.url, e.target.checked)}
                      className="w-3.5 h-3.5 accent-accent cursor-pointer"
                    />
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-medium text-text-primary truncate max-w-[200px]">{item.name}</div>
                    <div className="text-[10px] text-text-muted truncate max-w-[200px] mt-0.5">{item.url}</div>
                  </td>
                  <td className="px-3 py-3">
                    <span className={clsx('text-[10px] px-2 py-0.5 rounded-full border', plat.cls)}>{plat.label}</span>
                  </td>
                  <td className="px-3 py-3 text-right">
                    {item.latest_extract_method === 'login_blocked' ? (
                      <span className="inline-flex items-center gap-1 text-orange-500 text-[11px]">
                        <Lock size={12} /> 需登录
                      </span>
                    ) : (
                      <span className="font-medium text-text-primary">
                        {fmtPrice(item.latest_price, item.latest_currency)}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-center">
                    {scores[item.url]
                      ? <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-blue-50 text-blue-600">{scores[item.url].total}</span>
                      : <span className="text-text-muted">-</span>}
                  </td>
                  <td className="px-3 py-3 text-center text-text-muted">{FREQ_LABELS[item.frequency] || item.frequency}</td>
                  <td className="px-3 py-3 text-center">
                    <button
                      onClick={() => onToggle(item.url, !item.enabled)}
                      disabled={toggling === item.url}
                      className={clsx(
                        'relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                        item.enabled ? 'bg-accent' : 'bg-gray-300',
                        toggling === item.url && 'opacity-50',
                      )}
                    >
                      <span className={clsx(
                        'inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform',
                        item.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]',
                      )} />
                    </button>
                  </td>
                  <td className="px-3 py-3 text-text-muted text-[11px]">
                    {item.latest_crawled_at?.slice(0, 16) || '-'}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex items-center justify-center gap-1">
                      <button
                        onClick={() => onChart({ url: item.url, name: item.name })}
                        className="p-1.5 rounded-md hover:bg-accent/10 text-text-muted hover:text-accent transition-colors"
                        title="价格趋势"
                      >
                        <BarChart3 size={14} />
                      </button>
                      <button
                        onClick={() => onRemove(item.url, item.name)}
                        className="p-1.5 rounded-md hover:bg-red-50 text-text-muted hover:text-red-500 transition-colors"
                        title="移除"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card list */}
      <div className="sm:hidden divide-y divide-border-subtle/50">
        {items.map(item => {
          const plat = PLATFORM_BADGE[item.platform] || PLATFORM_BADGE.generic
          return (
            <div key={item.id} className="px-4 py-3 space-y-2">
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text-primary truncate">{item.name}</span>
                    <span className={clsx('text-[10px] px-1.5 py-0.5 rounded-full border shrink-0', plat.cls)}>
                      {plat.label}
                    </span>
                  </div>
                  <p className="text-[10px] text-text-muted truncate mt-0.5">{item.url}</p>
                </div>
                <button
                  onClick={() => onToggle(item.url, !item.enabled)}
                  disabled={toggling === item.url}
                  className={clsx(
                    'relative inline-flex h-5 w-9 items-center rounded-full transition-colors shrink-0',
                    item.enabled ? 'bg-accent' : 'bg-gray-300',
                  )}
                >
                  <span className={clsx(
                    'inline-block h-3.5 w-3.5 rounded-full bg-white shadow transition-transform',
                    item.enabled ? 'translate-x-[18px]' : 'translate-x-[3px]',
                  )} />
                </button>
              </div>
              <div className="flex items-center justify-between text-xs">
                {item.latest_extract_method === 'login_blocked' ? (
                  <span className="inline-flex items-center gap-1 text-orange-500">
                    <Lock size={12} /> 需登录
                  </span>
                ) : (
                  <span className="font-medium text-text-primary">
                    {fmtPrice(item.latest_price, item.latest_currency)}
                  </span>
                )}
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => onChart({ url: item.url, name: item.name })}
                    className="p-1.5 rounded-md hover:bg-accent/10 text-text-muted hover:text-accent"
                  >
                    <BarChart3 size={14} />
                  </button>
                  <button
                    onClick={() => onRemove(item.url, item.name)}
                    className="p-1.5 rounded-md hover:bg-red-50 text-text-muted hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <div className="flex items-center justify-between text-[10px] text-text-muted">
                <span>{FREQ_LABELS[item.frequency] || item.frequency}</span>
                <span>{item.latest_crawled_at?.slice(0, 16) || '未更新'}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 分析查询面板 ──────────────────────────────

function AnalyzePanel({ onSuccess, onConfigCookie }: { onSuccess: () => void; onConfigCookie: () => void }) {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState('')
  const [analyzing, setAnalyzing] = useState(false)
  const toast = useToast()

  async function handleAnalyze() {
    if (!url.trim()) {
      toast.warning('请输入竞品 URL')
      return
    }
    setAnalyzing(true)
    setResult('')
    try {
      const res = await competitorService.analyze(url.trim())
      setResult(res.result)
      toast.success('分析完成')
      onSuccess()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '分析失败')
      setResult(`分析失败: ${e instanceof Error ? e.message : '未知错误'}`)
    } finally {
      setAnalyzing(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* URL 输入 */}
      <div className="flex gap-2">
        <input
          value={url}
          onChange={e => setUrl(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !analyzing && handleAnalyze()}
          placeholder="输入竞品 URL（如 https://item.jd.com/xxx）"
          className="flex-1 border border-border-subtle rounded-lg px-3 py-2 text-sm
            focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent"
        />
        <button
          onClick={handleAnalyze}
          disabled={analyzing || !url.trim()}
          className={clsx(
            'flex items-center gap-1.5 bg-accent text-white text-xs px-4 py-2 rounded-lg transition-colors',
            analyzing ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent-hover',
          )}
        >
          <Search size={14} />
          {analyzing ? '分析中...' : '分析'}
        </button>
      </div>

      {/* 分析结果 */}
      {analyzing && (
        <div className="flex items-center justify-center py-12 bg-surface-base rounded-xl border border-border-subtle">
          <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
          <span className="ml-2 text-xs text-text-muted">正在抓取并分析竞品页面，请稍候...</span>
        </div>
      )}
      {result && !analyzing && (
        <div className="space-y-3">
          <div className="bg-surface-base rounded-xl border border-border-subtle p-4 text-sm text-text-primary whitespace-pre-wrap leading-relaxed">
            {result}
          </div>
          {result.includes('登录拦截') && (
            <div className="bg-orange-50 border border-orange-200 rounded-xl px-4 py-3 flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs text-orange-700">
                <Lock size={14} />
                该平台需要登录才能抓取商品数据
              </div>
              <button
                onClick={onConfigCookie}
                className="flex items-center gap-1.5 bg-orange-500 text-white text-xs px-3 py-1.5 rounded-lg hover:bg-orange-600 transition-colors"
              >
                <KeyRound size={14} /> 配置 Cookie
              </button>
            </div>
          )}
        </div>
      )}
      {!result && !analyzing && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Search size={36} className="text-text-muted/30 mb-3" />
          <p className="text-sm text-text-secondary">输入竞品 URL 开始分析</p>
          <p className="text-xs text-text-muted mt-1">
            支持京东、天猫、淘宝、Amazon、拼多多、苏宁等电商平台
          </p>
        </div>
      )}
    </div>
  )
}

// ── 添加竞品弹窗 ──────────────────────────────

const FREQ_OPTIONS = [
  { value: 'daily', label: '每日' },
  { value: '4h', label: '每4小时' },
  { value: 'weekly', label: '每周' },
]

function AddCompetitorDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: () => void }) {
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [frequency, setFrequency] = useState('daily')
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  async function handleSave() {
    if (!url.trim()) {
      toast.warning('请输入竞品 URL')
      return
    }
    setSaving(true)
    try {
      await competitorService.addWatch({ url: url.trim(), name: name.trim(), frequency })
      toast.success('已添加监控')
      onSuccess()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '添加失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-surface-base rounded-xl shadow-2xl w-full max-w-md">
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <h3 className="text-sm font-medium text-text-primary">添加竞品监控</h3>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-black/5">
            <X size={16} className="text-text-muted" />
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <div>
            <label className="block text-xs text-text-secondary mb-1">竞品 URL <span className="text-red-500">*</span></label>
            <input
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="https://item.jd.com/..."
              className="w-full border border-border-subtle rounded-lg px-3 py-2 text-sm
                focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent"
              autoFocus
            />
          </div>
          <div>
            <label className="block text-xs text-text-secondary mb-1">名称（可选）</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="如：iPhone 15 Pro Max"
              className="w-full border border-border-subtle rounded-lg px-3 py-2 text-sm
                focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent"
            />
          </div>
          <div>
            <label className="block text-xs text-text-secondary mb-1">监控频率</label>
            <select
              value={frequency}
              onChange={e => setFrequency(e.target.value)}
              className="w-full border border-border-subtle rounded-lg px-3 py-2 text-sm
                focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent bg-white"
            >
              {FREQ_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-border-subtle">
          <button
            onClick={onClose}
            className="text-xs text-text-muted hover:text-text-primary px-3 py-1.5 rounded-lg hover:bg-black/5"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !url.trim()}
            className={clsx(
              'flex items-center gap-1.5 bg-accent text-white text-xs px-4 py-1.5 rounded-lg transition-colors',
              saving ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent-hover',
            )}
          >
            {saving ? '添加中...' : '添加'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Cookie 管理面板 ────────────────────────────

function CookieStatusCard({
  status,
  deleting,
  onDelete,
}: {
  status: CookieStatus
  deleting: string | null
  onDelete: (platform: string) => void
}) {
  return (
    <div className="bg-surface-elevated/60 border border-border-subtle rounded-lg p-3 space-y-2">
      {/* 标题行 */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-text-secondary flex items-center gap-1.5">
          <Database size={13} /> Cookie 管理
        </span>
        <span className="text-[10px] text-text-muted">{status.items.length} 个平台</span>
      </div>

      {status.items.length === 0 ? (
        <div className="text-[10px] text-orange-600 bg-orange-50 border border-orange-200 rounded-md px-2.5 py-1.5 flex items-center gap-1.5">
          <Lock size={11} /> 未配置 Cookie — 淘宝/天猫/抖音等需登录的平台将无法抓取
        </div>
      ) : (
        <div className="space-y-1.5">
          {status.items.map(item => (
            <div key={item.platform} className="flex items-center gap-2 rounded-md border border-border-subtle bg-white/70 px-2.5 py-1.5">
              <span className={clsx('shrink-0 text-[10px] px-1.5 py-px rounded-full border', PLATFORM_BADGE[item.platform]?.cls ?? PLATFORM_BADGE.generic.cls)}>
                {PLATFORM_BADGE[item.platform]?.label ?? item.platform}
              </span>
              <span className={clsx('shrink-0 inline-flex items-center gap-1 text-[9px] px-1.5 py-px rounded-full', item.source === 'qr' ? 'bg-green-50 text-green-600' : 'bg-blue-50 text-blue-600')}>
                {item.source === 'qr' ? <Smartphone size={9} /> : <KeyRound size={9} />}
                {item.source === 'qr' ? '扫码' : '手动'}
              </span>
              <span className="flex-1 min-w-0 text-[10px] text-text-muted truncate">
                {item.saved_at ? item.saved_at.replace('T', ' ') : '已配置'} · {item.preview}
              </span>
              <button
                onClick={() => onDelete(item.platform)}
                disabled={deleting === item.platform}
                className="shrink-0 p-1 rounded text-red-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                title={`删除 ${PLATFORM_BADGE[item.platform]?.label ?? item.platform} Cookie`}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Cookie 配置弹窗 ──────────────────────────────

function CookieConfigModal({
  currentStatus,
  onClose,
  onSuccess,
  onRefresh,
}: {
  currentStatus: CookieStatus
  onClose: () => void
  onSuccess: () => void
  onRefresh: () => void
}) {
  const toast = useToast()
  const [cookies, setCookies] = useState('')
  const [manualPlatform, setManualPlatform] = useState<string>('taobao')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<CookieTestResult | null>(null)
  const [showGuide, setShowGuide] = useState(false)

  // QR 登录状态
  const [modalTab, setModalTab] = useState<'manual' | 'qr'>('manual')
  const [qrPlatform, setQrPlatform] = useState<string>('taobao')
  const [qrUrl, setQrUrl] = useState('')
  const [qrToken, setQrToken] = useState('')
  const [qrSession, setQrSession] = useState('')
  const [qrStatus, setQrStatus] = useState<'idle' | 'loading' | 'waiting' | 'scanned' | 'success' | 'expired' | 'error'>('idle')
  const [countdown, setCountdown] = useState(0)
  const [retrying, setRetrying] = useState(false)
  const [retryResult, setRetryResult] = useState<RetryResult | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function stopPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
  }

  useEffect(() => stopPolling, [])

  async function handleSave() {
    if (!cookies.trim()) {
      toast.warning('请粘贴 Cookie 值')
      return
    }
    setSaving(true)
    try {
      await competitorService.saveCookies(cookies.trim(), manualPlatform)
      toast.success(`${PLATFORM_BADGE[manualPlatform]?.label ?? manualPlatform} Cookie 已保存，立即生效`)
      setCookies('')
      onRefresh()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(platform: string) {
    setDeleting(platform)
    try {
      await competitorService.clearCookies(platform)
      toast.success(`${PLATFORM_BADGE[platform]?.label ?? platform} Cookie 已删除`)
      onRefresh()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败')
    } finally {
      setDeleting(null)
    }
  }

  async function handleTest() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await competitorService.testCookies()
      setTestResult(res)
      if (res.ok && !res.login_intercepted) {
        toast.success('Cookie 验证通过！')
      } else if (res.login_intercepted) {
        toast.warning('Cookie 未生效，页面仍为登录页')
      } else {
        toast.error(res.error || '测试失败')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '测试失败')
    } finally {
      setTesting(false)
    }
  }

  async function handleClear() {
    const label = PLATFORM_BADGE[manualPlatform]?.label ?? manualPlatform
    if (!confirm(`确定清除 ${label} 已保存的 Cookie？清除后该平台将无法抓取。`)) return
    try {
      await competitorService.clearCookies(manualPlatform)
      toast.success(`${label} Cookie 已清除`)
      setCookies('')
      setTestResult(null)
      onRefresh()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '清除失败')
    }
  }

  async function handleStartQr() {
    stopPolling()
    setQrStatus('loading')
    setQrUrl('')
    setRetryResult(null)
    try {
      const res = await competitorService.startQrLogin(qrPlatform)
      if (!res.ok) throw new Error(res.error)
      setQrUrl(res.qr_url)
      setQrToken(res.token)
      setQrSession(res.session_cookies)
      setQrStatus('waiting')
      setCountdown(res.expires_in)
      // 启动轮询（每 2s）
      pollRef.current = setInterval(async () => {
        try {
          const poll = await competitorService.pollQrLogin(qrPlatform, res.token, res.session_cookies)
          if (poll.status === 'scanned') {
            setQrStatus('scanned')
          } else if (poll.status === 'confirmed') {
            stopPolling()
            setQrStatus('success')
            if (poll.saved) toast.success('扫码登录成功！Cookie 已自动保存')
            onSuccess()
          } else if (poll.status === 'expired') {
            stopPolling()
            setQrStatus('expired')
            toast.warning('二维码已过期，请重新扫码')
          }
        } catch {
          // 轮询失败，静默重试
        }
      }, 2000)
      // 启动倒计时
      timerRef.current = setInterval(() => {
        setCountdown(prev => {
          if (prev <= 1) { stopPolling(); setQrStatus('expired'); return 0 }
          return prev - 1
        })
      }, 1000)
    } catch (e) {
      setQrStatus('error')
      toast.error(e instanceof Error ? e.message : '启动扫码失败')
    }
  }

  async function handleRetryBlocked() {
    setRetrying(true)
    setRetryResult(null)
    try {
      const res = await competitorService.retryBlocked()
      setRetryResult(res)
      if (res.succeeded > 0) {
        toast.success(`${res.succeeded}/${res.retried} 个 URL 重试成功`)
        onSuccess()
      } else if (res.retried > 0) {
        toast.warning('重试完成，但仍需登录')
      } else {
        toast.info('没有被登录拦截的监控项')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '重试失败')
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-surface-base rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle shrink-0">
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-accent" />
            <h3 className="text-sm font-medium text-text-primary">Cookie 配置</h3>
            {currentStatus.configured && (
              <span className="inline-flex items-center gap-1 text-[10px] text-green-600 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full">
                <CheckCircle size={10} /> 已配置
              </span>
            )}
          </div>
          <button onClick={onClose} className="p-1 rounded-md hover:bg-black/5">
            <X size={16} className="text-text-muted" />
          </button>
        </div>

        {/* 顶部状态卡片（固定，不随内容滚动） */}
        <div className="px-5 py-3 border-b border-border-subtle shrink-0">
          <CookieStatusCard
            status={currentStatus}
            deleting={deleting}
            onDelete={handleDelete}
          />
        </div>

        {/* Tab 切换 */}
        <div className="flex border-b border-border-subtle shrink-0">
          <button
            onClick={() => setModalTab('manual')}
            className={clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-colors border-b-2',
              modalTab === 'manual'
                ? 'text-accent border-accent'
                : 'text-text-muted border-transparent hover:text-text-secondary',
            )}
          >
            <KeyRound size={13} /> 手动输入
          </button>
          <button
            onClick={() => { setModalTab('qr'); stopPolling(); setQrStatus('idle') }}
            className={clsx(
              'flex-1 flex items-center justify-center gap-1.5 px-4 py-2.5 text-xs font-medium transition-colors border-b-2',
              modalTab === 'qr'
                ? 'text-accent border-accent'
                : 'text-text-muted border-transparent hover:text-text-secondary',
            )}
          >
            <Smartphone size={13} /> 扫码登录
          </button>
        </div>

        {/* Body（仅 Tab 内容，可滚动） */}
        <div className="px-5 py-4 space-y-4 overflow-y-auto flex-1">
          {modalTab === 'manual' && (
          <>
          {/* 平台选择 */}
          <div>
            <label className="block text-xs text-text-secondary mb-1.5">目标平台</label>
            <div className="flex flex-wrap gap-1.5">
              {MANUAL_PLATFORMS.map(p => (
                <button
                  key={p}
                  onClick={() => setManualPlatform(p)}
                  className={clsx(
                    'text-[11px] px-2.5 py-1 rounded-lg border transition-colors',
                    manualPlatform === p
                      ? 'border-accent text-accent bg-accent/5 font-medium'
                      : 'border-border-subtle text-text-muted hover:text-text-secondary',
                  )}
                >
                  {PLATFORM_BADGE[p]?.label ?? p}
                </button>
              ))}
            </div>
            <p className="text-[10px] text-text-muted mt-1.5">
              各平台 Cookie 独立保存、互不覆盖；抓取时按 URL 自动匹配对应平台
            </p>
          </div>

          {/* Cookie 输入区 */}
          <div>
            <label className="block text-xs text-text-secondary mb-1">
              {PLATFORM_BADGE[manualPlatform]?.label ?? manualPlatform} 浏览器 Cookie 值
            </label>
            <textarea
              value={cookies}
              onChange={e => setCookies(e.target.value)}
              placeholder="粘贴从浏览器复制的 Cookie 字符串，如: tb_token=xxx; _m_h5_tk=xxx; ..."
              rows={4}
              className="w-full border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono
                focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent resize-none"
              autoFocus
            />
          </div>

          {/* 操作按钮 */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving || !cookies.trim()}
              className={clsx(
                'flex items-center gap-1.5 bg-accent text-white text-xs px-4 py-2 rounded-lg transition-colors',
                saving || !cookies.trim() ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent-hover',
              )}
            >
              {saving ? '保存中...' : '保存 Cookie'}
            </button>
            {currentStatus.configured && (
              <>
                <button
                  onClick={handleTest}
                  disabled={testing}
                  className={clsx(
                    'flex items-center gap-1.5 border border-accent/30 text-accent text-xs px-3 py-2 rounded-lg transition-colors',
                    testing ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent/5',
                  )}
                >
                  {testing ? '测试中...（约30秒）' : '测试 Cookie'}
                </button>
                <button
                  onClick={handleClear}
                  className="text-xs text-red-500 hover:text-red-600 px-3 py-2 rounded-lg hover:bg-red-50 transition-colors"
                >
                  清除
                </button>
              </>
            )}
          </div>

          {/* 测试结果 */}
          {testResult && (
            <div className={clsx(
              'rounded-lg border px-3 py-2 text-xs',
              testResult.ok && !testResult.login_intercepted
                ? 'bg-green-50 border-green-200 text-green-700'
                : 'bg-orange-50 border-orange-200 text-orange-700',
            )}>
              <div className="flex items-center gap-1.5 mb-1">
                {testResult.ok && !testResult.login_intercepted
                  ? <CheckCircle size={12} />
                  : <AlertTriangle size={12} />}
                {testResult.message}
              </div>
              {testResult.ok && (
                <div className="text-[10px] text-text-muted">
                  页面长度: {testResult.content_length} 字符 |
                  含价格符号: {testResult.has_price ? '是' : '否'} |
                  登录拦截: {testResult.login_intercepted ? '是' : '否'}
                </div>
              )}
            </div>
          )}

          {/* 获取 Cookie 指引 */}
          <div className="border border-border-subtle rounded-lg overflow-hidden">
            <button
              onClick={() => setShowGuide(!showGuide)}
              className="w-full flex items-center justify-between px-3 py-2 text-xs text-text-secondary hover:bg-surface-hover/30 transition-colors"
            >
              <span className="flex items-center gap-1.5">
                <ExternalLink size={12} /> 如何获取浏览器 Cookie？
              </span>
              <span className="text-text-muted text-[10px]">{showGuide ? '收起' : '展开'}</span>
            </button>
            {showGuide && (
              <div className="px-3 py-3 border-t border-border-subtle text-[11px] text-text-secondary space-y-2 leading-relaxed">
                <div>
                  <strong>方法一：Chrome DevTools</strong>
                  <ol className="list-decimal list-inside mt-1 space-y-0.5 text-text-muted">
                    <li>用 Chrome 打开 <a href="https://www.taobao.com" target="_blank" rel="noopener" className="text-accent hover:underline">taobao.com</a> 并登录</li>
                    <li>按 F12 打开开发者工具</li>
                    <li>切换到 <strong>Application</strong> → <strong>Cookies</strong> → <strong>https://www.taobao.com</strong></li>
                    <li>在 Cookie 列表底部找到 <strong>Request Headers</strong> 中的 <code className="bg-black/5 px-1 rounded">Cookie</code> 值</li>
                    <li>或：在 <strong>Network</strong> 面板任意请求 → Headers → Request Headers → Cookie</li>
                    <li>复制完整的 Cookie 值，粘贴到上方输入框</li>
                  </ol>
                </div>
                <div className="pt-1 border-t border-border-subtle">
                  <strong>方法二：浏览器扩展</strong>
                  <p className="mt-1 text-text-muted">安装 "Cookie-Editor" 或 "EditThisCookie" 扩展，在已登录的页面上点击导出，复制 Cookie 字符串。</p>
                </div>
                <div className="pt-1 border-t border-border-subtle">
                  <strong>注意事项</strong>
                  <ul className="list-disc list-inside mt-1 space-y-0.5 text-text-muted">
                    <li>Cookie 有有效期，过期后需重新获取</li>
                    <li>保存后立即生效，无需重启后端服务</li>
                    <li>建议使用「测试 Cookie」按钮验证是否生效</li>
                  </ul>
                </div>
              </div>
            )}
          </div>

          {/* 替代方案 */}
          <div className="bg-blue-50 border border-blue-200 rounded-lg px-3 py-2 text-[11px] text-blue-700">
            <div className="flex items-center gap-1.5 mb-1">
              <Activity size={12} /> 替代方案
            </div>
            <ul className="list-disc list-inside space-y-0.5 text-blue-600">
              <li>京东 (item.jd.com) 和 Amazon 通常无需登录即可抓取</li>
              <li>手动在浏览器中打开链接查看价格</li>
              <li>拼多多 (pinduoduo.com) 大部分页面可直接抓取</li>
            </ul>
          </div>
          </>
          )}

          {/* QR 扫码登录面板 */}
          {modalTab === 'qr' && (
            <div className="space-y-4">
              {/* 平台选择 */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-secondary">选择平台:</span>
                {QR_PLATFORMS.map(p => (
                  <button
                    key={p.id}
                    onClick={() => { setQrPlatform(p.id); stopPolling(); setQrStatus('idle') }}
                    className={clsx(
                      'text-xs px-3 py-1.5 rounded-lg border transition-colors',
                      qrPlatform === p.id
                        ? 'border-accent text-accent bg-accent/5'
                        : 'border-border-subtle text-text-muted hover:text-text-secondary',
                    )}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {/* QR 状态展示 */}
              {qrStatus === 'idle' && (
                <div className="text-center py-6">
                  <Smartphone size={48} className="mx-auto text-text-muted mb-3" />
                  <p className="text-xs text-text-secondary mb-4">点击下方按钮获取二维码</p>
                  <button
                    onClick={handleStartQr}
                    className="inline-flex items-center gap-1.5 bg-accent text-white text-xs px-5 py-2.5 rounded-lg hover:bg-accent-hover transition-colors"
                  >
                    <Smartphone size={14} /> 获取二维码
                  </button>
                </div>
              )}

              {qrStatus === 'loading' && (
                <div className="text-center py-8">
                  <Loader2 size={32} className="mx-auto text-accent animate-spin mb-2" />
                  <p className="text-xs text-text-muted">正在获取二维码...</p>
                </div>
              )}

              {(qrStatus === 'waiting' || qrStatus === 'scanned') && qrUrl && (
                <div className="text-center space-y-3">
                  {/* QR 图片 */}
                  <div className="flex justify-center">
                    <img
                      src={qrUrl}
                      alt="扫码登录"
                      className="w-48 h-48 max-w-full object-contain rounded-lg border border-border-subtle bg-white"
                    />
                  </div>
                  {/* 倒计时 */}
                  <div className="flex items-center justify-center gap-1.5 text-xs text-text-muted">
                    <Clock size={12} />
                    {countdown}s 后过期
                  </div>
                  {/* 状态 */}
                  {qrStatus === 'waiting' && (
                    <p className="text-xs text-text-secondary">请用手机 App 扫描二维码</p>
                  )}
                  {qrStatus === 'scanned' && (
                    <p className="text-xs text-green-600 flex items-center justify-center gap-1">
                      <CheckCircle size={12} /> 已扫描，请在手机上确认登录
                    </p>
                  )}
                  <button
                    onClick={handleStartQr}
                    className="text-[10px] text-text-muted hover:text-accent"
                  >
                    刷新二维码
                  </button>
                </div>
              )}

              {qrStatus === 'success' && (
                <div className="text-center py-4 space-y-3">
                  <CheckCircle size={40} className="mx-auto text-green-500" />
                  <p className="text-xs text-green-600">登录成功！Cookie 已自动保存</p>
                  {/* 自动重试 */}
                  <button
                    onClick={handleRetryBlocked}
                    disabled={retrying}
                    className={clsx(
                      'inline-flex items-center gap-1.5 border border-accent/30 text-accent text-xs px-4 py-2 rounded-lg transition-colors',
                      retrying ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent/5',
                    )}
                  >
                    {retrying ? <><Loader2 size={12} className="animate-spin" /> 重试中...</> : <><RefreshCw size={12} /> 重试被拦截的抓取</>}
                  </button>
                  {retryResult && (
                    <div className="text-[10px] text-text-muted">
                      重试 {retryResult.retried} 个 URL，成功 {retryResult.succeeded} 个
                    </div>
                  )}
                </div>
              )}

              {qrStatus === 'expired' && (
                <div className="text-center py-4 space-y-3">
                  <AlertTriangle size={32} className="mx-auto text-orange-400" />
                  <p className="text-xs text-orange-600">二维码已过期</p>
                  <button
                    onClick={handleStartQr}
                    className="inline-flex items-center gap-1.5 bg-accent text-white text-xs px-4 py-2 rounded-lg hover:bg-accent-hover transition-colors"
                  >
                    <RefreshCw size={12} /> 重新获取
                  </button>
                </div>
              )}

              {qrStatus === 'error' && (
                <div className="text-center py-4 space-y-3">
                  <AlertTriangle size={32} className="mx-auto text-red-400" />
                  <p className="text-xs text-red-600">扫码登录启动失败</p>
                  <p className="text-[10px] text-text-muted">可能是平台 API 变更或网络问题，请切回「手动配置」Tab</p>
                  <button
                    onClick={handleStartQr}
                    className="inline-flex items-center gap-1.5 border border-accent/30 text-accent text-xs px-4 py-2 rounded-lg hover:bg-accent/5 transition-colors"
                  >
                    <RefreshCw size={12} /> 重试
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer 操作栏 */}
        <div className="flex items-center justify-between gap-2 px-5 py-3 border-t border-border-subtle shrink-0">
          <span className="text-[10px] text-text-muted truncate">
            {currentStatus.configured ? 'Cookie 已保存，即时生效' : '配置后立即生效，无需重启'}
          </span>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={onClose}
              className="text-xs text-text-muted hover:text-text-primary px-3 py-1.5 rounded-lg hover:bg-black/5 transition-colors"
            >
              关闭
            </button>
            {modalTab === 'manual' && (
              <button
                onClick={handleSave}
                disabled={saving || !cookies.trim()}
                className={clsx(
                  'bg-accent text-white text-xs px-4 py-1.5 rounded-lg transition-colors',
                  saving || !cookies.trim() ? 'opacity-60 cursor-not-allowed' : 'hover:bg-accent-hover',
                )}
              >
                {saving ? '应用中...' : '应用并关闭'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
