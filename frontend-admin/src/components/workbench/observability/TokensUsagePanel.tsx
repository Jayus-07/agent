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
  { value: 'llm', label: '大模型' },
  { value: 'embedding', label: '向量模型' },
  { value: 'rerank', label: '重排模型' },
  { value: 'ocr', label: '文字识别' },
]

const CALL_PAGE_SIZE = 20

type MetricMode = 'tokens' | 'cost'

function formatNum(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString('zh-CN')
}

function formatCostCny(n: number | undefined | null): string {
  // 小额 Token 费用保留精度，避免 OCR 等真实费用被显示成 ¥0.00。
  const amount = n ?? 0
  const absolute = Math.abs(amount)
  const digits = amount === 0 ? 2 : absolute < 0.000001 ? 10 : absolute < 1 ? 8 : 2
  const formatted = amount.toFixed(digits).replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '')
  return `¥${formatted}`
}

function formatCostBreakdown(
  exactCostCny: number | undefined,
  estimatedCostCny: number | undefined,
  unknownCalls: number,
  localCalls = 0,
  subscriptionCalls = 0,
): string {
  const exact = exactCostCny ?? 0
  const estimated = estimatedCostCny ?? 0
  const unknownAmountCalls = unknownCalls
  const parts: string[] = []
  if (exact > 0) parts.push(formatCostCny(exact))
  if (estimated > 0) parts.push(`估算 ${formatCostCny(estimated)}`)
  if (subscriptionCalls > 0) {
    parts.push(`套餐抵扣 ${formatNum(subscriptionCalls)} 次（金额未同步）`)
  }
  if (localCalls > 0 && exact === 0 && estimated === 0 && subscriptionCalls === 0) {
    parts.push(`本地 ${formatCostCny(0)}`)
  }
  if (unknownAmountCalls > 0) {
    parts.push(`金额未知 ${formatNum(unknownAmountCalls)} 次`)
  }
  if (parts.length === 0) {
    return unknownAmountCalls > 0 ? '金额未知' : formatCostCny(0)
  }
  return parts.join(' + ')
}

function formatRecordedCost(
  costStatus: string | undefined,
  costCny: number | null | undefined,
  pricingSource: string | undefined,
  totalTokens: number,
): string {
  const status = (costStatus || '').trim().toLowerCase()
  if (status === 'local') {
    const missingUsage = totalTokens <= 0 ? '；本次 Token 用量缺失' : ''
    return `本地 ¥0.00（不含硬件、电力）${missingUsage}`
  }
  if (status === 'subscription') return '套餐抵扣（人民币金额未同步）'
  if (status === 'unpriced') return '金额未知（未计价）'
  if (!status || status === 'missing') return '历史计价状态缺失'
  if (status === 'price_unknown' || pricingSource === 'registry_fallback') return '价格未核验'
  if (status === 'estimated') {
    if (costCny === undefined || costCny === null) return '估算金额未返回'
    const basis = pricingSource === 'official_historical_price' ? '（供应商公开价）' : ''
    return `估算 ${formatCostCny(costCny)}${basis}`
  }
  if (status !== 'exact') return '计价状态异常'
  return costCny === undefined || costCny === null ? '成本未返回' : formatCostCny(costCny)
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
  if (comp === 'llm') return { label: '大模型', cls: 'bg-blue-100 text-blue-700' }
  if (comp === 'embedding') return { label: '向量', cls: 'bg-emerald-100 text-emerald-700' }
  if (comp === 'ocr') return { label: '文字识别', cls: 'bg-amber-100 text-amber-700' }
  return { label: '重排', cls: 'bg-violet-100 text-violet-700' }
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
  const pricingReady = totals?.cost_classification_version === 2
  const exactCalls = totals?.exact_calls ?? 0
  const daily = data?.daily ?? []
  const models = data?.models ?? []
  const maxDaily = Math.max(1, ...daily.map((d) => (metric === 'cost' ? d.cost_cny : d.total_tokens)))
  const hasCached = (totals?.cached_tokens ?? 0) > 0
  const hasReasoning = (totals?.reasoning_tokens ?? 0) > 0
  const unpricedCalls = totals?.unpriced_calls ?? 0
  const missingCostStatusCalls = totals?.missing_cost_status_calls ?? 0
  const unverifiedPriceCalls = totals?.unverified_price_calls ?? 0
  const unknownCostCalls = totals?.unknown_cost_calls
    ?? unpricedCalls + missingCostStatusCalls + (totals?.price_unknown_calls ?? 0)
  const historicalEstimateCalls = totals?.historically_estimated_calls ?? 0
  const localCostCalls = totals?.local_cost_calls ?? 0
  const localUsageMissingCalls = totals?.local_usage_missing_calls ?? 0
  const subscriptionCalls = totals?.subscription_cost_calls ?? 0
  const exactCostCny = totals?.exact_cost_cny ?? 0
  const estimatedCostCny = totals?.estimated_cost_cny ?? 0
  const totalCalls = totals?.calls ?? 0
  const classifiedCostCalls = exactCalls + (totals?.estimated_calls ?? 0)
    + localCostCalls + subscriptionCalls + unknownCostCalls
  const costClassificationGap = totalCalls - classifiedCostCalls
  const costCoveragePercent = totalCalls > 0
    ? Math.round((classifiedCostCalls / totalCalls) * 100)
    : 0
  const rangeLabel = RANGE_OPTIONS.find((r) => r.days === days)?.label ?? `${days} 天`

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Token 用量</h2>
          <p className="mt-1 max-w-3xl text-sm text-text-secondary">
            按组件和模型查看用量与费用；历史估算使用调用当日可追溯的供应商公开价。公开标价不等于最终账单，无法核价的记录单列。
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
          <div className="mt-1 text-[11px] text-slate-400">{formatNum(totals?.calls)} 次模型调用（含 OCR、向量和重排）</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">输入 Token</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.prompt_tokens)}</div>
          <div className="mt-1 text-[11px] text-slate-400">{hasCached ? `含缓存命中 ${formatNum(totals?.cached_tokens)}` : '模型输入'}</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] uppercase tracking-widest text-slate-400">输出 Token</span>
          <div className="mt-2 font-mono text-xl font-semibold tabular-nums text-slate-800">{loading ? '—' : formatNum(totals?.completion_tokens)}</div>
          <div className="mt-1 text-[11px] text-slate-400">{hasReasoning ? `含推理 ${formatNum(totals?.reasoning_tokens)}` : '模型输出'}</div>
        </div>
        <div className="rounded-xl border border-border-subtle bg-white p-4">
          <span className="text-[10px] tracking-wide text-slate-400">费用分类</span>
          {loading ? (
            <div className="mt-2 font-mono text-xl text-slate-800">—</div>
          ) : pricingReady ? (
            <>
              <div className="mt-2 space-y-1 text-xs leading-5 text-slate-700">
                {exactCostCny > 0 ? (
                  <div className="flex items-baseline justify-between gap-2">
                    <span>精确计价</span>
                    <strong className="font-mono text-sm tabular-nums">{formatCostCny(exactCostCny)}</strong>
                  </div>
                ) : (
                  <div className="text-slate-500">精确计价 {formatNum(exactCalls)} 次</div>
                )}
                {estimatedCostCny > 0 && (
                  <div className="flex items-baseline justify-between gap-2">
                    <span>公开价估算</span>
                    <strong className="font-mono text-sm tabular-nums text-amber-700">{formatCostCny(estimatedCostCny)}</strong>
                  </div>
                )}
              </div>
              <div className="mt-2 flex flex-wrap gap-1" aria-label="非账单费用分类">
                {subscriptionCalls > 0 && <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-800">套餐 {formatNum(subscriptionCalls)} 次</span>}
                {localCostCalls > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-700">本地 {formatNum(localCostCalls)} 次</span>}
                {unknownCostCalls > 0 && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-800">未知 {formatNum(unknownCostCalls)} 次</span>}
              </div>
            </>
          ) : (
            <div className="mt-2 text-sm text-slate-600">计价口径未确认</div>
          )}
          <div className="mt-2 text-[11px] leading-4 text-slate-400">
            {pricingReady
              ? `分类覆盖 ${formatNum(classifiedCostCalls)}/${formatNum(totalCalls)} 次（${costCoveragePercent}%）；公开价估算 ${formatNum(historicalEstimateCalls)} 次`
              : '后端尚未确认费用分类版本，已隐藏金额，避免把旧估算当作账单。'}
            {pricingReady && costClassificationGap !== 0 && `；分类合计与调用总数相差 ${formatNum(costClassificationGap)} 次`}
            {pricingReady && unverifiedPriceCalls > 0 && `；旧目录回退价记录 ${formatNum(unverifiedPriceCalls)} 次已按事件时配置重判`}
            {pricingReady && missingCostStatusCalls > 0 && `；原计价状态缺失 ${formatNum(missingCostStatusCalls)} 次`}
            {pricingReady && localUsageMissingCalls > 0 && `；其中本地 Token 数缺失 ${formatNum(localUsageMissingCalls)} 次`}
          </div>
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
        {metric === 'cost' && !pricingReady ? (
          <div className="flex h-40 items-center justify-center text-xs text-text-muted">计价口径未确认，暂不显示金额趋势</div>
        ) : daily.length === 0 ? (
          <div className="flex h-40 items-center justify-center text-xs text-slate-400">暂无数据 — 发起对话后这里会展示每日趋势</div>
        ) : (
          <div className="flex h-40 items-stretch gap-1.5" role="img" aria-label={`按日的${metric === 'cost' ? '成本' : 'Token'}消耗柱状图`}>
            {daily.map((d) => {
              const value = metric === 'cost' ? d.cost_cny : d.total_tokens
              const planCalls = d.subscription_cost_calls ?? 0
              const unknownCalls = d.unknown_cost_calls ?? 0
              const tooltip = `${d.day}：${formatCostCny(d.cost_cny)}${(d.estimated_cost_cny ?? 0) > 0 ? `（含估算 ${formatCostCny(d.estimated_cost_cny)}）` : ''}${planCalls > 0 ? ` + 套餐抵扣 ${planCalls} 次（金额未同步）` : ''}${unknownCalls > 0 ? ` + 金额未知 ${unknownCalls} 次` : ''} · ${formatNum(d.total_tokens)} Token`
              const barHeight = value > 0 ? Math.max(2, (value / maxDaily) * 100) : 1
              return (
                <div key={d.day} className="flex min-w-0 flex-1 cursor-pointer flex-col justify-end" title={tooltip}>
                  <div className="flex h-32 w-full items-end">
                    <div className="w-full rounded-t bg-violet-400" style={{ height: `${barHeight}%` }} />
                  </div>
                  <div className="mt-1 text-center text-[10px] text-slate-400">{d.day.slice(5).replace('-', '/')}</div>
                </div>
              )
            })}
          </div>
        )}
        {metric === 'cost' && pricingReady && (unknownCostCalls > 0 || historicalEstimateCalls > 0 || localCostCalls > 0 || subscriptionCalls > 0) && (
          <p className="mt-2 text-[11px] text-amber-700">
            图表金额包含可信精确成本和单列估算，不包含套餐金额未同步部分及其他未知金额；本地 ¥0 仅代表不产生云端 Token 账单，不含硬件、电力费用。
          </p>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-border-subtle bg-white">
        <div className="border-b border-slate-100 px-4 py-3 text-xs font-medium text-text-secondary">按模型细分（服务商和模型来自登记表与实际用量）</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-100 text-left text-[10px] uppercase tracking-widest text-slate-400">
                <th className="px-4 py-2.5 font-medium">服务商 / 模型</th>
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
                    <td className="px-4 py-2.5 text-right font-mono tabular-nums">
                      {pricingReady ? <>
                        <div>{formatCostBreakdown(
                        m.exact_cost_cny,
                        m.estimated_cost_cny,
                        m.unknown_cost_calls ?? 0,
                        m.local_cost_calls ?? 0,
                        m.subscription_cost_calls ?? 0,
                        )}</div>
                      {(m.historically_estimated_calls ?? 0) > 0 && (
                        <div className="text-[10px] text-amber-600">供应商公开价估算 {formatNum(m.historically_estimated_calls)} 次</div>
                      )}
                      {(m.registry_fallback_calls ?? 0) > 0 && (
                        <div className="text-[10px] text-amber-600">
                          历史目录回退价记录 {formatNum(m.registry_fallback_calls)} 次
                        </div>
                      )}
                      {(m.local_usage_missing_calls ?? 0) > 0 && (
                        <div className="text-[10px] text-amber-600">本地 Token 数缺失 {formatNum(m.local_usage_missing_calls)} 次</div>
                      )}
                      {(m.missing_cost_status_calls ?? 0) > 0 && (
                        <div className="text-[10px] text-amber-600">原计价状态缺失 {formatNum(m.missing_cost_status_calls)} 次</div>
                      )}
                      </> : '计价口径未确认'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <p className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
          {pricingReady
            ? '精确费用与公开价估算分开标注；本地模型费用为 ¥0 只表示不产生云端 Token 账单，不含硬件、电力费用。未核价记录不会显示为 ¥0。'
            : '后端计价口径未确认，费用金额已隐藏。'}
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
                          <td className="px-4 py-2 text-right font-mono tabular-nums">{pricingReady ? formatRecordedCost(c.cost_display_status ?? c.cost_status, c.cost_cny, c.cost_display_source ?? c.pricing_source, c.total_tokens) : '计价口径未确认'}</td>
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
