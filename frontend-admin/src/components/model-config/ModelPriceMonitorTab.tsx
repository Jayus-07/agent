'use client'

import { useState } from 'react'
import { Activity, AlertTriangle, CheckCircle2, Clock3, ExternalLink, RefreshCw, XCircle } from 'lucide-react'
import type { ModelPriceMonitorRun } from '@/api/modelPrices'

interface Props {
  runs: ModelPriceMonitorRun[]
  schedule: { timezone: string; hour: number; minute: number }
  isLoading: boolean
  isError: boolean
  isFetching: boolean
  onRefresh: () => void
}

const STATUS: Record<ModelPriceMonitorRun['status'], { label: string; className: string }> = {
  running: { label: '运行中', className: 'bg-blue-50 text-blue-700 ring-blue-200' },
  succeeded: { label: '完成', className: 'bg-emerald-50 text-emerald-700 ring-emerald-200' },
  partial: { label: '部分完成', className: 'bg-amber-50 text-amber-800 ring-amber-200' },
  failed: { label: '失败', className: 'bg-red-50 text-red-700 ring-red-200' },
}

const ALERT_LABELS: Record<string, string> = {
  MODEL_PRICE_SOURCE_CHANGED: '官方价格发生变化',
  MODEL_PRICE_SYSTEM_MISMATCH: '官方价与系统价格不一致',
  MODEL_PRICE_SOURCE_UNAVAILABLE: '价格来源不可用',
  MODEL_PRICE_MONITOR_FAILED: '巡检运行失败',
}

function dateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}

function price(value: { price_per_unit: string; unit: string; currency: string } | null): string {
  if (!value) return '未配置有效系统价格'
  const unit = value.unit === 'per_1m_tokens' ? '/ 1M tokens' : value.unit
  return `${value.currency} ${value.price_per_unit} ${unit}`
}

function zonedParts(date: Date, timezone: string): Record<string, number> {
  return Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric', hourCycle: 'h23',
  }).formatToParts(date).filter((part) => part.type !== 'literal').map((part) => [part.type, Number(part.value)]))
}

function zonedInstant(year: number, month: number, day: number, hour: number, minute: number, timezone: string): Date {
  const wallTime = Date.UTC(year, month - 1, day, hour, minute)
  let instant = wallTime
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const local = zonedParts(new Date(instant), timezone)
    const localAsUtc = Date.UTC(local.year, local.month - 1, local.day, local.hour, local.minute, local.second)
    instant = wallTime - (localAsUtc - instant)
  }
  return new Date(instant)
}

function nextRun(hour: number, minute: number, timezone: string): string {
  const now = new Date()
  const parts = zonedParts(now, timezone)
  let candidate = zonedInstant(parts.year, parts.month, parts.day, hour, minute, timezone)
  if (candidate <= now) {
    const tomorrow = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + 1))
    candidate = zonedInstant(tomorrow.getUTCFullYear(), tomorrow.getUTCMonth() + 1, tomorrow.getUTCDate(), hour, minute, timezone)
  }
  return candidate.toLocaleString('zh-CN', { timeZone: timezone, hour12: false })
}

function trustedSourceUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && [
      'console.volcengine.com', 'ark.volcengine.com', 'help.aliyun.com',
    ].includes(url.hostname.toLowerCase())
  } catch {
    return false
  }
}

export default function ModelPriceMonitorTab({ runs, schedule, isLoading, isError, isFetching, onRefresh }: Props) {
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const latest = runs[0]
  const sourcesOk = latest ? latest.sources_total - latest.sources_failed : 0
  const scheduleText = `每日 ${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}（北京时间）`

  if (isLoading) return <div className="h-36 animate-pulse rounded-xl border border-slate-100 bg-slate-50" />

  return <section className="space-y-5" aria-label="模型价格巡检">
    <div>
      <h2 className="text-sm font-semibold text-slate-900">模型价格巡检 Agent</h2>
      <p className="mt-1 text-xs text-slate-500">每日读取官方价格页并比较系统当前生效价格；发现变化或配置差异时触发现有告警流程。</p>
    </div>
    {isError && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">巡检记录暂时无法读取，请检查记忆库连接和 API 状态。</div>}
    <div className="grid gap-3 md:grid-cols-3">
      <Summary icon={<Activity size={16} />} label="最近一次运行" value={latest ? STATUS[latest.status].label : '尚未运行'} note={latest ? dateTime(latest.started_at) : '等待每日首次执行'} />
      <Summary icon={<CheckCircle2 size={16} />} label="来源读取" value={latest ? `${sourcesOk}/${latest.sources_total}` : '—'} note={latest ? `新增快照 ${latest.quotes_recorded} 条` : '火山方舟与阿里云官方页面'} />
      <Summary icon={<AlertTriangle size={16} />} label="价格差异" value={latest ? String(latest.system_mismatches) : '—'} note={latest ? `官方变化 ${latest.source_changes} 项 · 来源失败 ${latest.sources_failed} 项` : '对比官方价与当前生效计费价'} />
    </div>

    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div>
        <div className="text-sm font-medium text-slate-900">定时任务 · {scheduleText}</div>
        <div className="mt-1 text-xs text-slate-500">下一次计划运行：{nextRun(schedule.hour, schedule.minute, schedule.timezone)}（{schedule.timezone}） · 只读巡检，不会修改计费价格</div>
      </div>
      <button type="button" onClick={onRefresh} disabled={isFetching} className="inline-flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50">
        <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />刷新记录
      </button>
    </div>

    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-4 py-3">
        <h3 className="text-sm font-semibold text-slate-900">运行与操作结果</h3>
        <p className="mt-1 text-xs text-slate-500">保留最近运行记录；展开记录可查看每个官方来源、价格对比和已触发的告警。</p>
      </div>
      {!runs.length ? <div className="px-4 py-10 text-center text-sm text-slate-500">暂无巡检记录。部署迁移并启动 Beat/Worker 后，系统会在计划时间自动运行。</div> : <div className="divide-y divide-slate-100">
        {runs.map((run) => {
          const expanded = expandedRun === run.run_id
          const status = STATUS[run.status]
          return <article key={run.run_id}>
            <button type="button" aria-expanded={expanded} onClick={() => setExpandedRun(expanded ? null : run.run_id)} className="grid w-full gap-2 px-4 py-3 text-left hover:bg-slate-50 md:grid-cols-[minmax(210px,1.4fr)_100px_100px_100px_100px] md:items-center">
              <span><span className="block text-xs font-medium text-slate-900">{dateTime(run.started_at)}</span><span className="mt-1 block font-mono text-[10px] text-slate-400">{run.run_id}</span></span>
              <span><span className={`inline-flex rounded-full px-2 py-1 text-[11px] ring-1 ${status.className}`}>{status.label}</span></span>
              <span className="text-xs text-slate-600">来源失败 {run.sources_failed}/{run.sources_total}</span>
              <span className="text-xs text-slate-600">差异 {run.system_mismatches}</span>
              <span className="text-xs text-slate-500">{expanded ? '收起详情' : '查看详情'}</span>
            </button>
            {expanded && <RunDetails run={run} />}
          </article>
        })}
      </div>}
    </div>
  </section>
}

function Summary({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) {
  return <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
    <div className="flex items-center gap-2 text-[11px] text-text-muted">{icon}{label}</div>
    <div className="mt-2 font-mono text-xl font-semibold text-text-primary">{value}</div>
    <div className="mt-1 text-[10px] text-text-muted">{note}</div>
  </div>
}

function RunDetails({ run }: { run: ModelPriceMonitorRun }) {
  const prices = run.comparison_results?.prices ?? []
  const sourceChanges = run.comparison_results?.source_changes ?? []
  return <div className="space-y-4 border-t border-slate-100 bg-slate-50/70 px-4 py-4">
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
      <span className="inline-flex items-center gap-1"><Clock3 size={13} />开始 {dateTime(run.started_at)}</span>
      <span>结束 {dateTime(run.finished_at)}</span>
      <span>价格快照新增 {run.quotes_recorded} 条</span>
      {run.error_type && <span className="text-red-700">错误类型：{run.error_type}</span>}
    </div>

    <div className="grid gap-3 lg:grid-cols-3">
      {run.source_results.map((source) => <div key={source.source_id} className="rounded-lg border border-slate-200 bg-white p-3">
        <div className="flex items-start justify-between gap-2">
          <div><div className="text-xs font-semibold text-slate-900">{source.name}</div><div className="mt-1 text-[10px] text-slate-500">解析到 {source.quote_count} 项价格</div></div>
          {source.status === 'succeeded' ? <CheckCircle2 size={16} className="text-emerald-600" /> : <XCircle size={16} className="text-red-600" />}
        </div>
        {source.quotes[0]?.source_updated_at && <div className="mt-2 text-[10px] text-slate-500">页面更新时间：{source.quotes[0].source_updated_at}</div>}
        {source.error_type && <div className="mt-2 rounded bg-red-50 px-2 py-1 text-[10px] text-red-700">读取或解析失败：{source.error_type}</div>}
        {trustedSourceUrl(source.url) && <a href={source.url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex items-center gap-1 break-all text-[10px] text-blue-700 hover:underline">查看官方页面 <ExternalLink size={11} /></a>}
      </div>)}
    </div>

    {prices.length > 0 && <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className="min-w-full text-left text-xs">
        <thead className="bg-slate-50 text-[10px] text-slate-500"><tr><th className="px-3 py-2 font-medium">模型 / 维度</th><th className="px-3 py-2 font-medium">官方价格</th><th className="px-3 py-2 font-medium">系统生效价格</th><th className="px-3 py-2 font-medium">结果</th></tr></thead>
        <tbody className="divide-y divide-slate-100">{prices.map((item) => <tr key={`${item.source_id}:${item.model_name}:${item.dimension}`}>
          <td className="px-3 py-2"><span className="font-medium text-slate-800">{item.model_name}</span><span className="ml-2 text-slate-500">{item.dimension}</span></td>
          <td className="px-3 py-2 font-mono text-slate-700">{price(item.official)}</td>
          <td className="px-3 py-2 font-mono text-slate-700">{price(item.system)}{item.system?.price_table_version && <span className="ml-2 font-sans text-[10px] text-slate-400">版本 {item.system.price_table_version}</span>}</td>
          <td className="px-3 py-2">{item.matches_system ? <span className="text-emerald-700">一致</span> : <span className="font-medium text-red-700">需核对</span>}</td>
        </tr>)}</tbody>
      </table>
    </div>}

    {sourceChanges.length > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
      <div className="text-xs font-semibold text-amber-900">相较前次巡检的官方价格变化</div>
      <ul className="mt-2 space-y-1 text-xs text-amber-900">{sourceChanges.map((change, index) => <li key={`${String(change.model_name)}:${String(change.dimension)}:${index}`}>
        {String(change.model_name)} · {String(change.dimension)}：{price(change.previous as { price_per_unit: string; unit: string; currency: string })} → {price(change.official as { price_per_unit: string; unit: string; currency: string })}
      </li>)}</ul>
    </div>}

    <div className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="text-xs font-semibold text-slate-800">告警记录</div>
      {run.alert_events.length ? <ul className="mt-2 space-y-1 text-xs text-slate-600">{run.alert_events.map((alert, index) => <li key={`${alert.code}:${index}`} className="flex flex-wrap items-center gap-x-2">
        <AlertTriangle size={13} className="text-amber-600" />{ALERT_LABELS[alert.code] ?? alert.code}<span className="text-slate-400">已触发现有告警记录流程；Webhook 实际送达状态不在此处确认。</span>
      </li>)}</ul> : <div className="mt-1 text-xs text-slate-500">本次运行未触发价格巡检告警。</div>}
    </div>
  </div>
}
