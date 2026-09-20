'use client'

/**
 * / — 运营总览（管理端首页，2026-09-16 替代原 redirect /observability）
 *
 * 一屏回答两个问题：系统现在有没有事（健康/告警/错误率）、
 * 有没有等我处理的事（待审批、待复核、失败任务）。
 * 卡片 = 主数值 + 副信息 + 跳转链接；数据全部来自既有 api 域，逐卡独立
 * 加载（单卡失败不拖垮整页），错误降级为 "—" 并在卡上标记。
 * 语义色无 tailwind token，沿用全站内联 hex（同登录页 #FCEBEB/#FAEEDA 体系）。
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import {
  Activity, AlertTriangle, CheckCircle2, ChevronRight, Clock,
  Database, ShieldCheck, Sparkles, TrendingUp, XCircle, WalletCards, MessageSquareText,
} from 'lucide-react'
import { getTokensSummary, getTraceStats, type TokensSummary } from '@/api/observability'
import { evaluationService } from '@/api/evaluation'
import { approvalService } from '@/api/approvals'
import { competitorService } from '@/api/competitor'
import { selectionDecisionApi } from '@/api/selectionDecision'
import { fetchRaw } from '@/api/client'
import { getBudgetSummary } from '@/api/budgets'
import { listPriceVersions } from '@/api/modelPrices'
import { listFeedbackCandidates } from '@/api/feedbackCandidates'
import { atLeast } from '@/lib/auth'

type Loadable<T> = { state: 'loading' | 'ok' | 'error'; data: T | null }

function useAsync<T>(fn: () => Promise<T>): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ state: 'loading', data: null })
  const run = useCallback(() => {
    setState({ state: 'loading', data: null })
    fn().then(
      (data) => setState({ state: 'ok', data }),
      () => setState({ state: 'error', data: null }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { run() }, [run])
  return state
}

const fmtInt = (n: number | null | undefined) =>
  n == null ? '—' : n.toLocaleString('zh-CN')
const fmtUsd = (n: number | null | undefined) =>
  n == null ? '—' : `$${n.toFixed(2)}`
const fmtPct = (n: number | null | undefined) =>
  n == null ? '—' : `${(n * 100).toFixed(1)}%`

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

/** 迷你占位条（Skeleton 组件是表格骨架，卡片数值用这个更合适） */
function Pulse({ w = 96 }: { w?: number }) {
  return <span className="inline-block h-6 animate-pulse rounded bg-slate-100" style={{ width: w }} />
}

/* ── 卡片 ───────────────────────────────────────────────── */

function StatCard(props: {
  icon: React.ReactNode
  title: string
  value: React.ReactNode
  sub?: React.ReactNode
  state: 'loading' | 'ok' | 'error'
  href: string
}) {
  const { icon, title, value, sub, state, href } = props
  return (
    <Link
      href={href}
      className="group block rounded-xl border border-black/5 bg-white p-4 shadow-card transition-shadow hover:shadow-md"
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-[12px] text-text-secondary">
          {icon}
          {title}
        </span>
        <ChevronRight size={14} className="text-text-muted transition-transform group-hover:translate-x-0.5" />
      </div>
      <div className="mt-2 text-2xl font-semibold text-text-primary">
        {state === 'loading' ? <Pulse /> : value}
      </div>
      <div className="mt-1 flex min-h-[16px] items-center text-[11px] text-text-muted">
        {state === 'error' ? <span style={{ color: '#791F1F' }}>加载失败</span> : sub}
      </div>
    </Link>
  )
}

function TodoRow(props: {
  icon: React.ReactNode
  title: string
  count: number | null
  loading: boolean
  error: boolean
  href: string
  /** count > 0 时的强调色：danger=红（需要立即处理） */
  tone?: 'danger' | 'warning'
}) {
  const { icon, title, count, loading, error, href, tone = 'warning' } = props
  const urgent = count != null && count > 0
  return (
    <Link href={href} className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-surface-hover">
      <span className="text-text-muted">{icon}</span>
      <span className="flex-1 text-[13px] text-text-secondary">{title}</span>
      {loading ? (
        <Pulse w={40} />
      ) : error ? (
        <span className="text-[12px] text-text-muted">—</span>
      ) : (
        <span
          className="rounded-full px-2 py-0.5 text-[12px] font-medium"
          style={{
            background: urgent ? (tone === 'danger' ? '#FCEBEB' : '#FAEEDA') : 'rgba(0,0,0,0.04)',
            color: urgent ? (tone === 'danger' ? '#791F1F' : '#633806') : 'var(--text-muted)',
          }}
        >
          {count}
        </span>
      )}
    </Link>
  )
}

/* ── 页面 ───────────────────────────────────────────────── */

export default function AdminDashboard() {
  // 服务健康：/health 返回 { status, rag }
  const health = useAsync(async () => {
    const res = await fetchRaw('/api/health')
    if (!res.ok) throw new Error(String(res.status))
    return (await res.json()) as { status?: string; rag?: { status?: string } }
  })

  const tokens = useAsync<TokensSummary>(async () => {
    const s = await getTokensSummary(7)
    if (!s || typeof s !== 'object') throw new Error('bad payload')
    return s
  })

  const traces = useAsync(async () => {
    const s = await getTraceStats(24)
    if (!s || typeof s !== 'object') throw new Error('bad payload')
    return s
  })

  const evals = useAsync(async () => {
    const runs = await evaluationService.listRuns(1)
    return runs[0] ?? null
  })

  const approvals = useAsync(async () => {
    const r = await approvalService.list('pending', 100)
    return r.total ?? r.items?.length ?? 0
  })

  const ragStats = useAsync(async () => {
    // /rag/stats 返回 doc_count/chunk_count 等；待复核数在 /rag/pending 的 total
    const [statsRes, pendingRes] = await Promise.all([
      fetchRaw('/api/rag/stats'),
      fetchRaw('/api/rag/pending?limit=1'),
    ])
    if (!statsRes.ok) throw new Error(String(statsRes.status))
    const stats = (await statsRes.json()) as { doc_count?: number }
    const pending = pendingRes.ok
      ? ((await pendingRes.json()) as { total?: number }).total ?? 0
      : 0
    return { docCount: stats.doc_count ?? 0, pending }
  })

  const competitor = useAsync(async () => {
    const r = await competitorService.getStats()
    return r.stats ?? null
  })

  const decisions = useAsync(async () => {
    const r = await selectionDecisionApi.list(1, 50)
    const tasks = r.tasks ?? []
    return { failed: tasks.filter((t) => t.status === 'failed').length, total: tasks.length }
  })

  const budget = useAsync(getBudgetSummary)
  const prices = useAsync(listPriceVersions)
  const feedback = useAsync(async () => {
    if (!atLeast('admin')) return { items: [] }
    return listFeedbackCandidates('pending')
  })

  const daily = tokens.data?.daily ?? []
  const today = daily[daily.length - 1]
  const ragOk = health.data?.rag?.status ? health.data.rag.status !== 'error' : null

  return (
    <div>
      {/* 页头 */}
      <div className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-lg font-semibold text-text-primary">运营总览</h1>
          <p className="mt-1 text-xs text-text-muted">
            系统状态与待办速览 · 明细见「可观测 / 运营干预」各页
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="rounded-lg border border-black/10 px-3 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-surface-hover"
        >
          刷新
        </button>
      </div>

      {/* 系统状态 + 用量 */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <StatCard
          icon={<Activity size={14} />}
          title="服务状态"
          state={health.state}
          value={health.state === 'ok' ? (health.data?.status === 'ok' ? '正常' : '异常') : '—'}
          sub={
            health.state === 'ok' ? (
              <span>
                app OK
                {ragOk != null && (
                  <span style={{ color: ragOk ? '#2F7D32' : '#791F1F' }}>
                    {' '}· RAG {ragOk ? 'OK' : '异常'}
                  </span>
                )}
              </span>
            ) : undefined
          }
          href="/observability/alerts"
        />
        <StatCard
          icon={<TrendingUp size={14} />}
          title="今日 Token"
          state={tokens.state}
          value={fmtInt(today?.total_tokens ?? 0)}
          sub={today ? `${fmtInt(today.calls)} 次调用 · ${fmtUsd(today.cost_usd)}` : '近 7 天汇总'}
          href="/observability/tokens"
        />
        <StatCard
          icon={<Sparkles size={14} />}
          title="7 天成本"
          state={tokens.state}
          value={fmtUsd(tokens.data?.totals?.cost_usd)}
          sub={`${fmtInt(tokens.data?.totals?.calls)} 次 LLM 调用`}
          href="/observability/tokens"
        />
        <StatCard
          icon={<CheckCircle2 size={14} />}
          title="24h 问答成功率"
          state={traces.state}
          value={fmtPct(num(traces.data?.success_rate))}
          sub={
            traces.data
              ? `${fmtInt(traces.data.total_24h)} 条 · ${fmtInt(traces.data.error_count)} 错误 · p95 ${(traces.data.p95_duration_ms / 1000).toFixed(1)}s`
              : undefined
          }
          href="/observability/traces"
        />
        <StatCard
          icon={<WalletCards size={14} />}
          title="预算阻断"
          state={budget.state}
          value={fmtInt(budget.data?.blocked_subjects)}
          sub={budget.data ? `价格覆盖率 ${fmtPct(budget.data.price_coverage_ratio)}` : undefined}
          href="/cost-governance/budgets"
        />
        <StatCard
          icon={<MessageSquareText size={14} />}
          title="待反馈候选"
          state={feedback.state}
          value={fmtInt(feedback.data?.items.length)}
          sub={atLeast('admin') ? '待审核后才能进入评测集' : '管理员可查看'}
          href="/evaluations/feedback"
        />
      </div>

      {/* 待办 + 业务概览 两栏 */}
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-black/5 bg-white p-2 shadow-card">
          <h2 className="px-3 pb-1 pt-2 text-[13px] font-medium text-text-primary">待我处理</h2>
          <TodoRow
            icon={<ShieldCheck size={16} />}
            title="待审批工具调用（写操作需人工确认）"
            count={approvals.state === 'ok' ? approvals.data : null}
            loading={approvals.state === 'loading'}
            error={approvals.state === 'error'}
            href="/approvals"
            tone="danger"
          />
          <TodoRow
            icon={<WalletCards size={16} />}
            title="预算已达上限主体"
            count={budget.state === 'ok' ? budget.data?.blocked_subjects ?? 0 : null}
            loading={budget.state === 'loading'}
            error={budget.state === 'error'}
            href="/cost-governance/budgets"
            tone="danger"
          />
          <TodoRow
            icon={<AlertTriangle size={16} />}
            title="价格缺口或待审核版本"
            count={prices.state === 'ok' ? (budget.data && budget.data.price_coverage_ratio < 1 ? 1 : (prices.data?.items.filter((item) => item.status === 'pending' || item.status === 'reviewed_1').length ?? 0)) : null}
            loading={prices.state === 'loading' || budget.state === 'loading'}
            error={prices.state === 'error' || budget.state === 'error'}
            href="/cost-governance/prices"
          />
          <TodoRow
            icon={<MessageSquareText size={16} />}
            title="待审核反馈候选"
            count={feedback.state === 'ok' ? feedback.data?.items.length ?? 0 : null}
            loading={feedback.state === 'loading'}
            error={feedback.state === 'error'}
            href="/evaluations/feedback"
          />
          <TodoRow
            icon={<Database size={16} />}
            title="知识库待复核文档"
            count={ragStats.state === 'ok' ? ragStats.data?.pending ?? 0 : null}
            loading={ragStats.state === 'loading'}
            error={ragStats.state === 'error'}
            href="/knowledge/pending"
          />
          <TodoRow
            icon={<XCircle size={16} />}
            title="选品决策失败任务"
            count={decisions.state === 'ok' ? decisions.data?.failed ?? 0 : null}
            loading={decisions.state === 'loading'}
            error={decisions.state === 'error'}
            href="/selection-decision"
            tone="danger"
          />
          <TodoRow
            icon={<AlertTriangle size={16} />}
            title="竞品降价（今日采集发现）"
            count={competitor.state === 'ok' && competitor.data ? num(competitor.data.price_drops) : null}
            loading={competitor.state === 'loading'}
            error={competitor.state === 'error'}
            href="/competitors"
          />
        </section>

        <section className="rounded-xl border border-black/5 bg-white p-2 shadow-card">
          <h2 className="px-3 pb-1 pt-2 text-[13px] font-medium text-text-primary">业务概览</h2>
          <div className="grid grid-cols-2 gap-2 p-1">
            <div className="rounded-lg bg-surface-elevated p-3">
              <div className="text-[11px] text-text-muted">知识库文档</div>
              <div className="mt-1 text-xl font-semibold text-text-primary">
                {ragStats.state === 'ok' ? fmtInt(ragStats.data?.docCount ?? 0) : '—'}
              </div>
            </div>
            <div className="rounded-lg bg-surface-elevated p-3">
              <div className="text-[11px] text-text-muted">竞品监控</div>
              <div className="mt-1 text-xl font-semibold text-text-primary">
                {competitor.state === 'ok' && competitor.data
                  ? `${fmtInt(competitor.data.enabled)} / ${fmtInt(competitor.data.total)}`
                  : '—'}
                <span className="ml-1 text-[11px] font-normal text-text-muted">启用中</span>
              </div>
            </div>
            <div className="rounded-lg bg-surface-elevated p-3">
              <div className="text-[11px] text-text-muted">最近评测通过率</div>
              <div className="mt-1 text-xl font-semibold text-text-primary">
                {evals.state === 'ok' && evals.data ? fmtPct(evals.data.pass_rate) : '—'}
              </div>
              {evals.data && (
                <div className="mt-0.5 truncate text-[11px] text-text-muted">
                  {evals.data.module} · <Link className="text-accent hover:underline" href="/evaluations">{evals.data.run_id.slice(0, 8)}</Link>
                </div>
              )}
            </div>
            <div className="rounded-lg bg-surface-elevated p-3">
              <div className="text-[11px] text-text-muted">选品决策任务</div>
              <div className="mt-1 text-xl font-semibold text-text-primary">
                {decisions.state === 'ok' ? fmtInt(decisions.data?.total ?? 0) : '—'}
                <span className="ml-1 text-[11px] font-normal text-text-muted">近 50 条</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-1.5 px-3 pb-2 pt-1 text-[11px] text-text-muted">
            <Clock size={12} />
            网关 401/429 趋势见
            <Link href="/observability/gateway" className="text-accent hover:underline">网关安全</Link>
          </div>
        </section>
      </div>
    </div>
  )
}
