'use client'

/**
 * /selection — 智能选品页面
 *
 * 功能模块：
 *   - 推荐列表：潜力分徽章、子分数、LLM 推荐理由折叠、加入监控
 *   - 品类趋势区：价格分位面积图、卖点词频条形图、评价增速列表
 */

import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Legend,
} from 'recharts'
import { Sparkles, RefreshCw, Plus, ChevronDown, ChevronUp } from 'lucide-react'
import {
  selectionService, RecommendationItem, TrendsData,
} from '@/services/selection'
import { competitorService } from '@/services/competitor'

function scoreColor(total: number): string {
  if (total >= 80) return 'bg-emerald-100 text-emerald-700'
  if (total >= 60) return 'bg-blue-100 text-blue-700'
  return 'bg-gray-100 text-gray-600'
}

export default function SelectionPage() {
  const [items, setItems] = useState<RecommendationItem[]>([])
  const [trends, setTrends] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [watchedMsg, setWatchedMsg] = useState<Record<string, string>>({})

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
      setWatchedMsg((m) => ({ ...m, [item.url]: '已加入监控' }))
    } catch (e) {
      setWatchedMsg((m) => ({ ...m, [item.url]: `加入失败: ${e instanceof Error ? e.message : e}` }))
    }
  }

  return (
    <div className="p-6 space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Sparkles size={20} /> 智能选品
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            基于竞品快照的规则评分 + LLM 推荐理由；数据新鲜度以抓取时间为准
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1 px-3 py-1.5 text-sm border rounded-md hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {error && <div className="text-sm text-red-600 bg-red-50 p-3 rounded">{error}</div>}

      {/* 推荐列表 */}
      <section className="border rounded-lg overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50 text-sm font-medium">潜力推荐 Top {items.length}</div>
        {items.length === 0 && !loading ? (
          <div className="p-8 text-center text-sm text-gray-400">
            暂无推荐数据：请先在竞品监控页添加监控项并抓取快照
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="px-4 py-2">商品</th>
                <th className="px-4 py-2">平台</th>
                <th className="px-4 py-2 text-right">现价</th>
                <th className="px-4 py-2 text-right">评分</th>
                <th className="px-4 py-2 text-right">评价数</th>
                <th className="px-4 py-2 text-center">潜力分</th>
                <th className="px-4 py-2">数据新鲜度</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <Fragment key={it.url}>
                  <tr className="border-b hover:bg-gray-50">
                    <td className="px-4 py-2 max-w-[240px] truncate" title={it.title}>{it.title}</td>
                    <td className="px-4 py-2">{it.platform}</td>
                    <td className="px-4 py-2 text-right">
                      {it.latest_price != null ? `${it.latest_price.toFixed(2)}` : '-'}
                    </td>
                    <td className="px-4 py-2 text-right">{it.rating ?? '-'}</td>
                    <td className="px-4 py-2 text-right">{it.review_count?.toLocaleString() ?? '-'}</td>
                    <td className="px-4 py-2 text-center">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${scoreColor(it.score.total)}`}>
                        {it.score.total}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-400">
                      {it.latest_crawled_at?.slice(0, 16) ?? '-'}
                    </td>
                    <td className="px-4 py-2 text-right whitespace-nowrap">
                      <button
                        onClick={() => setExpanded((s) => ({ ...s, [it.url]: !s[it.url] }))}
                        className="text-gray-400 hover:text-gray-600 mr-2"
                        title="推荐理由"
                      >
                        {expanded[it.url] ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                      </button>
                      <button
                        onClick={() => addToWatch(it)}
                        className="text-blue-600 hover:text-blue-700 inline-flex items-center gap-0.5 text-xs"
                      >
                        <Plus size={14} /> 监控
                      </button>
                      {watchedMsg[it.url] && <span className="ml-1 text-xs text-gray-400">{watchedMsg[it.url]}</span>}
                    </td>
                  </tr>
                  {expanded[it.url] && (
                    <tr className="border-b bg-gray-50/50">
                      <td colSpan={8} className="px-4 py-3 text-xs space-y-1">
                        <div className="text-gray-600">
                          子分数：口碑 {it.score.breakdown.reputation} / 热度 {it.score.breakdown.heat} / 价格 {it.score.breakdown.price} / 差异 {it.score.breakdown.differentiation} / 稳定 {it.score.breakdown.stability}
                          {it.score.notes.length > 0 && (
                            <span className="ml-2 text-amber-600">⚠ {it.score.notes.join(', ')}</span>
                          )}
                        </div>
                        {it.llm_reason && <div className="text-gray-700">推荐理由：{it.llm_reason}</div>}
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

      {/* 品类趋势区 */}
      {trends && (
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="border rounded-lg p-4">
            <div className="text-sm font-medium mb-3">价格分位趋势（p25 / p50 / p75）</div>
            {trends.price_quantiles.length === 0 ? (
              <div className="text-xs text-gray-400 py-8 text-center">暂无价格数据</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={trends.price_quantiles}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Area type="monotone" dataKey="p25" stroke="#94a3b8" fill="#e2e8f0" />
                  <Area type="monotone" dataKey="p50" stroke="#3b82f6" fill="#bfdbfe" />
                  <Area type="monotone" dataKey="p75" stroke="#6366f1" fill="#c7d2fe" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="border rounded-lg p-4">
            <div className="text-sm font-medium mb-3">热卖卖点词频</div>
            {trends.highlight_freq.length === 0 ? (
              <div className="text-xs text-gray-400 py-8 text-center">暂无卖点数据</div>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={trends.highlight_freq.slice(0, 10)} layout="vertical">
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis type="number" tick={{ fontSize: 11 }} />
                  <YAxis type="category" dataKey="keyword" width={80} tick={{ fontSize: 11 }} />
                  <Tooltip />
                  <Bar dataKey="count" fill="#3b82f6" />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="border rounded-lg p-4 lg:col-span-2">
            <div className="text-sm font-medium mb-3">评价增速 Top5（条/天）</div>
            {trends.review_growth.length === 0 ? (
              <div className="text-xs text-gray-400 py-4 text-center">需要 ≥2 次快照才能计算增速</div>
            ) : (
              <ul className="text-sm divide-y">
                {trends.review_growth.slice(0, 5).map((g) => (
                  <li key={g.url} className="py-2 flex justify-between">
                    <span className="truncate max-w-[70%]">{g.name}</span>
                    <span className="text-emerald-600 font-medium">+{g.daily_delta} / 天</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
