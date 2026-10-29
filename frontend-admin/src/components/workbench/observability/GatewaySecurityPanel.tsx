'use client'

import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import {
  getGatewayAuthMetrics,
  getGatewayAccessLogs,
  type GatewayAccessLogRow,
} from '@/api/observability'

/**
 * 网关安全（2026-10-30 按模拟页重构，不复用旧 GatewayPanel）。
 *
 * 数据源不变：/observability/gateway-auth（只读代理 Prometheus，不可达 available=false
 * 显式降级）+ /observability/gateway-access-logs（APISIX 插件 → Redis Streams →
 * ai.gateway_access_logs，表未建/PG 不可达 available=false 降级提示）。
 * 保留不可消失契约：30s 静默轮询（后台暂停/聚焦补刷/去重）、默认「仅异常」视角、
 * 心跳与自引用默认排除（防自膨胀）、用户/IP/路径过滤、翻页、auth 白名单未验签标注。
 */

const WINDOWS = [
  { hours: 1, label: '近 1 小时' },
  { hours: 6, label: '近 6 小时' },
  { hours: 24, label: '近 24 小时' },
  { hours: 168, label: '近 7 天' },
]

const REASON_LABELS: Record<string, string> = {
  'no-credential': '无凭据（未登录直访）',
  'expired': 'Token 过期',
  'signature': '签名校验失败',
  'blacklist': 'Token 已拉黑（登出/吊销）',
  'issuer': 'issuer 不符',
  'missing-user-id': '缺少 userId claim',
  'token-type-mismatch': 'refresh 冒充 access',
  'blacklist-timeout': '黑名单查询超时（fail-closed）',
  'blacklist-unavailable': '黑名单 Redis 不可用（fail-closed）',
  'malformed': 'Token 格式错误',
  'invalid': 'Token 无效',
}

const reasonLabel = (r: string) => REASON_LABELS[r] ?? r

const ACCESS_LOG_LIMIT = 100

function statusTone(status: number): string {
  if (status >= 500) return 'bg-red-100 text-red-700'
  if (status === 429) return 'bg-orange-100 text-orange-700'
  if (status === 403) return 'bg-amber-100 text-amber-700'
  if (status >= 400) return 'bg-red-50 text-red-600'
  return 'bg-emerald-50 text-emerald-700'
}

function hbar(label: string, value: number, max: number) {
  const width = Math.max(2, Math.round((value / (max || 1)) * 100))
  return (
    <div className="mb-1.5 flex items-center gap-2">
      <span className="w-56 shrink-0 truncate text-xs text-text-secondary" title={label}>{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div className="h-full rounded-full bg-red-400" style={{ width: `${width}%` }} />
      </div>
      <span className="w-14 text-right font-mono text-xs tabular-nums text-text-secondary">{value}</span>
    </div>
  )
}

export default function GatewaySecurityPanel() {
  const [hours, setHours] = useState(24)
  const [draft, setDraft] = useState({ userId: '', ip: '', path: '' })
  const [filters, setFilters] = useState({ userId: '', ip: '', path: '' })
  const [offset, setOffset] = useState(0)
  const [abnormalOnly, setAbnormalOnly] = useState(true)
  const [includeNoise, setIncludeNoise] = useState(false)

  const metrics = useQuery({
    queryKey: ['gateway-auth-metrics', hours],
    queryFn: () => getGatewayAuthMetrics(hours),
  })

  const logs = useQuery({
    queryKey: ['gateway-access-logs', hours, filters, abnormalOnly, includeNoise, offset],
    queryFn: () => getGatewayAccessLogs({ hours, ...filters, abnormalOnly, includeNoise, limit: ACCESS_LOG_LIMIT, offset }),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  })

  // 切时间窗/过滤词/视角回到第一页；查询由 queryKey 变化驱动
  useEffect(() => { setOffset(0) }, [hours, filters, abnormalOnly, includeNoise])

  const m = metrics.data
  const rows: GatewayAccessLogRow[] = logs.data?.logs ?? []
  const total = logs.data?.total ?? 0

  const code401 = useMemo(() => m?.status_codes?.find((c) => c.code === '401')?.count ?? 0, [m])
  const code429 = useMemo(() => m?.status_codes?.find((c) => c.code === '429')?.count ?? 0, [m])
  const otherCodes = useMemo(
    () => (m?.status_codes ?? []).filter((c) => c.code !== '401' && c.code !== '429').reduce((sum, c) => sum + c.count, 0),
    [m],
  )
  const maxDenied = useMemo(
    () => Math.max(1, ...(m?.denied_by_reason ?? []).map((r) => r.count)),
    [m],
  )
  const maxSeries = useMemo(() => Math.max(1, ...(m?.denied_series ?? []).map((p) => p.value)), [m])

  return (
    <div className="space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-text-primary">网关安全</h2>
          <p className="mt-1 max-w-3xl text-sm text-text-secondary">
            APISIX 认证与拒绝观测 · 口径注记：认证拒绝（gateway-auth 插件 denied_total）≠ 401
            （还含限流等其他来源）。数据源为只读代理，Prometheus 不可达时显式降级。
          </p>
        </div>
        <button
          type="button"
          onClick={() => { void metrics.refetch(); void logs.refetch() }}
          className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-border-subtle bg-white px-3 text-sm font-medium text-text-primary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <RefreshCw size={14} className={metrics.isFetching || logs.isFetching ? 'animate-spin' : ''} aria-hidden="true" />刷新
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2" aria-label="统计时间范围">
        {WINDOWS.map((w) => (
          <button key={w.hours} type="button" aria-pressed={hours === w.hours} onClick={() => setHours(w.hours)} className={`min-h-10 rounded-md border px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${hours === w.hours ? 'border-accent bg-violet-50 font-medium text-accent' : 'border-border-subtle bg-white text-text-secondary hover:bg-bg-hover'}`}>
            {w.label}
          </button>
        ))}
      </div>

      {!m?.available ? (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900" role="status">
          网关指标数据源不可用（Prometheus 未启动或不可达）——本页显式降级，不伪造空统计。
          {m?.error && <span className="ml-1 font-mono text-[11px]">{m.error}</span>}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <div className="min-h-24 rounded-md border border-red-200 bg-red-50 px-4 py-3">
              <span className="block text-sm text-red-700">认证拒绝</span>
              <strong className="mt-2 block font-mono text-2xl tabular-nums text-red-700">{m?.total_denied ?? 0}</strong>
              <span className="mt-1 block text-xs text-red-500">gateway-auth denied_total</span>
            </div>
            <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-sm text-text-secondary">401 总计</span>
              <strong className="mt-2 block font-mono text-2xl tabular-nums text-text-primary">{code401}</strong>
              <span className="mt-1 block text-xs text-text-muted">含限流等其他来源</span>
            </div>
            <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-sm text-text-secondary">429 限流</span>
              <strong className="mt-2 block font-mono text-2xl tabular-nums text-text-primary">{code429}</strong>
              <span className="mt-1 block text-xs text-text-muted">按路由限流插件命中</span>
            </div>
            <div className="min-h-24 rounded-md border border-border-subtle bg-white px-4 py-3">
              <span className="block text-sm text-text-secondary">其余状态码合计</span>
              <strong className="mt-2 block font-mono text-2xl tabular-nums text-text-primary">{otherCodes}</strong>
              <span className="mt-1 block text-xs text-text-muted">apisix_http_status 其余计数（含 2xx）</span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <section className="rounded-md border border-border-subtle bg-white p-4">
              <h3 className="mb-2 text-[13px] font-medium text-text-primary">拒绝趋势（5 分钟粒度）</h3>
              {(m?.denied_series ?? []).length === 0 ? (
                <p className="py-6 text-center text-xs text-text-muted">该时间窗内无拒绝记录。</p>
              ) : (
                <div className="flex h-24 items-end gap-1" role="img" aria-label="按 5 分钟粒度的拒绝次数柱状图">
                  {(m?.denied_series ?? []).map((p) => (
                    <div key={p.ts} className="flex flex-1 flex-col justify-end" title={`${new Date(p.ts * 1000).toLocaleString('zh-CN')}：${Math.round(p.value)} 次`}>
                      <div className="rounded-t bg-violet-400" style={{ height: `${Math.max(3, (p.value / maxSeries) * 100)}%`, minHeight: 2 }} />
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section className="rounded-md border border-border-subtle bg-white p-4">
              <h3 className="mb-2 text-[13px] font-medium text-text-primary">拒绝原因分布</h3>
              {(m?.denied_by_reason ?? []).length === 0 ? (
                <p className="py-6 text-center text-xs text-text-muted">该时间窗内无拒绝记录。</p>
              ) : (
                (m?.denied_by_reason ?? []).map((r) => <div key={r.reason}>{hbar(reasonLabel(r.reason), r.count, maxDenied)}</div>)
              )}
              {(m?.would_deny_by_reason ?? []).length > 0 && (
                <div className="mt-3 border-t border-dashed border-border-subtle pt-3">
                  {(m?.would_deny_by_reason ?? []).map((r) => <div key={r.reason}>{hbar(`未拦截观察 would_deny · ${reasonLabel(r.reason)}`, r.count, maxDenied)}</div>)}
                  <p className="text-[11px] text-text-muted">命中规则但处于观察模式未实际拦截（audit 灰度口径，SENSITIVE_API_GUARD_MODE）。</p>
                </div>
              )}
            </section>
          </div>
        </>
      )}

      {/* 访问审计明细 */}
      <section className="rounded-md border border-border-subtle bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-[13px] font-medium text-text-primary">
            访问审计明细
            <span className="ml-2 text-[11px] font-normal text-text-muted">谁从哪个 IP 访问了什么端点 · 30s 自动刷新（后台暂停）</span>
          </h3>
          <div className="flex flex-wrap items-center gap-1.5 text-xs">
            <div className="flex items-center gap-0.5 rounded-lg border border-border-subtle bg-white p-0.5">
              <button type="button" onClick={() => setAbnormalOnly(true)} className={`rounded-md px-2.5 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${abnormalOnly ? 'bg-violet-50 font-medium text-violet-700' : 'text-text-secondary hover:text-text-primary'}`}>仅异常</button>
              <button type="button" onClick={() => setAbnormalOnly(false)} className={`rounded-md px-2.5 py-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${!abnormalOnly ? 'bg-violet-50 font-medium text-violet-700' : 'text-text-secondary hover:text-text-primary'}`}>全部</button>
            </div>
            <label className="flex cursor-pointer select-none items-center gap-1 text-[11px] text-text-secondary" title="包含 /health 心跳与 /observability 自引用查询（默认排除，防审计列表自我膨胀）">
              <input type="checkbox" checked={includeNoise} onChange={(e) => setIncludeNoise(e.target.checked)} className="accent-violet-600" />
              含心跳/自引用
            </label>
            <form
              className="flex items-center gap-1.5"
              onSubmit={(e) => {
                e.preventDefault()
                setFilters({ userId: draft.userId.trim(), ip: draft.ip.trim(), path: draft.path.trim() })
              }}
            >
              <input value={draft.userId} onChange={(e) => setDraft({ ...draft, userId: e.target.value })} placeholder="用户 ID" aria-label="按用户 ID 过滤" className="w-24 rounded-lg border border-border-subtle px-2 py-1 text-xs text-text-primary outline-none transition-colors focus:border-violet-400" />
              <input value={draft.ip} onChange={(e) => setDraft({ ...draft, ip: e.target.value })} placeholder="IP" aria-label="按 IP 过滤" className="w-28 rounded-lg border border-border-subtle px-2 py-1 text-xs text-text-primary outline-none transition-colors focus:border-violet-400" />
              <input value={draft.path} onChange={(e) => setDraft({ ...draft, path: e.target.value })} placeholder="路径" aria-label="按路径过滤" className="w-28 rounded-lg border border-border-subtle px-2 py-1 text-xs text-text-primary outline-none transition-colors focus:border-violet-400" />
              <button type="submit" className="rounded-lg border border-border-subtle bg-white px-3 py-1.5 text-xs text-text-secondary transition-colors hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">查询</button>
            </form>
          </div>
        </div>

        {logs.error ? (
          <div className="py-8 text-center text-[12px] text-red-500" role="alert">{logs.error instanceof Error ? logs.error.message : '加载失败'}</div>
        ) : !logs.data?.available ? (
          <div className="rounded-lg border border-border-subtle bg-slate-50 p-6 text-center text-[12px] leading-relaxed text-text-secondary" role="status">
            访问日志数据源不可用（迁移未跑或 PostgreSQL 不可达）——显式降级，不伪造空表。
            <code className="mt-2 block rounded border border-border-subtle bg-white px-2 py-1 font-mono text-[11px] text-text-secondary">alembic upgrade head</code>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-100 text-left text-[10px] uppercase tracking-widest text-slate-400">
                    <th className="px-3 py-2 font-medium">时间</th>
                    <th className="px-3 py-2 font-medium">用户</th>
                    <th className="px-3 py-2 font-medium">IP</th>
                    <th className="px-3 py-2 font-medium">端点</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 font-medium">认证方式</th>
                    <th className="px-3 py-2 text-right font-medium">耗时</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {rows.length === 0 ? (
                    <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-400">{logs.isFetching ? '加载中…' : '该过滤条件下暂无记录'}</td></tr>
                  ) : (
                    rows.map((row, i) => (
                      <tr key={`${row.ts}-${i}`} className="border-b border-slate-50 hover:bg-slate-50/60">
                        <td className="whitespace-nowrap px-3 py-2 font-mono text-[11.5px] tabular-nums text-slate-500">{new Date(row.ts).toLocaleString('zh-CN', { hour12: false })}</td>
                        <td className="px-3 py-2 text-[12px]">{row.username || row.user_id || <span className="text-slate-300">—</span>}{!row.user_id && <span className="ml-1 text-[10px] text-amber-600">未验签（auth/sys 白名单）</span>}</td>
                        <td className="px-3 py-2 font-mono text-[11.5px] text-slate-600">{row.client_ip}</td>
                        <td className="max-w-[280px] truncate px-3 py-2 font-mono text-[11.5px] text-slate-700" title={`${row.method} ${row.uri}${row.query ? `?${row.query}` : ''}`}>{row.method} {row.uri}{row.query ? `?${row.query}` : ''}</td>
                        <td className="px-3 py-2"><span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${statusTone(row.status)}`}>{row.status}</span></td>
                        <td className="px-3 py-2 text-[11px] text-slate-500">{row.auth_type || <span className="text-amber-600">白名单</span>}</td>
                        <td className="px-3 py-2 text-right font-mono text-[11.5px] tabular-nums text-slate-500">{row.duration_ms}ms</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            {total > ACCESS_LOG_LIMIT && (
              <div className="flex items-center justify-between px-3 py-2 text-xs text-text-secondary">
                <span>第 {offset + 1}-{Math.min(offset + ACCESS_LOG_LIMIT, total)} 条 / 共 {total} 条</span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setOffset((o) => Math.max(0, o - ACCESS_LOG_LIMIT))} disabled={offset <= 0} className="rounded-lg border border-border-subtle px-3 py-1 hover:bg-slate-50 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">上一页</button>
                  <button type="button" onClick={() => setOffset((o) => (o + ACCESS_LOG_LIMIT < total ? o + ACCESS_LOG_LIMIT : o))} disabled={offset + ACCESS_LOG_LIMIT >= total} className="rounded-lg border border-border-subtle px-3 py-1 hover:bg-slate-50 disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">下一页</button>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  )
}
