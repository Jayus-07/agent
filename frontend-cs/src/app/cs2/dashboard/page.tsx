'use client'

// cs-workstation-v2 P7：数据看板（/cs2/dashboard，supervisor 全量 / agent 个人）。
// 对齐原型 agent-workstation.html：范围切换（今日/近7日/近30日）联动六卡
// 涨跌；近 7 日接待/转人工双柱图；意图分布横条；坐席绩效表
// （接待量/平均首响/AHT/CSAT/质检分/当前状态，agent 仅见本人一行）。
import { useCallback, useEffect, useState } from 'react'
import {
  getDashboard,
  type DashboardRange,
  type DashboardView,
} from '@/cs2/workspace/api'

const RANGES: Array<{ key: DashboardRange; label: string }> = [
  { key: 'today', label: '今日' },
  { key: 'd7', label: '近7日' },
  { key: 'd30', label: '近30日' },
]

const STATUS_ZH: Record<string, string> = {
  online: '在线',
  busy: '忙碌',
  away: '小休',
  offline: '离线',
}

const STATUS_CLASS: Record<string, string> = {
  online: 'bg-emerald-50 text-emerald-600',
  busy: 'bg-blue-50 text-blue-600',
  away: 'bg-amber-50 text-amber-600',
  offline: 'bg-slate-100 text-slate-500',
}

function fmtDeltaPct(delta: number | null): string {
  if (delta == null) return ''
  const pct = Math.round(delta * 1000) / 10
  return `${pct >= 0 ? '+' : ''}${pct}%`
}

function fmtDeltaNum(delta: number | null, suffix: string): string {
  if (delta == null) return ''
  return `${delta >= 0 ? '+' : ''}${delta}${suffix}`
}

function fmtSeconds(seconds: number | null): string {
  if (seconds == null) return '—'
  return `${seconds}s`
}

function fmtAht(seconds: number | null): string {
  if (seconds == null) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m${String(s).padStart(2, '0')}s`
}

function TrendBars({ trend }: { trend: DashboardView['trend'] }) {
  const max = Math.max(1, ...trend.map((p) => p.received))
  return (
    <div className="flex h-40 items-end gap-2">
      {trend.map((point) => (
        <div key={point.date} className="flex flex-1 flex-col items-center gap-1">
          <div className="flex h-32 w-full items-end justify-center gap-0.5">
            <div
              className="w-2.5 rounded-t bg-indigo-500/80"
              style={{ height: `${Math.max(2, (point.received / max) * 100)}%` }}
              title={`${point.date} 接待量 ${point.received}`}
            />
            <div
              className="w-2.5 rounded-t bg-amber-400/80"
              style={{ height: `${Math.max(2, (point.human / max) * 100)}%` }}
              title={`${point.date} 转人工 ${point.human}`}
            />
          </div>
          <span className="text-[10px] text-slate-400">{point.date}</span>
        </div>
      ))}
    </div>
  )
}

export default function Cs2DashboardPage() {
  const [range, setRange] = useState<DashboardRange>('today')
  const [view, setView] = useState<DashboardView | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async (r: DashboardRange) => {
    setLoading(true)
    setError(null)
    try {
      setView(await getDashboard(r))
    } catch {
      setError('看板数据加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload(range)
  }, [reload, range])

  const cards = view?.cards
  const maxIntent = Math.max(1, ...(view?.intents ?? []).map((i) => i.count))

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <h1 className="text-sm font-semibold text-slate-900">数据看板</h1>
        <div className="ml-4 flex gap-1">
          {RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              className={
                range === item.key
                  ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
                  : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
              }
              onClick={() => setRange(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {view && (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">
            {view.scope === 'all' ? 'supervisor：全量' : 'agent：个人'}
          </span>
        )}
        {error && (
          <span className="ml-auto text-[11px] text-red-500">{error}</span>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        {loading && !view && (
          <p className="text-xs text-slate-400">加载中…</p>
        )}
        {cards && (
          <>
            <div className="grid grid-cols-3 gap-3 xl:grid-cols-6">
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">排队中</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {cards.queue_waiting}
                </p>
                <p className="text-[10px] text-slate-400">实时</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">进行中</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {cards.in_progress}
                </p>
                <p className="text-[10px] text-slate-400">实时</p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">接待量</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {cards.received.value}
                </p>
                <p className="text-[10px] text-emerald-600">
                  {fmtDeltaPct(cards.received.delta_pct)}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">转人工率</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {cards.handoff_rate.value != null
                    ? `${Math.round(cards.handoff_rate.value * 100)}%`
                    : '—'}
                </p>
                <p className="text-[10px] text-slate-500">
                  {fmtDeltaNum(cards.handoff_rate.delta_pp, 'pp')}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">平均首响</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {fmtSeconds(cards.avg_first_reply_s.value)}
                </p>
                <p className="text-[10px] text-slate-500">
                  {fmtDeltaNum(cards.avg_first_reply_s.delta_s, 's')}
                </p>
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="text-[11px] text-slate-400">CSAT</p>
                <p className="mt-1 text-xl font-semibold text-slate-900">
                  {cards.csat.value ?? '—'}
                </p>
                <p className="text-[10px] text-slate-500">
                  {fmtDeltaNum(cards.csat.delta, '')}
                </p>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <div className="mb-2 flex items-center gap-3 text-[11px] text-slate-500">
                  <b className="text-xs text-slate-900">近 7 日接待量</b>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded bg-indigo-500/80" />
                    接待量
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="h-2 w-2 rounded bg-amber-400/80" />
                    转人工
                  </span>
                </div>
                <TrendBars trend={view?.trend ?? []} />
              </div>
              <div className="rounded-xl border border-slate-200 bg-white p-3">
                <p className="mb-2 text-xs font-semibold text-slate-900">
                  意图分布（当前窗口）
                </p>
                {(view?.intents ?? []).length === 0 && (
                  <p className="text-xs text-slate-400">窗口内暂无意图数据</p>
                )}
                {(view?.intents ?? []).map((item) => (
                  <div
                    key={item.intent}
                    className="mb-1.5 flex items-center gap-2 text-[11px]"
                  >
                    <span className="w-24 truncate text-slate-600" title={item.intent}>
                      {item.intent}
                    </span>
                    <div className="h-2.5 flex-1 overflow-hidden rounded bg-slate-100">
                      <div
                        className="h-full rounded bg-indigo-500/80"
                        style={{ width: `${(item.count / maxIntent) * 100}%` }}
                      />
                    </div>
                    <span className="w-12 text-right text-slate-500">
                      {Math.round(item.pct * 1000) / 10}%
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <p className="mb-2 text-xs font-semibold text-slate-900">
                坐席绩效（{view?.scope === 'all' ? '全员' : '本人'}）
              </p>
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-2 py-1.5 font-medium">坐席</th>
                    <th className="px-2 py-1.5 font-medium">接待量</th>
                    <th className="px-2 py-1.5 font-medium">平均首响</th>
                    <th className="px-2 py-1.5 font-medium">AHT</th>
                    <th className="px-2 py-1.5 font-medium">CSAT</th>
                    <th className="px-2 py-1.5 font-medium">质检分</th>
                    <th className="px-2 py-1.5 font-medium">当前状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(view?.agents ?? []).map((row) => (
                    <tr key={row.agent_id} className="border-t border-slate-100">
                      <td className="px-2 py-1.5 font-medium text-slate-900">
                        {row.display_name}
                      </td>
                      <td className="px-2 py-1.5 text-slate-600">{row.handled}</td>
                      <td className="px-2 py-1.5 text-slate-600">
                        {fmtSeconds(row.avg_first_reply_s)}
                      </td>
                      <td className="px-2 py-1.5 text-slate-600">
                        {fmtAht(row.aht_s)}
                      </td>
                      <td className="px-2 py-1.5 text-slate-600">
                        {row.csat ?? '—'}
                      </td>
                      <td className="px-2 py-1.5 text-slate-600">
                        {row.qa_score ?? '—'}
                      </td>
                      <td className="px-2 py-1.5">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] ${
                            STATUS_CLASS[row.status] ?? 'bg-slate-100 text-slate-500'
                          }`}
                        >
                          {STATUS_ZH[row.status] ?? row.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {(view?.agents ?? []).length === 0 && (
                    <tr>
                      <td className="px-2 py-2 text-slate-400" colSpan={7}>
                        暂无坐席
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
