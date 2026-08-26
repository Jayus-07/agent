'use client'

/**
 * PriceHistoryModal — 价格历史弹窗（Recharts 折线图 + 快照列表）
 *
 * 接收竞品 URL，加载历史快照数据并渲染趋势图表。
 * 支持时间范围筛选（7 天 / 30 天 / 全部）。
 */

import { useEffect, useState, useCallback } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  Tooltip, CartesianGrid,
} from 'recharts'
import { X } from 'lucide-react'
import { competitorService, type PriceSnapshot, type PriceChange } from '@/services/competitor'
import { clsx } from 'clsx'

interface Props {
  url: string
  name: string
  onClose: () => void
}

type DayFilter = 0 | 7 | 30
const DAY_OPTIONS: { value: DayFilter; label: string }[] = [
  { value: 7, label: '7天' },
  { value: 30, label: '30天' },
  { value: 0, label: '全部' },
]

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: '¥', USD: '$', GBP: '£', EUR: '€',
}

/** 自定义图表 Tooltip */
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const snap = payload[0]?.payload as PriceSnapshot & { date: string }
  const sym = CURRENCY_SYMBOLS[snap?.currency] || '¥'
  return (
    <div className="bg-surface-base rounded-lg shadow-lg border border-border-subtle px-3 py-2 text-xs">
      <p className="font-medium text-text-primary mb-1">{snap?.crawled_at?.slice(0, 16)}</p>
      {snap?.price != null && (
        <p className="text-accent">现价: {sym}{snap.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</p>
      )}
      {snap?.original_price != null && (
        <p className="text-text-muted">划线价: {sym}{snap.original_price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</p>
      )}
      {snap?.promo_text && (
        <p className="text-amber-600 mt-0.5">促销: {snap.promo_text.slice(0, 30)}</p>
      )}
    </div>
  )
}

export default function PriceHistoryModal({ url, name, onClose }: Props) {
  const [days, setDays] = useState<DayFilter>(0)
  const [snapshots, setSnapshots] = useState<PriceSnapshot[]>([])
  const [priceChange, setPriceChange] = useState<PriceChange | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await competitorService.getHistory(url, days)
      setSnapshots(res.snapshots)
      setPriceChange(res.price_change)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [url, days])

  useEffect(() => { load() }, [load])

  // 图表数据（旧→新排列）
  const chartData = [...snapshots]
    .filter(s => s.price != null)
    .reverse()
    .map(s => ({ ...s, date: s.crawled_at?.slice(5, 10) || '' }))

  // 价格变化指示器
  const changeColor = !priceChange
    ? 'text-text-muted'
    : priceChange.diff < 0
      ? 'text-emerald-600'
      : priceChange.diff > 0
        ? 'text-red-500'
        : 'text-text-muted'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-surface-base rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-text-primary truncate">{name}</h3>
            <p className="text-[10px] text-text-muted truncate mt-0.5">{url}</p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {/* 时间范围筛选 */}
            <div className="flex rounded-lg bg-black/5 p-0.5">
              {DAY_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setDays(opt.value)}
                  className={clsx(
                    'px-2.5 py-1 text-[10px] rounded-md transition-colors',
                    days === opt.value
                      ? 'bg-white text-text-primary shadow-sm'
                      : 'text-text-muted hover:text-text-secondary',
                  )}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button onClick={onClose} className="p-1 rounded-md hover:bg-black/5 transition-colors">
              <X size={16} className="text-text-muted" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="flex items-center justify-center py-20">
              <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
              <span className="ml-2 text-xs text-text-muted">加载中...</span>
            </div>
          )}

          {error && (
            <p className="text-xs text-red-500 py-8 text-center">{error}</p>
          )}

          {!loading && !error && snapshots.length === 0 && (
            <p className="text-xs text-text-muted py-12 text-center">
              暂无价格历史数据，请先执行一次分析
            </p>
          )}

          {!loading && !error && snapshots.length > 0 && (
            <>
              {/* 趋势摘要 */}
              {priceChange && (
                <div className={clsx('text-xs mb-3 font-medium', changeColor)}>
                  {priceChange.diff < 0
                    ? `📉 降价 ${priceChange.diff.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })}（${priceChange.pct.toFixed(1)}%）`
                    : priceChange.diff > 0
                      ? `📈 涨价 +${priceChange.diff.toLocaleString('zh-CN', { style: 'currency', currency: 'CNY' })}（+${priceChange.pct.toFixed(1)}%）`
                      : '📊 价格平稳'}
                </div>
              )}

              {/* Recharts 折线图 */}
              <div className="h-56 mb-6">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                    <defs>
                      <linearGradient id="priceGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#4D6BFE" stopOpacity={0.2} />
                        <stop offset="100%" stopColor="#4D6BFE" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: '#94a3b8' }}
                      tickLine={false}
                      axisLine={{ stroke: '#e2e8f0' }}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fill: '#94a3b8' }}
                      tickLine={false}
                      axisLine={false}
                      width={60}
                      tickFormatter={(v: number) => `¥${v.toLocaleString()}`}
                    />
                    <Tooltip content={<ChartTooltip />} />
                    <Line
                      type="monotone"
                      dataKey="price"
                      stroke="#4D6BFE"
                      strokeWidth={2}
                      dot={{ r: 3, fill: '#4D6BFE', strokeWidth: 0 }}
                      activeDot={{ r: 5, fill: '#4D6BFE' }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* 快照列表 */}
              <div className="space-y-1">
                <h4 className="text-xs font-medium text-text-primary mb-2">历史快照</h4>
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-text-muted border-b border-border-subtle">
                      <th className="text-left py-1.5 font-medium">时间</th>
                      <th className="text-right py-1.5 font-medium">现价</th>
                      <th className="text-right py-1.5 font-medium">划线价</th>
                      <th className="text-left py-1.5 font-medium pl-3">促销</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshots.slice(0, 10).map(snap => (
                      <tr key={snap.id} className="border-b border-border-subtle/50 hover:bg-surface-hover/30">
                        <td className="py-1.5 text-text-secondary">{snap.crawled_at?.slice(0, 16)}</td>
                        <td className="py-1.5 text-right text-text-primary font-medium">
                          {snap.price != null ? `¥${snap.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}` : '-'}
                        </td>
                        <td className="py-1.5 text-right text-text-muted">
                          {snap.original_price != null ? `¥${snap.original_price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}` : '-'}
                        </td>
                        <td className="py-1.5 pl-3 text-text-muted truncate max-w-[150px]">
                          {snap.promo_text || '无'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {snapshots.length > 10 && (
                  <p className="text-[10px] text-text-muted mt-2 text-center">
                    仅显示最近 10 条，共 {snapshots.length} 条记录
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
