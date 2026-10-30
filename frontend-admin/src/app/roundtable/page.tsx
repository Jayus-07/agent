'use client'

/**
 * /roundtable — Agent 圆桌（创意演示）
 *
 * 数据源：后端 /roundtable（backend/app/api/routes/agent_roundtable.py）。
 * 管理员发起一场多域 Agent 人设角色的圆桌对话；每个 Agent 可调用自己
 * 域内的只读工具后基于结果发言。演示功能：聊完即弃、不落库、无断线
 * 续传——停止/断连即终止编排，未发生的发言不再消耗 token。
 * 角色名单事实源在后端（GET /roundtable/participants），本页不硬编码。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  CircleCheck, CircleStop, Loader2, MessagesSquare, Play, Wrench, X,
} from 'lucide-react'
import { AssetPageShell } from '@/components/layout/AssetPageShell'
import ForbiddenCard from '@/components/shared/ForbiddenCard'
import MarkdownContent from '@/components/chat/MarkdownContent'
import {
  roundtableService,
  type RoundtableDoneEvent,
  type RoundtableMessageEvent,
  type RoundtableMetaEvent,
  type RoundtableParticipantsResponse,
  type RoundtableToolResultEvent,
} from '@/api/roundtable'
import { atLeast } from '@/lib/auth'

type Phase = 'idle' | 'running' | 'done' | 'stopped' | 'error'

/** 域 → 头像配色（与 /agents 页关系指南同款浅底深字风格）
 *  键仅为渲染辅助（头像底色），不做域归属判断——归属以 GET /agents 为准 */
const AVATAR_CLASS: Record<string, string> = {
  travel: 'bg-blue-50 text-blue-700',
  customer_service: 'bg-emerald-50 text-emerald-700',
  travel_commerce: 'bg-amber-50 text-amber-700', // subflow-literal-exempt: 纯 UI 配色映射，非归属声明
  travel_booking: 'bg-rose-50 text-rose-700', // subflow-literal-exempt: 纯 UI 配色映射，非归属声明
  selection_funnel: 'bg-violet-50 text-violet-700',
}

interface FeedItem {
  kind: 'message' | 'tool'
  payload: RoundtableMessageEvent | RoundtableToolResultEvent
}

export default function RoundtablePage() {
  const canRun = atLeast('admin')
  const [catalog, setCatalog] = useState<RoundtableParticipantsResponse | null>(null)
  const [catalogError, setCatalogError] = useState(false)
  const [topic, setTopic] = useState('国庆想带家人出游，哪里人少、体验好？预算怎么控制？')
  const [selected, setSelected] = useState<string[]>([])
  const [maxRounds, setMaxRounds] = useState(2)
  const [phase, setPhase] = useState<Phase>('idle')
  const [meta, setMeta] = useState<RoundtableMetaEvent | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [thinking, setThinking] = useState<string | null>(null)
  const [doneInfo, setDoneInfo] = useState<RoundtableDoneEvent | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const feedEndRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!canRun) return
    let cancelled = false
    roundtableService.getParticipants()
      .then((data) => {
        if (cancelled) return
        setCatalog(data)
        setSelected((prev) => (prev.length > 0
          ? prev
          : data.participants.slice(0, 3).map((p) => p.route_mode)))
      })
      .catch(() => { if (!cancelled) setCatalogError(true) })
    return () => { cancelled = true }
  }, [canRun])

  // 组件卸载即取消流（规范：组件卸载或用户停止时取消读流）
  useEffect(() => () => abortRef.current?.abort(), [])

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ block: 'end' })
  }, [feed.length, thinking])

  const estimatedCalls = Math.round(selected.length * maxRounds * 1.5)

  const start = useCallback(async () => {
    if (phase === 'running' || !catalog) return
    if (selected.length < catalog.limits.min_participants) return
    const controller = new AbortController()
    abortRef.current = controller
    setPhase('running')
    setMeta(null)
    setFeed([])
    setThinking(null)
    setDoneInfo(null)
    setErrorMessage(null)
    try {
      for await (const event of roundtableService.streamRoundtable(
        { topic: topic.trim(), participants: selected, maxRounds },
        { signal: controller.signal },
      )) {
        if (event.event === 'meta') setMeta(event.data)
        else if (event.event === 'message') {
          setThinking(null)
          setFeed((prev) => [...prev, { kind: 'message', payload: event.data }])
        } else if (event.event === 'tool_result') {
          setFeed((prev) => [...prev, { kind: 'tool', payload: event.data }])
        } else if (event.event === 'status') {
          setThinking(event.data.stage === 'budget' ? null : event.data.message)
        } else if (event.event === 'done') {
          setDoneInfo(event.data)
          setPhase('done')
        } else if (event.event === 'error') {
          setErrorMessage(event.data.message)
          setPhase('error')
        }
      }
      // 流自然结束但没有 done 帧（连接中断）：如实标记，不当成功
      setPhase((prev) => (prev === 'running' ? 'stopped' : prev))
    } catch (err) {
      if (controller.signal.aborted) {
        setPhase('stopped')
      } else {
        setErrorMessage(err instanceof Error ? err.message : '圆桌连接失败。')
        setPhase('error')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }, [catalog, maxRounds, phase, selected, topic])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  if (!canRun) return <ForbiddenCard pageName="Agent 圆桌" detail="圆桌会真实消耗 LLM 用量，仅对平台管理员开放。" />

  const running = phase === 'running'
  const limits = catalog?.limits

  return (
    <AssetPageShell
      title="Agent 圆桌"
      desc="让多个域 Agent 围绕话题展开圆桌对话：每个 Agent 可先调用自己领域内的只读工具获取真实数据，再基于结果发言。演示功能，聊完即弃、不落库。"
    >
      {!catalog && !catalogError ? (
        <p className="text-[13px] text-text-secondary">正在读取圆桌角色清单…</p>
      ) : catalogError || !catalog ? (
        <p role="alert" className="text-[13px] text-red-600">圆桌角色清单读取失败，请刷新重试。</p>
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[340px_1fr]">
          {/* 配置面板 */}
          <section aria-label="圆桌配置" className="space-y-4 rounded-xl border border-border-subtle bg-surface-base p-4 shadow-card">
            <div>
              <label htmlFor="rt-topic" className="mb-1 block text-[12px] font-medium text-text-secondary">讨论话题</label>
              <textarea
                id="rt-topic"
                value={topic}
                maxLength={limits?.topic_max_chars ?? 200}
                onChange={(e) => setTopic(e.target.value)}
                disabled={running}
                rows={3}
                className="w-full rounded-lg border border-border-default px-3 py-2 text-[13px] text-text-primary focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-60"
              />
            </div>
            <div>
              <span className="mb-1 block text-[12px] font-medium text-text-secondary">
                参与 Agent（{limits?.min_participants ?? 2}~{limits?.max_participants ?? 5} 个）
              </span>
              <div className="space-y-1.5">
                {catalog.participants.map((p) => {
                  const on = selected.includes(p.route_mode)
                  return (
                    <label
                      key={p.route_mode}
                      className={`flex cursor-pointer items-center gap-2.5 rounded-lg border px-2.5 py-2 transition-colors ${on ? 'border-accent/50 bg-accent-soft' : 'border-border-subtle hover:bg-bg-hover'}`}
                    >
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={running}
                        onChange={() => setSelected((prev) => (on ? prev.filter((m) => m !== p.route_mode) : [...prev, p.route_mode]))}
                        className="h-4 w-4 accent-[#4D6BFE]"
                      />
                      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[12px] font-medium ${AVATAR_CLASS[p.route_mode] ?? 'bg-bg-elevated text-text-secondary'}`}>
                        {p.display_name.charAt(0)}
                      </span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-text-primary">{p.display_name}</span>
                        <span className="block truncate text-[11px] text-text-muted">{p.label} · {p.tools.length} 个只读工具</span>
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
            <div>
              <label htmlFor="rt-rounds" className="mb-1 block text-[12px] font-medium text-text-secondary">轮数</label>
              <div className="flex items-center gap-2.5">
                <select
                  id="rt-rounds"
                  value={maxRounds}
                  disabled={running}
                  onChange={(e) => setMaxRounds(Number(e.target.value))}
                  className="rounded-lg border border-border-default bg-surface-base px-2 py-1.5 text-[13px] text-text-primary focus:border-accent focus:outline-none"
                >
                  {Array.from({ length: limits?.max_rounds ?? 4 }, (_, i) => i + 1).map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
                <span className="text-[11px] text-text-muted">预计约 {estimatedCalls} 次 LLM 调用</span>
              </div>
            </div>
            {running ? (
              <button
                type="button"
                onClick={stop}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-red-200 bg-surface-base py-2 text-[13px] font-medium text-red-600 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300"
              >
                <CircleStop size={15} /> 停止圆桌
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void start()}
                disabled={selected.length < (limits?.min_participants ?? 2) || !topic.trim()}
                className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-accent py-2 text-[13px] font-medium text-white hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Play size={14} /> 开始圆桌
              </button>
            )}
            <p className="text-center text-[11px] text-text-muted">
              管理员专用 · 用量按域归因计入 LLM 台账 · 停止后未发生的发言不再消耗 token
            </p>
          </section>

          {/* 对话流 */}
          <section aria-label="圆桌对话" aria-live="polite" className="flex min-h-[520px] flex-col gap-3 rounded-xl border border-border-subtle bg-surface-base p-4 shadow-card">
            {meta && (
              <div className="border-b border-border-subtle pb-2.5">
                <p className="text-[13px] font-medium text-text-primary">{meta.topic}</p>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-text-muted">
                  <span>在座：</span>
                  {meta.participants.map((p) => (
                    <span key={p.route_mode} className="rounded-full bg-accent-soft px-2 py-0.5 text-accent">{p.display_name}</span>
                  ))}
                  <span>· {meta.max_rounds} 轮</span>
                </div>
              </div>
            )}

            {phase === 'idle' && !meta && (
              <div className="m-auto flex flex-col items-center gap-2 text-center text-text-muted">
                <MessagesSquare size={28} strokeWidth={1.5} />
                <p className="text-[13px]">配置左侧话题与参与者，点击「开始圆桌」</p>
              </div>
            )}

            {feed.map((item, i) => item.kind === 'message'
              ? <MessageBubble key={i} msg={item.payload as RoundtableMessageEvent} />
              : <ToolLine key={i} tool={item.payload as RoundtableToolResultEvent} />)}

            {thinking && running && (
              <div className="flex items-center gap-2 px-1 text-[13px] text-text-secondary">
                <Loader2 size={14} className="animate-spin text-accent" /> {thinking}
              </div>
            )}

            {doneInfo && (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-800">
                <CircleCheck size={14} className="text-emerald-600" />
                <span>圆桌结束</span>
                <span>耗时 {doneInfo.elapsed}s</span>
                <span>{doneInfo.message_count} 条发言</span>
                <span>{doneInfo.rounds} 轮</span>
              </div>
            )}
            {phase === 'stopped' && (
              <div className="flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elevated px-3 py-2 text-[12px] text-text-secondary">
                <X size={14} /> 已停止：未完成的发言不会继续（演示功能，断开即弃）。
              </div>
            )}
            {phase === 'error' && (
              <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                {errorMessage ?? '圆桌异常终止。'}
              </div>
            )}
            <div ref={feedEndRef} />
          </section>
        </div>
      )}
    </AssetPageShell>
  )
}

function MessageBubble({ msg }: { msg: RoundtableMessageEvent }) {
  return (
    <article className="flex gap-2.5">
      <span
        aria-hidden="true"
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[13px] font-medium ${AVATAR_CLASS[msg.agent] ?? 'bg-bg-elevated text-text-secondary'}`}
      >
        {msg.display_name.charAt(0)}
      </span>
      <div className="min-w-0 flex-1 rounded-xl border border-border-subtle px-3.5 py-2.5">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-medium text-text-primary">{msg.display_name}</span>
          <span className="rounded-full bg-bg-hover px-2 py-0.5 text-[11px] text-text-secondary">{msg.agent_label}</span>
          <span className="ml-auto text-[11px] text-text-muted">第 {msg.round} 轮</span>
        </div>
        <div className="text-[13.5px] leading-relaxed text-text-primary [&>:first-child]:mt-0 [&>:last-child]:mb-0">
          <MarkdownContent content={msg.content} />
        </div>
      </div>
    </article>
  )
}

function ToolLine({ tool }: { tool: RoundtableToolResultEvent }) {
  return (
    <div className="ml-[42px] flex items-center gap-2 rounded-lg border border-border-subtle bg-bg-elevated px-2.5 py-1.5 text-[12px] text-text-secondary">
      <Wrench size={12} className="shrink-0 text-accent" />
      <span>{tool.display_name} 调用</span>
      <code className="rounded bg-bg-hover px-1.5 py-0.5 text-[11px] text-text-primary">{tool.capability}</code>
      <span className={tool.ok ? 'text-emerald-700' : 'text-red-600'}>
        {tool.ok ? '✓' : '✗'} {tool.summary}
      </span>
    </div>
  )
}
