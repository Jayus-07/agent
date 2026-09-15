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
import { RefreshCw, ShieldAlert } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/layout/PageHeader'
import {
  getGatewayAuthMetrics,
  type GatewayAuthReasonRow,
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
      <span className="w-44 shrink-0 truncate text-[12px] text-text-secondary" title={label}>{label}</span>
      <div className="h-4 flex-1 overflow-hidden rounded bg-black/[0.04]">
        <div className="h-full rounded" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="w-16 shrink-0 text-right text-[12px] tabular-nums text-text-primary">{value.toLocaleString('zh-CN')}</span>
      {note && <span className="w-24 shrink-0 truncate text-[11px] text-text-muted">{note}</span>}
    </div>
  )
}

/** 内联 sparkline（纯 div 柱状，10 分钟粒度） */
function Sparkbars({ series }: { series: { ts: number; value: number }[] }) {
  if (series.length === 0) {
    return <div className="py-8 text-center text-[12px] text-text-muted">窗口内无拒绝记录</div>
  }
  const max = Math.max(...series.map((p) => p.value), 0.0001)
  return (
    <div className="flex h-20 items-end gap-[2px]">
      {series.map((p) => {
        const h = Math.max(3, (p.value / max) * 100)
        return (
          <div
            key={p.ts}
            className="flex-1 rounded-t bg-accent/60"
            style={{ height: `${h}%` }}
            title={`${new Date(p.ts * 1000).toLocaleString('zh-CN')}：${p.value}`}
          />
        )
      })}
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
    <div>
      <PageHeader
        title="网关安全"
        desc="APISIX 认证拒绝与限流命中 · 数据源 Prometheus（observability profile），30s 内为近似实时"
      />

      {/* 窗口切换 */}
      <div className="mb-4 flex items-center gap-2">
        <div className="flex items-center gap-1 rounded-lg bg-black/[0.03] p-0.5 text-[12px]">
          {WINDOWS.map((w) => (
            <button
              key={w.hours}
              onClick={() => setHours(w.hours)}
              className={clsx('rounded-[6px] px-3 py-1.5 transition-colors',
                hours === w.hours ? 'bg-white text-accent font-medium shadow-sm' : 'text-text-secondary hover:text-text-primary')}
            >
              {w.label}
            </button>
          ))}
        </div>
        <button
          onClick={() => load()}
          className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-black/[0.03]"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {loading ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] text-text-muted shadow-card">加载中…</div>
      ) : error ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] shadow-card" style={{ color: '#791F1F' }}>{error}</div>
      ) : !data?.available ? (
        /* Prometheus 未启动：显式降级 + 指引 */
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center shadow-card">
          <ShieldAlert size={28} className="mx-auto mb-3 text-text-muted" />
          <div className="text-[14px] font-medium text-text-primary">Prometheus 数据源不可用</div>
          <p className="mx-auto mt-2 max-w-[440px] text-[12px] leading-relaxed text-text-muted">
            网关指标由 Prometheus 抓取（observability profile，可选）。启动后本页自动恢复：
          </p>
          <code className="mt-3 inline-block rounded-lg bg-black/[0.03] px-3 py-1.5 text-[12px]">
            docker compose --profile observability up -d prometheus
          </code>
        </div>
      ) : (
        <>
          {/* 汇总卡 */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
              <div className="text-[12px] text-text-secondary">认证拒绝（{WINDOWS.find((w) => w.hours === hours)?.label}）</div>
              <div className="mt-2 text-2xl font-semibold" style={{ color: (data.total_denied ?? 0) > 100 ? '#791F1F' : 'var(--text-primary)' }}>
                {(data.total_denied ?? 0).toLocaleString('zh-CN')}
              </div>
              <div className="mt-1 text-[11px] text-text-muted">超 100 触发告警规则（prometheus-alert-rules）</div>
            </div>
            {(data.status_codes ?? []).map((c) => (
              <div key={c.code} className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
                <div className="text-[12px] text-text-secondary">{CODE_LABELS[c.code] ?? c.code}</div>
                <div className="mt-2 text-2xl font-semibold text-text-primary">{Math.round(c.count).toLocaleString('zh-CN')}</div>
                <div className="mt-1 text-[11px] text-text-muted">网关层计数（含 deny 与限流）</div>
              </div>
            ))}
          </div>

          {/* 拒绝趋势 */}
          <section className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
            <h2 className="mb-3 text-[13px] font-medium text-text-primary">拒绝趋势（5 分钟粒度）</h2>
            <Sparkbars series={data.denied_series ?? []} />
          </section>

          {/* 原因分布 */}
          <section className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
            <h2 className="mb-2 text-[13px] font-medium text-text-primary">拒绝原因分布</h2>
            {denied.length === 0 ? (
              <div className="py-6 text-center text-[12px] text-text-muted">窗口内无拒绝记录</div>
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

          {/* shadow 观测（灰度复测时用） */}
          {(data.would_deny_by_reason?.length ?? 0) > 0 && (
            <section className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
              <h2 className="mb-2 text-[13px] font-medium text-text-primary">
                would-deny（shadow 模式「本该拒绝」计数，当前为 enforce 时恒为 0）
              </h2>
              {data.would_deny_by_reason!.map((d) => (
                <BarRow key={d.reason} label={reasonLabel(d.reason)} value={Math.round(d.count)} max={Math.max(...data.would_deny_by_reason!.map((x) => x.count))} color="#6b7280" />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  )
}
