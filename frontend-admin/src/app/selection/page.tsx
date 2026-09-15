'use client'

/**
 * /selection — 智能选品页面（管理端）
 *
 * 功能模块：
 *   - 评分权重配置：五维度滑杆（PUT /selection/weights，全局策略，二次确认保存）
 *   - 推荐列表：潜力分徽章、子分数、LLM 推荐理由折叠、加入监控
 *   - 品类趋势区：价格分位面积图、卖点词频条形图、评价增速列表
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { Sparkles, RefreshCw, Plus, ChevronDown, ChevronUp, SlidersHorizontal } from 'lucide-react'

// recharts 较重，拆为独立 chunk 懒加载，避免路由切换时阻塞渲染
const TrendPanels = dynamic(() => import('./TrendPanels'), {
  ssr: false,
  loading: () => <div className="h-56 rounded-xl bg-black/5 animate-pulse" />,
})
import {
  selectionService, RecommendationItem, TrendsData,
} from '@/api/selection'
import { competitorService } from '@/api/competitor'
import { useToast } from '@/components/shared/Toast'

const WEIGHT_LABELS: Record<string, string> = {
  reputation: '口碑',
  heat: '热度',
  price: '价格',
  differentiation: '差异',
  stability: '稳定',
}

/** 权重配置卡：全局评分策略，属管理端职责（用户端看板只读消费） */
function WeightsCard({ onChanged }: { onChanged: () => void }) {
  const toast = useToast()
  const [weights, setWeights] = useState<Record<string, number> | null>(null)
  const [defaults, setDefaults] = useState<Record<string, number>>({})
  const [dirty, setDirty] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    selectionService.getWeights()
      .then((r) => { setWeights(r.weights); setDefaults(r.default) })
      .catch(() => toast.error('权重加载失败'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const total = weights ? Object.values(weights).reduce((a, b) => a + b, 0) : 0

  async function save() {
    if (!weights) return
    setSaving(true)
    try {
      const r = await selectionService.putWeights(weights)
      setWeights(r.weights)
      setDirty(false)
      setConfirming(false)
      toast.success('权重已保存，下次评分生效')
      onChanged()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="h-20 rounded-xl bg-black/5 animate-pulse" />
  if (!weights) return null

  return (
    <section className="bg-surface-base border border-border-subtle rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-medium text-text-primary flex items-center gap-1.5">
          <SlidersHorizontal size={15} /> 评分权重
          <span className="text-xs font-normal text-text-muted">（全局策略，影响潜力分计算）</span>
        </div>
        {dirty && !confirming && (
          <button
            onClick={() => setConfirming(true)}
            className="text-xs text-accent hover:text-accent-hover transition-colors"
          >
            保存变更
          </button>
        )}
        {confirming && (
          <span className="flex items-center gap-2 text-xs">
            <span className="text-amber-700">确认保存并生效？</span>
            <button
              onClick={save}
              disabled={saving}
              className="rounded-md bg-accent px-2.5 py-1 text-white transition-colors hover:bg-accent-hover disabled:opacity-60"
            >
              {saving ? '保存中…' : '确认'}
            </button>
            <button onClick={() => setConfirming(false)} className="text-text-muted hover:text-text-primary">取消</button>
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2 lg:grid-cols-5">
        {Object.keys(WEIGHT_LABELS).map((key) => (
          <label key={key} className="block text-xs">
            <span className="flex justify-between text-text-secondary">
              <span>{WEIGHT_LABELS[key]}</span>
              <span className="tabular-nums text-text-primary">
                {(weights[key] ?? 0).toFixed(2)}
                {defaults[key] != null && weights[key] !== defaults[key] && (
                  <span className="text-text-muted">（默认 {defaults[key].toFixed(2)}）</span>
                )}
              </span>
            </span>
            <input
              type="range"
              min={0}
              max={0.5}
              step={0.01}
              value={weights[key] ?? 0}
              onChange={(e) => {
                setWeights({ ...weights, [key]: Number(e.target.value) })
                setDirty(true)
                setConfirming(false)
              }}
              className="mt-1 w-full accent-[#4D6BFE]"
            />
          </label>
        ))}
      </div>
      <div className={`mt-2 text-[11px] ${Math.abs(total - 1) > 0.01 ? 'text-amber-600' : 'text-text-muted'}`}>
        权重合计 {total.toFixed(2)}{Math.abs(total - 1) > 0.01 && '（偏离 1.00 时总分分布会整体偏移，建议校准）'}
      </div>
    </section>
  )
}

function scoreColor(total: number): string {
  if (total >= 80) return 'bg-emerald-100 text-emerald-700'
  if (total >= 60) return 'bg-blue-100 text-blue-700'
  return 'bg-gray-100 text-gray-600'
}

export default function SelectionPage() {
  const toast = useToast()
  const [items, setItems] = useState<RecommendationItem[]>([])
  const [trends, setTrends] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [rec, tr] = await Promise.all([
        selectionService.getRecommendations({ limit: 10 }),
        selectionService.getTrends(30),
      ])
      setItems(rec.items)
      setTrends(tr)
    } catch (e) {
      setError(`加载失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const addToWatch = async (item: RecommendationItem) => {
    try {
      await competitorService.addWatch({ url: item.url, name: item.title, platform: item.platform })
      toast.success('已加入监控')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加入监控失败')
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6 sm:py-8 space-y-6">
        {/* 标题栏 */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-text-primary flex items-center gap-2">
              <Sparkles size={18} /> 智能选品
            </h1>
            <p className="text-xs text-text-muted mt-0.5">
              基于竞品快照的规则评分 + LLM 推荐理由；数据新鲜度以抓取时间为准
            </p>
          </div>
          <button
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors px-2 py-1.5 disabled:opacity-50"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3">
            <p className="text-xs text-red-600">{error}</p>
          </div>
        )}

        {/* 初次加载指示 */}
        {loading && items.length === 0 && !error ? (
          <div className="flex items-center justify-center py-20">
            <div className="w-5 h-5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
            <span className="ml-2 text-xs text-text-muted">加载中...</span>
          </div>
        ) : (
          <>
            {/* 评分权重（全局策略配置） */}
            <WeightsCard onChanged={load} />

            {/* 推荐列表 */}
            <section className="bg-surface-base border border-border-subtle rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-border-subtle bg-surface-elevated text-sm font-medium text-text-primary">
                潜力推荐 Top {items.length}
              </div>
              {items.length === 0 ? (
                <div className="p-8 text-center text-sm text-text-muted">
                  暂无推荐数据：请先在竞品监控页添加监控项并抓取快照
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-text-muted border-b border-border-subtle">
                      <th className="px-4 py-2 font-medium">商品</th>
                      <th className="px-4 py-2 font-medium">平台</th>
                      <th className="px-4 py-2 text-right font-medium">现价</th>
                      <th className="px-4 py-2 text-right font-medium">评分</th>
                      <th className="px-4 py-2 text-right font-medium">评价数</th>
                      <th className="px-4 py-2 text-center font-medium">潜力分</th>
                      <th className="px-4 py-2 font-medium">数据新鲜度</th>
                      <th className="px-4 py-2" />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it) => (
                      <Fragment key={it.url}>
                        <tr className="border-b border-border-subtle/50 hover:bg-surface-hover/30 transition-colors">
                          <td className="px-4 py-2 max-w-[240px] truncate text-text-primary" title={it.title}>{it.title}</td>
                          <td className="px-4 py-2 text-text-secondary">{it.platform}</td>
                          <td className="px-4 py-2 text-right text-text-primary">
                            {it.latest_price != null ? `${it.latest_price.toFixed(2)}` : '-'}
                          </td>
                          <td className="px-4 py-2 text-right text-text-secondary">{it.rating ?? '-'}</td>
                          <td className="px-4 py-2 text-right text-text-secondary">{it.review_count?.toLocaleString() ?? '-'}</td>
                          <td className="px-4 py-2 text-center">
                            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${scoreColor(it.score.total)}`}>
                              {it.score.total}
                            </span>
                          </td>
                          <td className="px-4 py-2 text-xs text-text-muted">
                            {it.latest_crawled_at?.slice(0, 16) ?? '-'}
                          </td>
                          <td className="px-4 py-2 text-right whitespace-nowrap">
                            <button
                              onClick={() => setExpanded((s) => ({ ...s, [it.url]: !s[it.url] }))}
                              className="text-text-muted hover:text-accent mr-2 transition-colors"
                              title="推荐理由"
                            >
                              {expanded[it.url] ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                            </button>
                            <button
                              onClick={() => addToWatch(it)}
                              className="text-accent hover:text-accent-hover inline-flex items-center gap-0.5 text-xs transition-colors"
                            >
                              <Plus size={14} /> 监控
                            </button>
                          </td>
                        </tr>
                        {expanded[it.url] && (
                          <tr className="border-b border-border-subtle/50 bg-surface-elevated/50">
                            <td colSpan={8} className="px-4 py-3 text-xs space-y-1">
                              <div className="text-text-secondary">
                                子分数：口碑 {it.score.breakdown.reputation} / 热度 {it.score.breakdown.heat} / 价格 {it.score.breakdown.price} / 差异 {it.score.breakdown.differentiation} / 稳定 {it.score.breakdown.stability}
                                {it.score.notes.length > 0 && (
                                  <span className="ml-2 text-amber-600">⚠ {it.score.notes.join(', ')}</span>
                                )}
                              </div>
                              {it.llm_reason && <div className="text-text-primary">推荐理由：{it.llm_reason}</div>}
                              {it.llm_risks && <div className="text-amber-700">风险提示：{it.llm_risks}</div>}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              )}
            </section>

            {/* 品类趋势区（recharts 懒加载） */}
            {trends && (
              <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <TrendPanels trends={trends} />
                <div className="bg-surface-base border border-border-subtle rounded-xl p-4 lg:col-span-2">
                  <div className="text-sm font-medium text-text-primary mb-3">评价增速 Top5（条/天）</div>
                  {trends.review_growth.length === 0 ? (
                    <div className="text-xs text-text-muted py-4 text-center">需要 ≥2 次快照才能计算增速</div>
                  ) : (
                    <ul className="text-sm divide-y divide-border-subtle/50">
                      {trends.review_growth.slice(0, 5).map((g) => (
                        <li key={g.url} className="py-2 flex justify-between">
                          <span className="truncate max-w-[70%] text-text-primary">{g.name}</span>
                          <span className={`font-medium ${g.daily_delta < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                            {g.daily_delta > 0 ? '+' : ''}{g.daily_delta} / 天
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  )
}
