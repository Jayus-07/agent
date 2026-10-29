'use client'

import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useToast } from '@/components/shared/Toast'
import {
  getTokensSummary,
  getTokensCalls,
  type TokensSummary,
  type TokenCallRow,
} from '@/api/observability'

/**
 * Token 用量（2026-10-30 按模拟页重构，不复用旧 TokensPanel）。
 *
 * 保留不可消失契约：今天/7/30/90 天窗、组件筛选含 OCR、5 张汇总卡（输入/输出拆分、
 * 缓存/推理子口径、请求轮次按 trace 去重）、条件列（缓存/推理有数据才出现）、
 * 调用明细分页表（含 Trace 跳转）、成本由后端价格治理表计算（未登记价格不伪造）。
 * 模型清单热加载自登记表 + llm_usage 实际用量，未知模型兜底展示。
 */

const RANGE_OPTIONS = [
  { days: 1, label: '今天' },
  { days: 7, label: '近 7 天' },
  { days: 30, label: '近 30 天' },
  { days: 90, label: '近 90 天' },
]

const COMPONENT_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: 'llm', label: 'LLM' },
  { value: 'embedding', label: 'Embedding' },
  { value: 'rerank', label: 'Reranker' },
  { value: 'ocr', label: 'OCR' },
]

const CALL_PAGE_SIZE = 20

type MetricMode = 'tokens' | 'cost'

function formatNum(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString('zh-CN')
}

function formatCostCny(n: number | undefined | null): string {
  // 成本数值 2026-10-01 起为记账本位币 CNY
  return `¥${(n ?? 0).toFixed(2)}`
}

function formatLocalTime(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}:${ss}`
}

function compBadge(comp: string): { label: string; cls: string } {
  if (comp === 'llm') return { label: 'LLM', cls: 'bg-blue-100 text-blue-700' }
  if (comp === 'embedding') return { label: 'Emb', cls: 'bg-emerald-100 text-emerald-700' }
  if (comp === 'ocr') return { label: 'OCR', cls: 'bg-amber-100 text-amber-700' }
  return { label: 'Rnk', cls: 'bg-violet-100 text-violet-700' }
}

export default function TokensUsagePanel() {
  const toast = useToast()
  const [data, setData] = useState<TokensSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)
  const [component, setComponent] = useState('all')
  const [metric, setMetric] = useState<MetricMode>('tokens')

  const [calls, setCalls] = useState<TokenCallRow[]>([])
  const [callsTotal, setCallsTotal] = useState(0)
  const [callsPage, setCallsPage] = useState(1)
  const [callsModel, setCallsModel] = useState('')
  const [callsLoading, setCallsLoading] = useState(false)
  const [showCalls, setShowCalls] = useState(false)

  const load = useCallback(async (d: number, comp: string) => {
    setLoading(true)
    try {
      setData(await getTokensSummary(d, comp === 'all' ? undefined : comp))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载 Token 用量失败')
    } finally {
      setLoading(false)
    }
  }, [toast])

  useEffect(() => { void load(days, component) }, [load, days, component])

  const loadCalls = useCallback(async (d: number, page: number, model: string, comp: string) => {
    setCallsLoading(true)
    try {
      const r = await getTokensCalls(d, {
        model: model || undefined,
        component: comp === 'all' ? undefined : comp,
        limit: CALL_PAGE_SIZE,
        offset: (page - 1) * CALL_PAGE_SIZE,
      })
      setCalls(r.calls)
      setCallsTotal(r.total)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载调用明细失败')
    } finally {
      setCallsLoading(false)
    }
  }, [toast])

  useEffect(() => {
    if (showCalls) void loadCalls(days, callsPage, callsModel, component)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCalls, days, callsPage, callsModel, component])

  const totals = data?.totals
  const daily = data?.daily ?? []
  const models = data?.models ?? []
  const maxDaily = Math.max(1, ...daily.map((d) => (metric === 'cost' ? d.cost_cny : d.total_tokens)))
  const hasCached = (totals?.cached_tokens ?? 0) > 0
  const hasReasoning = (totals?.reasoning_tokens ?? 0) > 0
  const rangeLabel = RANGE_OPTIONS.find((r) => r.days === days)?.label ?? `${days} 天`

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Token 用量</h2>
          <p className="mt-1 max-w-3xl text-sm text-text-secondary">
            按组件 × 模型的用量与费用 · 成本由后端价格治理表计算并返回，未登记生效价格不会伪造成本；
            模型清单热加载自登记表与实际用量。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load(days, component)}
          disabled={loading}
          className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-60"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} aria-hidden="true" />刷新
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2" aria-label="组件与时间范围">
        <div className="flex overflow-hidden rounded-lg border border-border-subtle bg-white">
          {COMPONENT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              aria-pressed={component === opt.value}
              onClick={() => { setComponent(opt.value); setCallsPage(1) }}
              className={`px-3 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${component === opt.value ? 'bg-accent text-white' : 'text-text-secondary hover:bg-bg-hover'}`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div className="flex overflow-hidden rounded-lg border border-border-subtle bg-white">
          {RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.days}
              type="button"
              aria-pressed={days === opt.days}
              onClick={() => { setDays(opt.days); setCallsPage(1) }}
              className={`px-3 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${days === opt.days ? 'bg-accent text-white' : 'text-text-secondary hover:bg-bg-hover'}`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">总 Token</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.total_tokens)}</div>
          <div className="mt-1 text-[11px] text-slate-400">{formatNum(totals?.calls)} 次 LLM 调用</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">输入 Token</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.prompt_tokens)}</div>
          <div className="mt-1 text-[11px] text-slate-400">{hasCached ? `含缓存命中 ${formatNum(totals?.cached_tokens)}` : 'prompt'}</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">输出 Token</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.completion_tokens)}</div>
          <div className="mt-1 text-[11px] text-slate-400">{hasReasoning ? `含推理 ${formatNum(totals?.reasoning_tokens)}` : 'completion'}</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">总成本</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatCostCny(totals?.cost_cny)}</div>
          <div className="mt-1 text-[11px] text-slate-400">记账本位币 ¥（价格表权威值）</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">请求轮次</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.requests)}</div>
          <div className="mt-1 text-[11px] text-slate-400">按 trace 去重</div>
        </div>
      </div>

      <section className="rounded-xl border border-border-subtle bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-xs font-medium text-text-secondary">每日消耗趋势（{rangeLabel}）</div>
          <div className="flex overflow-hidden rounded-lg border border-border-subtle">
            <button type="button" aria-pressed={metric === 'tokens'} onClick={() => setMetric('tokens')} className={`px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${metric === 'tokens' ? 'bg-accent text-white' : 'text-text-secondary hover:bg-bg-hover'}`}>Token 数</button>
            <button type="button" aria-pressed={metric === 'cost'} onClick={() => setMetric('cost')} className={`px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${metric === 'cost' ? 'bg-accent text-white' : 'text-text-secondary hover:bg-bg-hover'}`}>成本</button>
          </div>
        </div>
        {daily.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-xs text-slate-400">暂无数据 — 发起对话后这里会展示每日趋势</div>
        ) : (
          <div className="flex h-40 items-end gap-1.5" role="img" aria-label={`按日的${metric === 'cost' ? '成本' : 'Token'}消耗柱状图`}>
            {daily.map((d) => {
              const value = metric === 'cost' ? d.cost_cny : d.total_tokens
              const tooltip = `${d.day}：${formatCostCny(d.cost_cny)} · ${formatNum(d.total_tokens)} tok`
              return (
                <div key={d.day} className="flex flex-1 cursor-pointer flex-col justify-end" title={tooltip}>
                  <div className="rounded-t bg-violet-400 transition-[height]" style={{ height: `${Math.max(2, (value / maxDaily) * 100)}%` }} />
                  <div className="mt-1 text-center text-[10px] text-slate-400">{d.day.slice(5).replace('-', '/')}</div>
                </div>
              )
            })}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-border-subtle bg-white">
        <div className="border-b border-slate-100 px-4 py-3 text-xs font-medium text-text-secondary">按模型细分（Provider / Model 热加载自登记表与实际用量）</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-left text-[10px] uppercase tracking-widest text-slate-400">
                <th className="px-4 py-2.5 font-medium">Provider / Model</th>
                <th className="px-4 py-2.5 text-right font-medium">调用次数</th>
                <th className="px-4 py-2.5 text-right font-medium">输入 Token</th>
                <th className="px-4 py-2.5 text-right font-medium">输出 Token</th>
                <th className="px-4 py-2.5 text-right font-medium">总 Token</th>
                {hasCached && <th className="px-4 py-2.5 text-right font-medium">缓存命中</th>}
                {hasReasoning && <th className="px-4 py-2.5 text-right font-medium">推理</th>}
                <th className="px-4 py-2.5 text-right font-medium">成本 ¥</th>
              </tr>
            </thead>
            <tbody>
              {models.length === 0 ? (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-slate-400">{loading ? '加载中…' : '该组件此窗口无调用记录'}</td></tr>
              ) : (
                models.map((m, i) => (
                  <tr key={`${m.provider}-${m.model}-${i}`} className="border-b border-slate-50 hover:bg-slate-50/60">
                    <td className="px-4 py-2.5">
                      <div className="font-medium text-slate-700">{m.model || <span className="text-slate-400">未知模型</span>}</div>
                      <div className="mt-0.5 text-[10px] text-slate-400">{m.provider || '-'}</div>
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.calls)}</td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.prompt_tokens)}</td>
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatNum(m.completion_tokens)}</td>
                    <td className="px-4 py-2.5 text-right font-mono font-medium tabular-nums">{formatNum(m.total_tokens)}</td>
                    {hasCached && <td className="px-4 py-2.5 text-right font-mono tabular-nums text-emerald-600">{formatNum(m.cached_tokens)}</td>}
                    {hasReasoning && <td className="px-4 py-2.5 text-right font-mono tabular-nums text-violet-600">{formatNum(m.reasoning_tokens)}</td>}
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums">{formatCostCny(m.cost_cny)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          本地服务（billing=local）有 Token 计量、无计费成本；未登记生效价格的模型不伪造成本。
        </p>
      </section>

      <section className="overflow-hidden rounded-xl border border-border-subtle bg-white">
        <button
          type="button"
          onClick={() => setShowCalls((v) => !v)}
          aria-expanded={showCalls}
          className="flex w-full items-center justify-between border-b border-slate-100 px-4 py-3 text-xs font-medium text-text-secondary hover:bg-slate-50/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <span>调用明细{callsTotal > 0 ? `（共 ${formatNum(callsTotal)} 条，分页 ${CALL_PAGE_SIZE} 条/页）` : ''}</span>
          <span>{showCalls ? '收起 ▲' : '展开 ▼'}</span>
        </button>
        {showCalls && (
          <>
            <div className="border-b border-slate-100 px-4 py-2.5">
              <select
                aria-label="按模型筛选调用明细"
                value={callsModel}
                onChange={(e) => { setCallsModel(e.target.value); setCallsPage(1) }}
                className="rounded-lg border border-border-subtle bg-white px-2 py-1 text-xs text-text-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <option value="">全部模型</option>
                {models.map((m, i) => <option key={`${m.provider}-${m.model}-${i}`} value={m.model}>{m.model || '未知模型'}</option>)}
              </select>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-[10px] uppercase tracking-widest text-slate-400">
                    <th className="px-4 py-2.5 font-medium">时间</th>
                    <th className="px-4 py-2.5 font-medium">类型</th>
                    <th className="px-4 py-2.5 font-medium">模型</th>
                    <th className="px-4 py-2.5 text-right font-medium">输入</th>
                    <th className="px-4 py-2.5 text-right font-medium">输出</th>
                    <th className="px-4 py-2.5 text-right font-medium">总 Token</th>
                    {hasCached && <th className="px-4 py-2.5 text-right font-medium">缓存</th>}
                    {hasReasoning && <th className="px-4 py-2.5 text-right font-medium">推理</th>}
                    <th className="px-4 py-2.5 text-right font-medium">成本</th>
                    <th className="px-4 py-2.5 text-right font-medium">耗时</th>
                    <th className="px-4 py-2.5 font-medium">Trace</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.length === 0 ? (
                    <tr><td colSpan={11} className="px-4 py-8 text-center text-slate-400">{callsLoading ? '加载中…' : '该时间窗内暂无调用记录'}</td></tr>
                  ) : (
                    calls.map((c, i) => {
                      const badge = compBadge(c.component)
                      return (
                        <tr key={`${c.ts}-${i}`} className="border-b border-slate-50 hover:bg-slate-50/60">
                          <td className="whitespace-nowrap px-4 py-2 font-mono tabular-nums text-slate-500">{formatLocalTime(c.ts)}</td>
                          <td className="px-4 py-2"><span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${badge.cls}`}>{badge.label}</span></td>
                          <td className="px-4 py-2">
                            <div className="font-medium text-slate-700">{c.model || '未知模型'}</div>
                            <div className="text-[10px] text-slate-400">{c.provider || '-'}</div>
                          </td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatNum(c.prompt_tokens)}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatNum(c.completion_tokens)}</td>
                          <td className="px-4 py-2 text-right font-mono font-medium tabular-nums">{formatNum(c.total_tokens)}</td>
                          {hasCached && <td className="px-4 py-2 text-right font-mono tabular-nums text-emerald-600">{formatNum(c.cached_tokens)}</td>}
                          {hasReasoning && <td className="px-4 py-2 text-right font-mono tabular-nums text-violet-600">{formatNum(c.reasoning_tokens)}</td>}
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{formatCostCny(c.cost_cny ?? c.cost_usd)}</td>
                          <td className="px-4 py-2 text-right font-mono tabular-nums text-slate-500">{c.duration_ms ? `${(c.duration_ms / 1000).toFixed(1)}s` : '-'}</td>
                          <td className="px-4 py-2">
                            {c.trace_id ? (
                              <a href={`/observability/traces/${c.trace_id}`} className="font-mono text-accent hover:underline" title={c.trace_id}>{c.trace_id.slice(0, 8)}</a>
                            ) : (
                              <span className="text-slate-300">-</span>
                            )}
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
            {callsTotal > CALL_PAGE_SIZE && (
              <div className="flex items-center justify-between px-4 py-2.5 text-xs text-text-secondary">
                <span>第 {(callsPage - 1) * CALL_PAGE_SIZE + 1}-{Math.min(callsPage * CALL_PAGE_SIZE, callsTotal)} 条 / 共 {formatNum(callsTotal)} 条</span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setCallsPage((p) => Math.max(1, p - 1))} disabled={callsPage <= 1 || callsLoading} className="rounded-lg border border-border-subtle px-3 py-1 hover:bg-slate-50 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">上一页</button>
                  <button type="button" onClick={() => setCallsPage((p) => (p * CALL_PAGE_SIZE < callsTotal ? p + 1 : p))} disabled={callsPage * CALL_PAGE_SIZE >= callsTotal || callsLoading} className="rounded-lg border border-border-subtle px-3 py-1 hover:bg-slate-50 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">下一页</button>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  )
}
