'use client'

/**
 * /observability/gateway — 网关安全（APISIX 认证拒绝 / 限流）
 *
 * 数据源：后端 /observability/gateway-auth 只读代理 Prometheus
 * （apisix_gateway_auth_denied_total / apisix_http_status）。
 * Prometheus 属可选 observability profile：不可达时页面显式降级，
 * 给出启动指引而不是报错。
 *
 * reason 与网关插件枚举对齐：no-credential / expired / signature /
 * blacklist / issuer / missing-user-id / token-type-mismatch / ...
 */
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { RefreshCw, ShieldAlert } from 'lucide-react'
import { clsx } from 'clsx'
import TraceBreadcrumb from '@/components/observability/trace/TraceBreadcrumb'
import {
  getGatewayAuthMetrics,
  getGatewayAccessLogs,
  type GatewayAuthReasonRow,
  type GatewayAccessLogRow,
} from '@/api/observability'

const WINDOWS = [
  { hours: 1, label: '近 1 小时' },
  { hours: 6, label: '近 6 小时' },
  { hours: 24, label: '近 24 小时' },
  { hours: 168, label: '近 7 天' },
]

/** 管理员可读的拒绝原因 */
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

function reasonLabel(r: string) {
  return REASON_LABELS[r] ?? r
}

const CODE_LABELS: Record<string, string> = {
  '401': '401 未认证（网关拒绝）',
  '429': '429 限流命中',
}

/** 极简横向条形（无图表库依赖，同全站风格） */
function BarRow(props: { label: string; value: number; max: number; color: string; note?: string }) {
  const { label, value, max, color, note } = props
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 2
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-44 shrink-0 truncate text-[12px] text-slate-500" title={label}>{label}</span>
      <div className="h-4 flex-1 overflow-hidden rounded-sm bg-slate-100">
        <div className="h-full rounded-sm" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="w-16 shrink-0 text-right text-[12px] tabular-nums text-slate-800">{value.toLocaleString('zh-CN')}</span>
      {note && <span className="w-24 shrink-0 truncate text-[11px] text-slate-400">{note}</span>}
    </div>
  )
}

/** 内联 sparkline（纯 div 柱状，5 分钟粒度；带 y 峰值与 x 首尾时间标注） */
function Sparkbars({ series }: { series: { ts: number; value: number }[] }) {
  if (series.length === 0) {
    return <div className="py-8 text-center text-[12px] text-slate-400">窗口内无拒绝记录</div>
  }
  const max = Math.max(...series.map((p) => p.value), 0.0001)
  const maxInt = Math.ceil(max)
  const first = new Date(series[0].ts * 1000)
  const last = new Date(series[series.length - 1].ts * 1000)
  const fmtAxis = (d: Date) => d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  return (
    <div>
      {/* y 轴峰值标注 */}
      <div className="mb-0.5 text-right text-[10px] tabular-nums text-slate-400">峰值 {maxInt.toLocaleString('zh-CN')} / 格</div>
      <div className="flex h-20 items-end gap-[3px]">
        {series.map((p) => {
          const h = Math.max(3, (p.value / max) * 100)
          return (
            <div
              key={p.ts}
              className="flex-1 rounded-t-sm bg-violet-300 transition-colors hover:bg-violet-400"
              style={{ height: `${h}%` }}
              title={`${new Date(p.ts * 1000).toLocaleString('zh-CN')}：${Math.round(p.value)} 次`}
            />
          )
        })}
      </div>
      {/* x 轴首尾时间 */}
      <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400">
        <span>{fmtAxis(first)}</span>
        <span>{fmtAxis(last)}</span>
      </div>
    </div>
  )
}

export default function GatewayPage() {
  const [hours, setHours] = useState(6)
  const [data, setData] = useState<Awaited<ReturnType<typeof getGatewayAuthMetrics>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(h = hours, silent = false) {
    if (!silent) setLoading(true)
    setError('')
    try {
      setData(await getGatewayAuthMetrics(h))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(hours) /* eslint-disable-line react-hooks/exhaustive-deps */ }, [hours])

  const denied: GatewayAuthReasonRow[] = data?.denied_by_reason ?? []
  const maxDenied = Math.max(...denied.map((d) => d.count), 0)

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        <TraceBreadcrumb crumbs={[{ label: '可观测中心', href: '/observability' }, { label: '网关安全' }]} />

        {/* Header：左标题 + 右操作（时间窗切换 / 刷新），同 traces 页布局 */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">网关安全</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              APISIX 认证拒绝与限流命中 · 数据源 Prometheus（observability profile）· 30s 内为近似实时
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 text-xs">
              {WINDOWS.map((w) => (
                <button
                  key={w.hours}
                  onClick={() => setHours(w.hours)}
                  className={clsx('rounded-md px-3 py-1.5 transition-colors',
                    hours === w.hours ? 'bg-violet-50 font-medium text-violet-700' : 'text-slate-500 hover:text-slate-800')}
                >
                  {w.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => load()}
              disabled={loading}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
            </button>
          </div>
        </div>

        {loading ? (
          <div className="bg-white border border-slate-200 rounded-xl py-12 text-center text-sm text-slate-400">加载中…</div>
        ) : error ? (
          <div className="bg-white border border-slate-200 rounded-xl py-12 text-center text-sm text-red-500">{error}</div>
        ) : !data?.available ? (
          /* Prometheus 未启动：显式降级 + 指引 */
          <div className="bg-white border border-slate-200 rounded-xl p-12 text-center">
            <ShieldAlert size={28} className="mx-auto mb-3 text-slate-400" />
            <div className="text-sm font-medium text-slate-800">Prometheus 数据源不可用</div>
            <p className="mx-auto mt-2 max-w-[440px] text-xs leading-relaxed text-slate-500">
              网关指标由 Prometheus 抓取（observability profile，可选）。启动后本页自动恢复：
            </p>
            <code className="mt-3 inline-block rounded-lg bg-slate-50 border border-slate-200 px-3 py-1.5 text-xs font-mono text-slate-600">
              docker compose --profile observability up -d prometheus
            </code>
          </div>
        ) : (
          <>
            {/* 汇总 KPI 卡（同 traces StatsBar 口径：小标签 + 等宽大数字） */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="bg-white border border-slate-200 rounded-xl px-4 py-3">
                <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">
                  认证拒绝（{WINDOWS.find((w) => w.hours === hours)?.label}）
                </p>
                <p
                  className="text-lg font-bold font-mono tabular-nums"
                  style={{ color: (data.total_denied ?? 0) > 100 ? '#ef4444' : '#1e293b' }}
                  title="口径：gateway-auth 插件验签被拒次数（denied_total 按拒绝原因聚合）；与 401 计数不同——401 还包含限流等其他来源"
                >
                  {(data.total_denied ?? 0).toLocaleString('zh-CN')}
                </p>
                <p className="text-[10px] text-slate-400 mt-0.5">按拒绝原因聚合 · 超 100 触发告警</p>
              </div>
              {(data.status_codes ?? []).map((c) => (
                <div key={c.code} className="bg-white border border-slate-200 rounded-xl px-4 py-3">
                  <p className="text-[10px] uppercase tracking-widest text-slate-400 mb-1">{CODE_LABELS[c.code] ?? c.code}</p>
                  <p
                    className="text-lg font-bold font-mono tabular-nums text-slate-800"
                    title="口径：apisix_http_status 按 HTTP 状态码计数，范围比「认证拒绝」宽"
                  >
                    {Math.round(c.count).toLocaleString('zh-CN')}
                  </p>
                  <p className="text-[10px] text-slate-400 mt-0.5">按 HTTP 状态码聚合（含 deny 与限流）</p>
                </div>
              ))}
            </div>

            {/* 拒绝趋势 + 原因分布：大屏并排（趋势 3 : 分布 2），窄屏退化为纵排 */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
              <section className="bg-white border border-slate-200 rounded-xl p-4 xl:col-span-3">
                <h2 className="mb-3 text-[13px] font-medium text-slate-800">拒绝趋势（5 分钟粒度）</h2>
                <Sparkbars series={data.denied_series ?? []} />
              </section>

              <section className="bg-white border border-slate-200 rounded-xl p-4 xl:col-span-2">
                <h2 className="mb-2 text-[13px] font-medium text-slate-800">拒绝原因分布</h2>
                {denied.length === 0 ? (
                  <div className="py-6 text-center text-[12px] text-slate-400">窗口内无拒绝记录</div>
                ) : (
                  denied.map((d) => (
                    <BarRow
                      key={d.reason}
                      label={reasonLabel(d.reason)}
                      value={Math.round(d.count)}
                      max={maxDenied}
                      color="#B45309"
                    />
                  ))
                )}
              </section>
            </div>

            {/* shadow 观测（灰度复测时用） */}
            {(data.would_deny_by_reason?.length ?? 0) > 0 && (
              <section className="bg-white border border-slate-200 rounded-xl p-4">
                <h2 className="mb-2 text-[13px] font-medium text-slate-800">
                  would-deny（shadow 模式「本该拒绝」计数，当前为 enforce 时恒为 0）
                </h2>
                {data.would_deny_by_reason!.map((d) => (
                  <BarRow key={d.reason} label={reasonLabel(d.reason)} value={Math.round(d.count)} max={Math.max(...data.would_deny_by_reason!.map((x) => x.count))} color="#94a3b8" />
                ))}
              </section>
            )}
          </>
        )}

        {/* 访问审计明细：数据源独立于 Prometheus（PG 表），指标不可用时本区块仍可用 */}
        <AccessLogsSection hours={hours} />
      </div>
    </div>
  )
}

/** 状态码着色：2xx 绿 / 3xx 灰 / 429 橙 / 其他 4xx、5xx、0（无上游）红 */
function statusColor(s: number): string {
  if (s >= 200 && s < 300) return '#15803d'
  if (s >= 300 && s < 400) return '#64748b'
  if (s === 429) return '#B45309'
  return '#791F1F'
}

function fmtTime(ts: string): string {
  const d = new Date(ts)
  return isNaN(d.getTime()) ? ts : d.toLocaleString('zh-CN', { hour12: false })
}

/**
 * 访问审计明细（2026-09-16）：谁从哪个 IP 访问了什么端点、结果如何。
 * 数据链路 APISIX gateway-access-log 插件 → Redis Streams → ai.gateway_access_logs。
 * 30s 静默轮询；user_id 经 gateway-auth 验签注入可信，auth/sys 白名单路由
 * 未验签（auth_type 为空），用户列显式标注。
 */
const ACCESS_LOG_LIMIT = 100

function AccessLogsSection({ hours }: { hours: number }) {
  // 表单草稿 vs 已提交过滤词：Enter/查询按钮才触发请求，避免逐键查询
  const [draft, setDraft] = useState({ userId: '', ip: '', path: '' })
  const [filters, setFilters] = useState({ userId: '', ip: '', path: '' })
  const [offset, setOffset] = useState(0)
  // 安全页默认视角是「仅异常」：打开就看到 401/403/429，而不是一屏 200
  // （后端排序恒为异常优先，切「全部」时异常行仍置顶）。心跳/自引用默认
  // 排除，防止 30s 轮询把审计表刷成自己的查询记录。
  const [abnormalOnly, setAbnormalOnly] = useState(true)
  const [includeNoise, setIncludeNoise] = useState(false)

  // React Query 接管轮询（2026-09-16）：30s 静默轮询 + 后台标签自动暂停
  // （refetchIntervalInBackground 默认 false）+ 切回前台自动补刷
  // （Provider 级 refetchOnWindowFocus）+ 相同参数请求去重，取代手写 setInterval。
  const { data, error, refetch, isFetching } = useQuery({
    queryKey: ['gateway-access-logs', hours, filters, abnormalOnly, includeNoise, offset],
    queryFn: () =>
      getGatewayAccessLogs({ hours, ...filters, abnormalOnly, includeNoise, limit: ACCESS_LOG_LIMIT, offset }),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData, // 翻页/切视角时保留旧数据，不闪空白
  })

  // 切时间窗/过滤词/视角回到第一页；查询本身由 queryKey 变化驱动
  useEffect(() => {
    setOffset(0)
  }, [hours, filters, abnormalOnly, includeNoise])

  const logs: GatewayAccessLogRow[] = data?.logs ?? []
  const total = data?.total ?? 0

  return (
    <section className="bg-white border border-slate-200 rounded-xl p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[13px] font-medium text-slate-800">
          访问审计明细
          <span className="ml-2 text-[11px] font-normal text-slate-400">
            谁从哪个 IP 访问了什么端点 · 30s 自动刷新（后台暂停）
          </span>
        </h2>
        <div className="flex flex-wrap items-center gap-1.5">
          {/* 视角切换：默认仅异常（4xx/5xx）。心跳/自引用默认排除，防 30s 轮询自膨胀 */}
          <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 text-xs">
            <button
              type="button"
              onClick={() => setAbnormalOnly(true)}
              className={clsx('rounded-md px-2.5 py-1 transition-colors',
                abnormalOnly ? 'bg-violet-50 font-medium text-violet-700' : 'text-slate-500 hover:text-slate-800')}
            >
              仅异常
            </button>
            <button
              type="button"
              onClick={() => setAbnormalOnly(false)}
              className={clsx('rounded-md px-2.5 py-1 transition-colors',
                !abnormalOnly ? 'bg-violet-50 font-medium text-violet-700' : 'text-slate-500 hover:text-slate-800')}
            >
              全部
            </button>
          </div>
          <label
            className="flex cursor-pointer select-none items-center gap-1 text-[11px] text-slate-500"
            title="包含 /health 心跳与 /observability 自引用查询（默认排除，防审计列表自我膨胀）"
          >
            <input type="checkbox" checked={includeNoise} onChange={(e) => setIncludeNoise(e.target.checked)} className="rounded accent-violet-600" />
            含心跳/自引用
          </label>
          <form
            className="flex items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              setFilters({ userId: draft.userId.trim(), ip: draft.ip.trim(), path: draft.path.trim() })
            }}
          >
          <input
            value={draft.userId}
            onChange={(e) => setDraft({ ...draft, userId: e.target.value })}
            placeholder="用户 ID"
            className="w-24 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-700 outline-none transition-colors focus:border-violet-400"
          />
          <input
            value={draft.ip}
            onChange={(e) => setDraft({ ...draft, ip: e.target.value })}
            placeholder="IP"
            className="w-28 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-700 outline-none transition-colors focus:border-violet-400"
          />
          <input
            value={draft.path}
            onChange={(e) => setDraft({ ...draft, path: e.target.value })}
            placeholder="路径"
            className="w-28 rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-700 outline-none transition-colors focus:border-violet-400"
          />
          <button
            type="submit"
            className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 transition-colors hover:text-slate-800"
          >
            查询
          </button>
          <button
            type="button"
            onClick={() => refetch()}
            title="刷新"
            className="flex items-center rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 transition-colors hover:text-slate-700"
          >
            <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          </button>
          </form>
        </div>
      </div>

      {error ? (
        <div className="py-8 text-center text-[12px] text-red-500">
          {error instanceof Error ? error.message : '加载失败'}
        </div>
      ) : !data?.available ? (
        <div className="rounded-lg bg-slate-50 border border-slate-200 p-6 text-center text-[12px] leading-relaxed text-slate-500">
          访问日志数据源不可用（迁移未跑或 PostgreSQL 不可达）
          <code className="mt-2 block rounded bg-white border border-slate-200 px-2 py-1 text-[11px] font-mono text-slate-600">
            alembic upgrade head
          </code>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="py-2.5 px-3 w-36">时间</th>
                  <th className="py-2.5 px-3 w-32">IP</th>
                  <th className="py-2.5 px-3 w-32">用户</th>
                  <th className="py-2.5 px-3 w-14">方法</th>
                  <th className="py-2.5 px-3">路径</th>
                  <th className="py-2.5 px-3 w-14">状态</th>
                  <th className="py-2.5 px-3 w-20 text-right">耗时</th>
                  <th className="py-2.5 px-3 w-40">Trace ID</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {logs.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="py-12 text-center text-[12px] text-slate-400">
                      窗口内无访问记录
                    </td>
                  </tr>
                ) : (
                  logs.map((l, i) => (
                    <tr key={`${l.trace_id}-${l.ts}-${i}`} className="text-[12px] hover:bg-slate-50/60">
                      <td className="py-2 px-3 tabular-nums text-slate-500" title={l.ts}>{fmtTime(l.ts)}</td>
                      <td className="py-2 px-3 font-mono text-[11px] text-slate-700">{l.client_ip || '-'}</td>
                      <td className="py-2 px-3 text-slate-700">
                        {/* 用户名列优先：后端 LEFT JOIN auth.users 回显（user_id 纯数字才关联） */}
                        {l.username ? (
                          <span title={`ID: ${l.user_id ?? '-'}`}>{l.username}</span>
                        ) : l.user_id ? (
                          <span className="font-mono text-[11px]" title="user_id 非数字 ID，未关联到用户表">{l.user_id}</span>
                        ) : (
                          <span className="text-slate-400">guest</span>
                        )}
                        {l.user_id && !l.auth_type && (
                          <span className="ml-1 rounded bg-slate-100 px-1 py-0.5 text-[10px] text-slate-400" title="auth/sys 白名单路由，头为客户端自带，未经网关验签">未验签</span>
                        )}
                      </td>
                      <td className="py-2 px-3 text-slate-500">{l.method}</td>
                      <td className="py-2 px-3 font-mono text-[11px] text-slate-700">
                        <span className="block max-w-[420px] truncate" title={`${l.uri}${l.query ? `?${l.query}` : ''}  ·  ${l.ua}`}>
                          {l.uri}{l.query && <span className="text-slate-400">?{l.query}</span>}
                        </span>
                      </td>
                      <td className="py-2 px-3 font-medium tabular-nums" style={{ color: statusColor(l.status) }}>{l.status}</td>
                      <td className="py-2 px-3 text-right tabular-nums text-slate-500">{l.duration_ms.toFixed(1)}ms</td>
                      <td className="py-2 px-3 font-mono text-[11px] text-slate-400" title={l.trace_id}>
                        {l.trace_id ? l.trace_id.slice(-14) : '-'}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {/* 分页 */}
          <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-xs text-slate-500">
            <span>共 {total.toLocaleString('zh-CN')} 条 · 本页 {logs.length} 条</span>
            <div className="flex gap-1.5">
              <button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - ACCESS_LOG_LIMIT))}
                className="rounded border border-slate-200 px-3 py-1 transition-colors hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                上一页
              </button>
              <button
                disabled={offset + ACCESS_LOG_LIMIT >= total}
                onClick={() => setOffset(offset + ACCESS_LOG_LIMIT)}
                className="rounded border border-slate-200 px-3 py-1 transition-colors hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                下一页
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  )
}
