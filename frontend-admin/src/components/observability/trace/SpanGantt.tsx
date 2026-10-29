'use client'

import { useMemo, useState } from 'react'
import { AlertTriangle, ArrowUpDown, Circle, Database, GitBranch, Search, ShieldCheck, Sparkles, Wrench } from 'lucide-react'
import type { Span } from '@/types/trace'

/**
 * Span 甘特时间线（2026-10-30 详情页重构，替代旧 StepTimeline/FlameGraph 双区块）。
 *
 * - 按 start_time 实测偏移定位（非等宽比例条），能看到并行、等待与空洞；
 * - 层级缩进按 parent_id 链推导；
 * - 未埋点黑洞：root metrics.uncovered_ms > 0 时以斜纹段标注在时间线尾部；
 * - 点击行展开：指标 / Input / Output / 事件 / 错误 / HTTP 拆分。
 */

const TYPE_STYLE: Record<string, { icon: typeof Circle; bar: string }> = {
  agent: { icon: GitBranch, bar: 'bg-violet-400' },
  llm_call: { icon: Sparkles, bar: 'bg-indigo-400' },
  retrieval: { icon: Search, bar: 'bg-cyan-400' },
  rerank: { icon: ArrowUpDown, bar: 'bg-cyan-400' },
  retrieval_gate: { icon: ShieldCheck, bar: 'bg-cyan-300' },
  rerank_gate: { icon: ShieldCheck, bar: 'bg-cyan-300' },
  embedding: { icon: Database, bar: 'bg-slate-400' },
  tool_call: { icon: Wrench, bar: 'bg-amber-400' },
  memory: { icon: Database, bar: 'bg-slate-400' },
}
const FALLBACK_STYLE = { icon: Circle, bar: 'bg-violet-300' }

function parseMs(iso: string | undefined, fallback: number): number {
  if (!iso) return fallback
  const t = Date.parse(iso)
  return Number.isNaN(t) ? fallback : t
}

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function preview(value: unknown, limit = 400): string {
  if (value === null || value === undefined || value === '') return ''
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 1)
  return text.length > limit ? `${text.slice(0, limit)}…（已截断）` : text
}

interface Props {
  spans: Span[]
  /** 根实测总耗时（服务端 duration_ms 权威值） */
  totalMs: number
  /** 未埋点黑洞毫秒数（root metrics.uncovered_ms） */
  uncoveredMs?: number
}

export default function SpanGantt({ spans, totalMs, uncoveredMs = 0 }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const layout = useMemo(() => {
    const byId = new Map(spans.map((s) => [s.id, s]))
    const depthOf = (span: Span, seen = new Set<string>()): number => {
      let depth = 0
      let cursor: string | null = span.parent_id
      while (cursor && byId.has(cursor) && !seen.has(cursor)) {
        seen.add(cursor)
        depth += 1
        cursor = byId.get(cursor)!.parent_id
      }
      return Math.min(depth, 5)
    }
    const t0 = spans.length ? Math.min(...spans.map((s) => parseMs(s.start_time, 0))) : 0
    const rows = spans
      .map((s) => {
        const start = Math.max(0, parseMs(s.start_time, t0) - t0)
        const dur = s.duration_ms > 0 ? s.duration_ms : Math.max(0, parseMs(s.end_time, start) - start)
        return { span: s, depth: depthOf(s), start, dur }
      })
      .sort((a, b) => a.start - b.start || a.depth - b.depth)
    const spanEnd = rows.reduce((max, r) => Math.max(max, r.start + r.dur), 0)
    const scale = Math.max(totalMs, spanEnd + (uncoveredMs > 0 ? uncoveredMs : 0), 1)
    return { rows, scale, spanEnd }
  }, [spans, totalMs, uncoveredMs])

  const { rows, scale, spanEnd } = layout
  const counts = useMemo(() => {
    const c = { llm: 0, ret: 0, tool: 0, total: spans.length }
    spans.forEach((s) => {
      if (s.type === 'llm_call') c.llm += 1
      else if (s.type === 'retrieval' || s.type === 'rerank') c.ret += 1
      else if (s.type === 'tool_call') c.tool += 1
    })
    return c
  }, [spans])

  const toggle = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
        <span>根实测耗时 <strong className="tabular-nums text-text-primary">{fmtMs(totalMs)}</strong></span>
        <span>Span <strong className="tabular-nums text-text-primary">{counts.total}</strong></span>
        <span>LLM <strong className="tabular-nums text-text-primary">{counts.llm}</strong></span>
        <span>检索/重排 <strong className="tabular-nums text-text-primary">{counts.ret}</strong></span>
        <span>工具 <strong className="tabular-nums text-text-primary">{counts.tool}</strong></span>
        {uncoveredMs > 0 && <span className="text-orange-600">未埋点 <strong className="tabular-nums">{fmtMs(uncoveredMs)}</strong>（{Math.round((uncoveredMs / scale) * 100)}%）</span>}
      </div>

      <div className="relative mb-1 ml-[214px] mr-[70px] hidden h-4 sm:block" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <span key={p} className="absolute -translate-x-1/2 font-mono text-[10px] text-text-muted" style={{ left: `${p * 100}%` }}>{fmtMs(Math.round(scale * p))}</span>
        ))}
      </div>

      <div className="relative">
        <div className="pointer-events-none absolute inset-y-0 left-[214px] right-[70px] hidden sm:block" aria-hidden="true">
          {[0.25, 0.5, 0.75].map((p) => (
            <i key={p} className="absolute inset-y-0 w-px bg-border-subtle opacity-60" style={{ left: `${p * 100}%` }} />
          ))}
        </div>
        {rows.map(({ span: s, depth, start, dur }) => {
          const style = TYPE_STYLE[s.type] ?? FALLBACK_STYLE
          const Icon = style.icon
          const bad = s.status === 'error' || s.status === 'timeout'
          const isOpen = expanded.has(s.id)
          const leftPct = (start / scale) * 100
          const widthPct = Math.max(0.5, (dur / scale) * 100)
          return (
            <div key={s.id}>
              <button
                type="button"
                onClick={() => toggle(s.id)}
                aria-expanded={isOpen}
                className="flex w-full items-center rounded py-1 text-left hover:bg-violet-50/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <span className="flex min-w-0 flex-none items-center gap-1.5 text-xs" style={{ width: 214, paddingLeft: depth * 14 }}>
                  <Icon size={13} className="shrink-0 text-slate-400" aria-hidden="true" />
                  <span className="truncate">{s.name || s.id}</span>
                </span>
                <span className="relative mr-[70px] h-3.5 flex-1">
                  <span
                    className={`absolute top-0.5 h-2.5 min-w-[3px] rounded-full ${style.bar} ${bad ? '!bg-red-400' : ''}`}
                    style={{ left: `${Math.min(leftPct, 98)}%`, width: `${Math.min(widthPct, 100 - Math.min(leftPct, 98))}%` }}
                    title={`${s.name}：${fmtMs(dur)}${s.llm_call ? ` · ${s.llm_call.model}` : ''}`}
                  />
                  <span className={`absolute right-0 top-0 w-16 text-right font-mono text-[11px] tabular-nums ${bad ? 'text-red-600' : 'text-text-secondary'}`}>{fmtMs(dur)}</span>
                </span>
              </button>
              {isOpen && (
                <div className="mb-1.5 ml-[214px] rounded-lg border border-border-subtle bg-bg-elevated p-3 sm:mr-[70px]">
                  <div className="grid grid-cols-2 gap-x-4 md:grid-cols-3">
                    <p className="text-[11px] text-text-muted">状态<span className="ml-1 font-mono text-text-secondary">{s.status}</span></p>
                    <p className="text-[11px] text-text-muted">类型<span className="ml-1 font-mono text-text-secondary">{s.type}</span></p>
                    {s.llm_call && <p className="text-[11px] text-text-muted">模型<span className="ml-1 font-mono text-text-secondary">{s.llm_call.model}</span></p>}
                    {Object.entries(s.metrics ?? {}).filter(([key]) => !['skipped_stages'].includes(key)).slice(0, 9).map(([key, value]) => (
                      <p key={key} className="truncate text-[11px] text-text-muted" title={`${key}=${String(value)}`}>{key}<span className="ml-1 font-mono text-text-secondary">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span></p>
                    ))}
                  </div>
                  {preview(s.input) && (
                    <>
                      <p className="mt-2 text-[10px] uppercase tracking-widest text-text-muted">Input</p>
                      <pre className="mt-1 max-h-28 overflow-hidden whitespace-pre-wrap break-all rounded bg-slate-50 p-2 font-mono text-[11px] text-text-secondary">{preview(s.input)}</pre>
                    </>
                  )}
                  {preview(s.output) && (
                    <>
                      <p className="mt-2 text-[10px] uppercase tracking-widest text-text-muted">Output</p>
                      <pre className="mt-1 max-h-28 overflow-hidden whitespace-pre-wrap break-all rounded bg-slate-50 p-2 font-mono text-[11px] text-text-secondary">{preview(s.output)}</pre>
                    </>
                  )}
                  {s.errors.length > 0 && <p className="mt-2 rounded bg-red-50 px-2 py-1 font-mono text-[11px] text-red-700">{s.errors.join('；')}</p>}
                  {s.events.length > 0 && (
                    <p className="mt-2 text-[11px] text-text-muted">
                      事件：{s.events.slice(0, 4).map((e) => (e.message ? `${e.name}（${e.message}）` : e.name)).join('；')}
                    </p>
                  )}
                  {s.http_breakdown && (
                    <>
                      <p className="mt-2 text-[10px] uppercase tracking-widest text-text-muted">HTTP 拆分（出站）</p>
                      {(() => {
                        const h = s.http_breakdown!
                        const parts: Array<[string, number]> = [['DNS', h.dns_ms], ['TLS', h.tls_ms], ['建连', h.connect_ms], ['首字节', h.ttfb_ms], ['传输', h.body_ms]]
                        const sum = parts.reduce((a, [, v]) => a + v, 0) || 1
                        return (
                          <>
                            <div className="mt-1 flex h-3 overflow-hidden rounded" aria-hidden="true">
                              {parts.map(([label, v]) => (
                                <span key={label} className="min-w-[2px]" title={`${label} ${v}ms`} style={{ width: `${Math.max(1, (v / sum) * 100)}%`, background: label === '首字节' ? '#818cf8' : label === '传输' ? '#34d399' : '#cbd5e1' }} />
                              ))}
                            </div>
                            <p className="mt-0.5 text-[10.5px] text-text-muted">{parts.map(([label, v]) => `${label} ${v}ms`).join(' · ')}</p>
                          </>
                        )
                      })()}
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}
        {uncoveredMs > 0 && (
          <div className="flex items-center py-1" role="note" aria-label={`未埋点黑洞 ${fmtMs(uncoveredMs)}`}>
            <span className="flex flex-none items-center gap-1.5 text-[11px] text-orange-600" style={{ width: 214 }}>
              <AlertTriangle size={13} aria-hidden="true" />未埋点黑洞（重试退避等）
            </span>
            <span className="relative mr-[70px] h-3.5 flex-1">
              <span
                className="absolute top-0.5 h-2.5 rounded-sm"
                style={{
                  left: `${Math.min((spanEnd / scale) * 100, 98)}%`,
                  width: `${Math.max(0.5, (uncoveredMs / scale) * 100)}%`,
                  background: 'repeating-linear-gradient(45deg,#fdba74,#fdba74 3px,transparent 3px,transparent 6px)',
                }}
                title={`未埋点 ${fmtMs(uncoveredMs)}：span 之间的无埋点时段（root 归因 covered/uncovered 口径）`}
              />
              <span className="absolute right-0 top-0 w-16 text-right font-mono text-[11px] tabular-nums text-orange-600">{fmtMs(uncoveredMs)}</span>
            </span>
          </div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-violet-400" aria-hidden="true" />图/节点</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-indigo-400" aria-hidden="true" />LLM</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-cyan-400" aria-hidden="true" />检索/重排/门禁</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-amber-400" aria-hidden="true" />工具</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-slate-400" aria-hidden="true" />记忆/向量</span>
        <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-red-400" aria-hidden="true" />失败</span>
        <span>点击行展开指标 / Input / Output / HTTP 拆分</span>
      </div>
    </div>
  )
}
