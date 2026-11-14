'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'
import rehypeHighlight from 'rehype-highlight'
import {
  AlertCircle,
  ArrowRight,
  ArrowUp,
  Brain,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  FileText,
  LoaderCircle,
  MessageSquareText,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  StopCircle,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import { NAV } from '@/components/layout/navConfig'
import { getCachedUser } from '@/lib/auth'
import { useBudgetStatus } from '@/hooks/useBudgetStatus'
import { useSendMessage } from '@/hooks/useChat'
import { useAbortStreamOnUnmount, useSSE } from '@/hooks/useSSE'
import {
  deleteMemorySession,
  listSessionsPage,
  renameMemorySession,
  SESSION_PAGE_SIZE,
  type SessionMeta,
} from '@/api/memory'
import { formatCny, type BudgetStatus } from '@/api/budgets'
import { invalidateSessionsCache, getSessionPageCached } from '@/lib/sessions-cache'
import { BUCKET_LABELS, filterByKeyword, formatTime, groupByTime } from '@/lib/session-groups'
import { useChatStore } from '@/store/chat'
import type { Message, SSEStreamEvent, StatusEvent, TodoItem } from '@/lib/types'
import {
  CHAT_INPUT_MAX_CHARS,
  chatInputOverLimit,
  chatInputShowCounter,
} from '@/lib/chatInputLimit'
import { listAgentTasks, restoreAgentTasks } from '@/api/agentTasks'
import { useAgentTask } from '@/hooks/useAgentTask'
import { presentAssistantAnswer } from './assistantAnswerPresentation'

const PAUSE_MESSAGE = '个人额度不足，本轮已暂停。补足额度后可以重新发送这条问题。'

function visibleTitle(title: string): string {
  return title?.trim() || '新任务'
}

function toSessionMeta(session: {
  id: string
  title: string
  messages: Message[]
  createdAt: number
  updatedAt: number
}): SessionMeta {
  return {
    session_id: session.id,
    title: visibleTitle(session.title),
    message_count: session.messages.length,
    created_at: new Date(session.createdAt).toISOString(),
    updated_at: new Date(session.updatedAt).toISOString(),
    context_summary: null,
  }
}

function AgentSidebar({
  open,
  onClose,
  onNewTask,
  budgetStatus,
  budgetBlocked,
}: {
  open: boolean
  onClose: () => void
  onNewTask: () => void
  budgetStatus: BudgetStatus | null
  budgetBlocked: boolean
}) {
  const router = useRouter()
  const pathname = usePathname()
  const sessionsVersion = useChatStore((s) => s.sessionsVersion)
  const currentId = useChatStore((s) => s.currentId)
  const localSessions = useChatStore((s) => s.sessions)
  const historyError = useChatStore((s) => s.historyError)
  const [keyword, setKeyword] = useState('')
  const [history, setHistory] = useState<SessionMeta[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [loadingMore, setLoadingMore] = useState(false)
  const [actionSession, setActionSession] = useState<string | null>(null)
  const [navExpanded, setNavExpanded] = useState(true)
  const [userInfo, setUserInfo] = useState<ReturnType<typeof getCachedUser>>(null)
  const loadVersion = useRef(0)
  const loadMoreRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    setUserInfo(getCachedUser())
  }, [])

  const refresh = useCallback(async (force: boolean) => {
    const version = ++loadVersion.current
    if (force) setRefreshing(true)
    try {
      const [page, longTasks] = await Promise.all([getSessionPageCached(force), listAgentTasks()])
      if (version !== loadVersion.current) return
      const taskSessions = new Map<string, SessionMeta>()
      for (const task of longTasks.tasks) {
        if (!taskSessions.has(task.session_id)) taskSessions.set(task.session_id, {
          session_id: task.session_id, title: task.question.slice(0, 30), message_count: 2,
          created_at: task.created_at, updated_at: task.updated_at, context_summary: null,
        })
      }
      const merged = [...page.sessions]
      for (const taskSession of taskSessions.values()) {
        const index = merged.findIndex((item) => item.session_id === taskSession.session_id)
        if (index < 0) merged.push(taskSession)
        else if ((taskSession.updated_at || '') > (merged[index].updated_at || '')) merged[index] = { ...merged[index], updated_at: taskSession.updated_at }
      }
      setHistory(merged.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')))
      setHasMore(page.has_more)
      setLoadError('')
    } catch (error) {
      if (version === loadVersion.current) {
        setLoadError(error instanceof Error ? error.message : '历史任务加载失败')
      }
    } finally {
      if (version === loadVersion.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => { void refresh(false) }, [refresh])
  useEffect(() => {
    if (!sessionsVersion) return
    const timer = setTimeout(() => void refresh(true), 1200)
    return () => clearTimeout(timer)
  }, [sessionsVersion, refresh])

  const loadMore = useCallback(async () => {
    if (!hasMore || loadingMore) return
    const cursor = history[history.length - 1]
    if (!cursor?.updated_at) {
      setHasMore(false)
      return
    }
    setLoadingMore(true)
    try {
      const page = await listSessionsPage(
        SESSION_PAGE_SIZE,
        cursor.updated_at,
        cursor.session_id,
      )
      setHistory((current) => {
        const known = new Set(current.map((item) => item.session_id))
        return [...current, ...page.sessions.filter((item) => !known.has(item.session_id))]
      })
      setHasMore(page.has_more)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '加载更早任务失败')
    } finally {
      setLoadingMore(false)
    }
  }, [hasMore, history, loadingMore])

  useEffect(() => {
    const target = loadMoreRef.current
    if (!target || !hasMore || loading || loadingMore || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void loadMore()
    }, { rootMargin: '100px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [hasMore, loading, loadingMore, loadMore])

  const localCurrent = useMemo(() => {
    const session = localSessions.find((item) => item.id === currentId)
    return session?.messages.length ? toSessionMeta(session) : null
  }, [localSessions, currentId])

  const mergedHistory = useMemo(() => {
    if (!localCurrent) return history
    const exists = history.some((item) => item.session_id === localCurrent.session_id)
    if (!exists) return [localCurrent, ...history]
    return history.map((item) => item.session_id === localCurrent.session_id ? localCurrent : item)
  }, [history, localCurrent])

  const groups = useMemo(
    () => groupByTime(filterByKeyword(mergedHistory, keyword)),
    [mergedHistory, keyword],
  )

  const selectSession = (sessionId: string) => {
    if (sessionId !== currentId) router.push(`/agent?session=${encodeURIComponent(sessionId)}`)
    onClose()
  }

  const rename = async (item: SessionMeta) => {
    const nextTitle = window.prompt('重命名任务', item.title)?.trim()
    if (!nextTitle || nextTitle === item.title) return
    try {
      await renameMemorySession(item.session_id, nextTitle)
      useChatStore.getState().renameSession(item.session_id, nextTitle)
      invalidateSessionsCache()
      await refresh(true)
    } catch (error) {
      window.alert(`重命名失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  const remove = async (item: SessionMeta) => {
    if (!window.confirm(`删除“${item.title}”？删除后不可恢复。`)) return
    try {
      await deleteMemorySession(item.session_id)
      useChatStore.getState().deleteSession(item.session_id)
      invalidateSessionsCache()
      if (item.session_id === currentId) router.push('/agent')
      await refresh(true)
    } catch (error) {
      window.alert(`删除失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  return (
    <>
      {open && <button className="fixed inset-0 z-30 bg-slate-950/25 md:hidden" onClick={onClose} aria-label="关闭任务栏" />}
      <aside className={`fixed inset-y-0 left-0 z-40 flex w-[258px] shrink-0 flex-col border-r border-slate-200 bg-white/95 shadow-xl backdrop-blur-xl transition-transform duration-200 motion-reduce:transition-none md:relative md:z-auto md:shadow-none ${open ? 'translate-x-0 md:flex' : '-translate-x-full md:hidden'}`}>
        <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-slate-100 px-4">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600"><Brain size={17} /></span>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-900">Agent AI</span>
          <button type="button" onClick={() => void refresh(true)} aria-label="刷新任务列表" className={`rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700 ${refreshing ? 'motion-safe:animate-spin' : ''}`}><RefreshCw size={14} /></button>
          <button type="button" onClick={onClose} aria-label="收起任务栏" className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"><PanelLeftClose size={15} /></button>
        </div>

        <div className="shrink-0 space-y-3 p-3">
          <label className="relative block">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="搜索任务…" aria-label="搜索任务" className="w-full rounded-xl border border-slate-200 bg-slate-50 py-2 pl-9 pr-3 text-xs text-slate-800 outline-none transition focus:border-indigo-300 focus:bg-white focus:ring-2 focus:ring-indigo-100" />
          </label>
          <button type="button" onClick={onNewTask} className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-[13px] font-medium text-slate-800 shadow-sm transition hover:border-indigo-200 hover:bg-indigo-50/50 active:scale-[.99]"><Plus size={15} />新建任务</button>
        </div>

        <div className="shrink-0 px-3 pb-2">
          <button type="button" onClick={() => setNavExpanded((value) => !value)} aria-expanded={navExpanded} className="flex w-full items-center justify-between rounded-lg px-2 py-2 text-left text-xs font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800">
            <span>全部功能</span><ChevronDown size={14} className={`transition-transform ${navExpanded ? 'rotate-180' : ''}`} />
          </button>
          {navExpanded && <nav className="mt-1 space-y-1">
            {NAV.filter((entry) => entry.path === '/agent').map((entry) => (
              <Link key={entry.path || entry.label} href={entry.path || '/agent'} onClick={onClose} className={`flex items-center gap-2.5 rounded-xl px-3 py-2 text-[13px] transition ${pathname === entry.path ? 'bg-indigo-50 font-medium text-indigo-700' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'}`}>
                <span className="flex h-5 w-5 items-center justify-center">{entry.icon}</span><span>{entry.label}</span>
              </Link>
            ))}
          </nav>}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
          <div className="px-2 pb-2 pt-3 text-[10px] font-semibold uppercase tracking-[.12em] text-slate-400">最近任务</div>
          {loading ? <p className="px-2 py-5 text-center text-xs text-slate-400">正在读取历史…</p> : null}
          {historyError || loadError ? <p className="px-2 py-2 text-[11px] leading-5 text-rose-600">历史加载失败：{historyError || loadError}</p> : null}
          {!loading && groups.length === 0 && !historyError && !loadError ? (
            <div className="px-2 py-8 text-center text-xs leading-5 text-slate-400">{keyword ? '没有匹配的任务' : '还没有历史任务'}</div>
          ) : null}
          {groups.map((group) => <section key={group.bucket} className="mb-3">
            <h2 className="px-2 pb-1.5 pt-2 text-[10px] font-medium text-slate-400">{BUCKET_LABELS[group.bucket]}</h2>
            <div className="space-y-1">
              {group.items.map((item) => (
                <div key={item.session_id} className={`group relative flex items-center rounded-xl transition ${item.session_id === currentId ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50'}`}>
                  <button type="button" onClick={() => selectSession(item.session_id)} title={item.title} className="min-w-0 flex-1 px-2.5 py-2 text-left">
                    <span className="flex items-center gap-2"><MessageSquareText size={13} className="shrink-0 opacity-60" /><span className="truncate text-xs font-medium">{visibleTitle(item.title)}</span></span>
                    <span className="mt-1 block pl-5 text-[10px] text-slate-400">{formatTime(item.updated_at)}</span>
                  </button>
                  <button type="button" onClick={() => setActionSession((value) => value === item.session_id ? null : item.session_id)} aria-label="任务操作" className="mr-1 rounded-lg p-1.5 text-slate-400 opacity-0 transition hover:bg-white hover:text-slate-700 group-hover:opacity-100 focus:opacity-100"><MoreHorizontal size={15} /></button>
                  {actionSession === item.session_id && <div className="absolute right-1 top-9 z-20 w-32 rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg">
                    <button type="button" onClick={() => { setActionSession(null); void rename(item) }} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-slate-700 hover:bg-slate-50"><FileText size={13} />重命名</button>
                    <button type="button" onClick={() => { setActionSession(null); void remove(item) }} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-rose-600 hover:bg-rose-50"><Trash2 size={13} />删除</button>
                  </div>}
                </div>
              ))}
            </div>
          </section>)}
          {hasMore && <div ref={loadMoreRef} className="px-2 py-3 text-center text-[10px] text-slate-400">{loadingMore ? '正在加载更早任务…' : <button type="button" onClick={() => void loadMore()} className="hover:text-indigo-600">加载更早任务</button>}</div>}
        </div>

        <div className="shrink-0 border-t border-slate-100 p-3"><BudgetSummary status={budgetStatus} blocked={budgetBlocked} /></div>
        <div className="flex shrink-0 items-center gap-2.5 border-t border-slate-100 px-4 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-white shadow-sm"><Brain size={16} /></span>
          <span className="min-w-0"><span className="block truncate text-xs font-medium text-slate-700">{userInfo?.username || '未登录'}</span><span className="block truncate text-[10px] text-slate-400">{userInfo?.phone || '未绑定手机号'}</span></span>
        </div>
      </aside>
    </>
  )
}

function remainingBalance(status: BudgetStatus | null): number | null {
  if (!status) return null
  const userDaily = status.user?.daily ?? status.daily
  const userMonthly = status.user?.monthly ?? status.monthly
  const daily = Number(userDaily.limit) - Number(userDaily.used) - Number(userDaily.reserved)
  const monthly = Number(userMonthly.limit) - Number(userMonthly.used) - Number(userMonthly.reserved)
  if (!Number.isFinite(daily) || !Number.isFinite(monthly)) return null
  return Math.max(0, Math.min(daily, monthly))
}

function BudgetSummary({ status, blocked }: { status: BudgetStatus | null; blocked: boolean }) {
  const remaining = remainingBalance(status)
  const ratio = Math.max(status?.user?.daily.ratio ?? status?.daily.ratio ?? 0, status?.user?.monthly.ratio ?? status?.monthly.ratio ?? 0)
  return (
    <div className={`rounded-2xl border px-3.5 py-3 ${blocked ? 'border-amber-200 bg-amber-50/90' : 'border-slate-200 bg-white/80'}`}>
      <div className="flex items-center gap-2">
        {blocked ? <ShieldAlert size={14} className="text-amber-600" /> : <Sparkles size={14} className="text-indigo-500" />}
        <span className="flex-1 text-[11px] font-medium text-slate-700">个人额度</span>
        <span className="text-[10px] tabular-nums text-slate-500">{Math.round(ratio * 100)}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full transition-[width] duration-300 ${blocked ? 'bg-amber-500' : 'bg-indigo-500'}`} style={{ width: `${Math.min(100, Math.max(0, ratio * 100))}%` }} /></div>
      <p className="mt-2 text-[10px] leading-4 text-slate-500">{remaining == null ? '正在读取额度…' : blocked ? '额度暂时不足；补足后可重新发送暂停的问题。' : `日/月额度可用约 ${formatCny(remaining)}`}</p>
    </div>
  )
}

type ProcessStep = {
  key: string
  title: string
  detail?: string
  state: 'running' | 'success' | 'warning' | 'error' | 'cancelled'
}

function processSteps(events: SSEStreamEvent[], labels: Record<string, string>, streaming: boolean): ProcessStep[] {
  const steps: ProcessStep[] = []
  const statusRows = new Map<string, number>()
  const toolRows = new Map<string, number>()
  const contextRows = new Map<string, number[]>()
  const pendingContextRow = (level: string) => {
    const rows = contextRows.get(level)
    return rows?.shift()
  }
  events.forEach((event, index) => {
    if (event.event === 'status') {
      const data = event.data as StatusEvent
      const key = data.execution_id || `${data.node}-${index}`
      const state: ProcessStep['state'] = data.phase === 'failed' || data.status === 'error'
        ? 'error'
        : data.phase === 'cancelled' || data.status === 'cancelled'
          ? 'cancelled'
          : data.phase === 'started' || data.status === 'running'
            ? 'running'
            : 'success'
      const detail = data.duration_ms != null ? `${(data.duration_ms / 1000).toFixed(1)} 秒` : undefined
      const existing = statusRows.get(key)
      if (existing != null) {
        steps[existing] = { ...steps[existing], state, detail: detail || steps[existing].detail }
      } else {
        statusRows.set(key, steps.length)
        steps.push({ key: `status-${key}`, title: labels[data.node] || data.node, detail, state })
      }
    } else if (event.event === 'log') {
      const payload = event.data.payload || {}
      const phase = typeof payload.phase === 'string' ? payload.phase : ''
      const tool = typeof payload.tool === 'string' ? payload.tool : ''
      const toolKey = tool || event.data.node
      if (phase === 'tool_start') {
        const row = steps.length
        toolRows.set(toolKey, row)
        steps.push({ key: `tool-${toolKey}-${index}`, title: `调用 ${tool || event.data.node}`, detail: event.data.message, state: 'running' })
      } else {
        const state: ProcessStep['state'] = event.data.level === 'error' ? 'error' : event.data.level === 'warn' ? 'warning' : 'success'
        steps.push({ key: `log-${event.data.step_id}-${index}`, title: event.data.message, detail: event.data.node === 'request_entry' ? '请求准备' : labels[event.data.node] || event.data.node, state })
      }
    } else if (event.event === 'file') {
      steps.push({ key: `file-${event.data.step_id}-${index}`, title: '生成文件', detail: event.data.files.join('、'), state: 'success' })
    } else if (event.event === 'context') {
      const level = event.data.level
      const action = event.data.action
      if (action === 'summary_started') {
        const row = steps.length
        contextRows.set(level, [...(contextRows.get(level) ?? []), row])
        steps.push({
          key: `context-${level}-${index}`,
          title: '正在整理较早对话',
          state: 'running',
        })
      } else if (action === 'summary_deferred') {
        const row = pendingContextRow(level)
        if (row != null) {
          const detail = event.data.reason === 'saved_for_next_turn'
            ? '摘要已保存，后续对话会使用'
            : '已采用安全裁剪继续处理'
          steps[row] = {
            ...steps[row], title: '对话上下文已安全处理', detail, state: 'success',
          }
        }
      } else if (action === 'auto_compact' || event.data.saved_tokens > 0) {
        const row = action === 'auto_compact' ? pendingContextRow(level) : undefined
        const title = '已整理较早对话'
        const detail = event.data.saved_tokens > 0
          ? `节省 ${event.data.saved_tokens.toLocaleString()} tokens`
          : action === 'auto_compact' ? '摘要已用于本轮' : undefined
        if (row != null) {
          steps[row] = { ...steps[row], title, detail, state: 'success' }
        } else {
          steps.push({ key: `context-${index}`, title, detail, state: 'success' })
        }
      }
    }
  })
  if (!streaming) {
    for (const row of toolRows.values()) {
      if (steps[row]?.state === 'running') steps[row] = { ...steps[row], state: 'success', detail: '已完成' }
    }
    for (const rows of contextRows.values()) {
      for (const row of rows) {
        if (steps[row]?.state === 'running') {
          steps[row] = {
            ...steps[row], title: '上下文整理状态未确认',
            state: 'warning',
          }
        }
      }
    }
  }
  return steps.slice(-18)
}

function StepIcon({ state }: { state: ProcessStep['state'] }) {
  if (state === 'running') return <LoaderCircle size={13} className="motion-safe:animate-spin text-indigo-500" />
  if (state === 'success') return <CheckCircle2 size={13} className="text-emerald-500" />
  if (state === 'warning') return <TriangleAlert size={13} className="text-amber-500" />
  if (state === 'error') return <AlertCircle size={13} className="text-rose-500" />
  return <Circle size={12} className="text-slate-400" />
}

function ProcessPanel({
  events,
  labels,
  todos,
  streaming,
}: {
  events: SSEStreamEvent[]
  labels: Record<string, string>
  todos: TodoItem[]
  streaming: boolean
}) {
  const [expanded, setExpanded] = useState(streaming)
  const steps = useMemo(() => processSteps(events, labels, streaming), [events, labels, streaming])
  useEffect(() => {
    setExpanded(streaming)
  }, [streaming])
  if (!steps.length && !todos.length && !streaming) return null
  return (
    <section className="mb-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/80">
      <button type="button" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded} className="flex w-full items-center gap-2 px-3 py-2.5 text-left">
        {streaming ? <LoaderCircle size={14} className="motion-safe:animate-spin text-indigo-500" /> : <CheckCircle2 size={14} className="text-emerald-500" />}
        <span className="flex-1 text-[11px] font-medium text-slate-700">{streaming ? '执行过程' : '本轮过程'}{steps.length ? ` · ${steps.length} 步` : ''}</span>
        <ChevronDown size={13} className={`text-slate-400 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && <div className="space-y-1 border-t border-slate-200/80 px-3 py-2.5">
        {steps.map((step) => <div key={step.key} className="flex items-start gap-2 rounded-lg px-1 py-1">
          <span className="mt-0.5 shrink-0"><StepIcon state={step.state} /></span>
          <span className="min-w-0 flex-1 text-[11px] leading-4 text-slate-700"><span>{step.title}</span>{step.detail && <span className="ml-2 text-slate-400">{step.detail}</span>}</span>
        </div>)}
        {todos.length > 0 && <div className="mt-2 space-y-1 border-t border-slate-200/80 pt-2">
          {todos.map((todo) => <div key={todo.id} className="flex items-start gap-2 px-1 py-0.5 text-[11px] leading-4 text-slate-600">
            {todo.status === 'completed' ? <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-500" /> : todo.status === 'in_progress' ? <LoaderCircle size={13} className="mt-0.5 shrink-0 motion-safe:animate-spin text-indigo-500" /> : <Circle size={13} className="mt-0.5 shrink-0 text-slate-300" />}
            <span>{todo.text}</span>
          </div>)}
        </div>}
        {streaming && !steps.length && !todos.length && <div className="flex items-center gap-1.5 px-1 py-1 text-[11px] text-slate-500"><span className="flex gap-1"><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse" /><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse [animation-delay:150ms]" /><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse [animation-delay:300ms]" /></span>正在处理</div>}
      </div>}
    </section>
  )
}

function RichAnswer({
  content,
  hasStructuredSources = false,
}: {
  content: string
  hasStructuredSources?: boolean
}) {
  if (!content) return null
  const displayContent = presentAssistantAnswer(content, hasStructuredSources)
  return <div className="break-words text-[14px] leading-7 text-slate-800 [&_a]:text-indigo-600 [&_a]:underline [&_a]:underline-offset-2 [&_blockquote]:my-3 [&_blockquote]:border-l-2 [&_blockquote]:border-indigo-200 [&_blockquote]:pl-3 [&_blockquote]:text-slate-600 [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1 [&_code]:py-0.5 [&_h1]:mb-2 [&_h1]:mt-4 [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-1 [&_h3]:mt-3 [&_h3]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_ol_li]:list-decimal [&_p]:my-2 [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-slate-900 [&_pre]:p-3 [&_pre]:text-xs [&_pre_code]:bg-transparent [&_table]:my-3 [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto [&_td]:border [&_td]:border-slate-200 [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-50 [&_th]:px-2 [&_th]:py-1">
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} rehypePlugins={[rehypeHighlight]}>{displayContent}</ReactMarkdown>
  </div>
}

function PauseNotice({
  message,
  disabled,
  onRetry,
}: {
  message: string
  disabled: boolean
  onRetry: () => void
}) {
  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-3">
      <div className="flex items-start gap-2.5">
        <ShieldAlert size={16} className="mt-0.5 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-amber-950">本轮已暂停</p>
          <p className="mt-1 text-xs leading-5 text-amber-900">{message || PAUSE_MESSAGE}</p>
          <button type="button" disabled={disabled} onClick={onRetry} className="mt-2.5 inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-45">
            补足余额后重新发送 <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </div>
  )
}

function LongTaskReply({ taskId, blocked }: { taskId: string; blocked: boolean }) {
  const { task, error, operation, control } = useAgentTask(taskId)
  const labels = task?.node_labels || {}
  const events: SSEStreamEvent[] = (task?.steps || []).map((step) => ({
    event: 'log', data: { level: 'info', node: step.node, step_id: '',
      message: `${labels[step.node] || step.node}已完成`, payload: {}, ts: new Date(step.at).getTime() / 1000 },
  }))
  const statusText = { PENDING: '排队中', RUNNING: '执行中', PAUSED: '已暂停', WAITING_USER: '等待补充信息', SUCCESS: '已完成', FAILED: '执行失败', CANCELLED: '已结束' }
  return <div className="space-y-3" aria-label="长任务进度">
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span className="font-medium text-indigo-700">长任务 · {error ? '状态暂不可用' : task ? statusText[task.status] : '正在读取进度'}</span>
      <div className="flex gap-2">
        {task && ['PENDING', 'RUNNING'].includes(task.status) && <button type="button" disabled={!!error || !!operation} onClick={() => void control('pause')} className="rounded-lg border border-slate-200 px-3 py-1.5 text-slate-700 disabled:opacity-50">{operation === 'pause' ? '正在暂停…' : '暂停任务'}</button>}
        {task?.status === 'PAUSED' && <button type="button" disabled={!!error || !!operation || (blocked && task.error_type === 'user_balance_insufficient')} onClick={() => void control('resume')} className="rounded-lg border border-indigo-200 px-3 py-1.5 text-indigo-700 disabled:opacity-50">{operation === 'resume' ? '正在继续…' : '继续任务'}</button>}
      </div>
    </div>
    <p className="text-xs leading-5 text-slate-500">{operation === 'pause' && task?.status !== 'PAUSED' ? '正在保存当前步骤，完成后暂停；已完成的步骤会保留。' : task?.progress || '任务可在后台执行，离开页面后可从任务历史返回。'}</p>
    <ProcessPanel events={events} labels={labels} todos={[]} streaming={task?.status === 'RUNNING'} />
    {!!task?.result?.answer && <RichAnswer content={task.result.answer} />}
    {task?.status === 'PAUSED' && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">{task.error_type === 'user_balance_insufficient' ? '个人额度不足，补足后点击继续任务。' : '进度已保存，点击继续任务可接着执行。'}发送新文字会结束此任务并开始新请求。</p>}
    {task?.status === 'FAILED' && <p className="text-xs text-rose-700">{task.error_message || '任务未完成，请查看执行记录。'}</p>}
    {!!error && <p role="alert" className="text-xs text-rose-700">{error}</p>}
  </div>
}

function AssistantMessage({
  message,
  active,
  deltaText,
  labels,
  todos,
  blocked,
  error,
  onRetry,
}: {
  message: Message
  active: boolean
  deltaText: string
  labels: Record<string, string>
  todos: TodoItem[]
  blocked: boolean
  error: unknown
  onRetry: () => void
}) {
  const events = message.trace?.streamEvents ?? message.streamEvents ?? []
  const answer = active ? deltaText : message.content
  const errorMessage = error instanceof Error ? error.message : ''
  const sources = message.sources || []
  return (
    <article className="flex gap-3 sm:gap-3.5">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-indigo-600"><Brain size={16} /></span>
      <div className="min-w-0 flex-1">
        <div className="mb-1.5 flex items-center gap-2"><span className="text-xs font-semibold text-slate-800">Agent AI</span><span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">企业助手</span></div>
        <div className="rounded-2xl rounded-tl-md border border-slate-200 bg-white px-4 py-3 shadow-[0_1px_3px_rgba(15,23,42,.035)] sm:px-5">
          {message.taskId ? <LongTaskReply taskId={message.taskId} blocked={blocked} /> : <>
          <ProcessPanel events={events} labels={labels} todos={active ? todos : message.trace?.todoItems || []} streaming={active} />
          {active && !answer ? <div className="flex items-center gap-2 py-1 text-xs text-slate-500"><span className="flex gap-1"><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse" /><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse [animation-delay:150ms]" /><i className="h-1.5 w-1.5 rounded-full bg-indigo-400 motion-safe:animate-pulse [animation-delay:300ms]" /></span>正在处理请求…</div> : <RichAnswer content={answer} hasStructuredSources={sources.length > 0} />}
          {message.pause && <PauseNotice message={message.pause.message} disabled={blocked} onRetry={onRetry} />}
          {errorMessage && !message.pause && <div className="mt-3 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-800"><AlertCircle size={14} className="mt-0.5 shrink-0" />{errorMessage}</div>}
          {sources.length > 0 && <div className="mt-4 border-t border-slate-100 pt-3"><p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-slate-400">参考来源</p><div className="flex flex-wrap gap-1.5">{sources.map((source, index) => <span key={`${source.filename}-${index}`} title={source.section || source.filename} className="inline-flex max-w-full items-center gap-1.5 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[10px] text-slate-600"><FileText size={12} className="shrink-0 text-indigo-400" /><span className="truncate">{source.filename}</span></span>)}</div></div>}
          {message.usage && <div className="mt-3 flex items-center gap-1.5 text-[10px] text-slate-400"><Clock3 size={11} />{message.trace?.elapsed ? `${message.trace.elapsed.toFixed(1)} 秒 · ` : ''}{message.usage.total_tokens.toLocaleString()} tokens{message.usage.calls != null ? ` · ${message.usage.calls} 次模型调用` : ''}</div>}
          </>}
        </div>
      </div>
    </article>
  )
}

function Composer({
  onSend,
  onStop,
  loading,
  blocked,
  status,
}: {
  onSend: (text: string) => void
  onStop: () => void
  loading: boolean
  blocked: boolean
  status: BudgetStatus | null
}) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`
  }, [input])

  const send = () => {
    const text = input.trim()
    if (!text || loading || blocked || chatInputOverLimit(text)) return
    setInput('')
    onSend(text)
  }
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      send()
    }
  }
  const cannotSend = blocked || chatInputOverLimit(input)
  const balance = remainingBalance(status)

  return <footer className="shrink-0 border-t border-slate-200/80 bg-white/75 px-3 pb-[max(12px,env(safe-area-inset-bottom))] pt-3 backdrop-blur-xl sm:px-6 sm:pt-4">
    <div className="mx-auto max-w-4xl">
      {blocked && <div className="mb-2 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-4 text-amber-900"><ShieldAlert size={13} className="shrink-0" />个人额度暂不可用；补足额度后可重新发送暂停的问题。{balance != null && <span className="ml-auto tabular-nums">可用 {formatCny(balance)}</span>}</div>}
      <div className="rounded-2xl border border-slate-200 bg-white px-3.5 pb-2.5 pt-3 shadow-[0_4px_18px_rgba(15,23,42,.045)] transition focus-within:border-indigo-300 focus-within:shadow-[0_5px_24px_rgba(79,70,229,.11)] sm:px-4">
        <textarea ref={textareaRef} value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={onKeyDown} rows={1} disabled={loading || blocked} placeholder="描述你要处理的任务…" aria-label="输入给企业助手的内容" className="max-h-[200px] min-h-[54px] w-full resize-none bg-transparent text-sm leading-6 text-slate-800 outline-none placeholder:text-slate-400 disabled:opacity-50" />
        <div className="flex items-center justify-between gap-3 pt-1.5">
          <div className="min-w-0 text-[10px] text-slate-400">{chatInputShowCounter(input) ? <span className={chatInputOverLimit(input) ? 'text-rose-600' : ''}>{input.length.toLocaleString()} / {CHAT_INPUT_MAX_CHARS.toLocaleString()}{chatInputOverLimit(input) ? ' · 输入过长，请精简内容' : ''}</span> : <span className="hidden sm:inline">Enter 发送 · Shift + Enter 换行</span>}</div>
          <button type="button" onClick={loading ? onStop : send} disabled={loading ? false : !input.trim() || cannotSend} aria-label={loading ? '停止生成' : '发送'} title={loading ? '停止生成' : '发送'} className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white shadow-sm transition active:scale-95 disabled:cursor-not-allowed disabled:opacity-35 ${loading ? 'bg-slate-800 hover:bg-slate-700' : 'bg-indigo-600 hover:bg-indigo-700'}`}>
            {loading ? <StopCircle size={17} /> : <ArrowUp size={17} strokeWidth={2.5} />}
          </button>
        </div>
      </div>
      <p className="mt-2 text-center text-[10px] text-slate-400">Agent AI 生成的内容请核实关键信息</p>
    </div>
  </footer>
}

export default function AgentWorkspace() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const sessionParam = searchParams.get('session')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [budgetBlocked, setBudgetBlocked] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)

  const sessions = useChatStore((s) => s.sessions)
  const currentId = useChatStore((s) => s.currentId)
  const currentSession = useMemo(() => sessions.find((session) => session.id === currentId), [sessions, currentId])
  const messages = currentSession?.messages ?? []
  const loading = useChatStore((s) => s.isLoading)
  const error = useChatStore((s) => s.error)
  const deltaText = useChatStore((s) => s.deltaText)
  const nodeLabels = useChatStore((s) => s.nodeLabels)
  const todoItems = useChatStore((s) => s.todoItems)
  const clarification = useChatStore((s) => s.clarification)
  const { status: budgetStatus }: { status: BudgetStatus | null } = useBudgetStatus(setBudgetBlocked)
  const { send: sendMessage, stopStream } = useSendMessage()
  const send = useCallback(async (text: string) => {
    router.replace(`/agent?session=${encodeURIComponent(currentId)}`)
    await sendMessage(text, null)
  }, [currentId, router, sendMessage])
  const { regenerate } = useSSE()
  useAbortStreamOnUnmount()

  useEffect(() => {
    if (sessionParam) {
      useChatStore.getState().switchSession(sessionParam)
      const session = useChatStore.getState().sessions.find((item) => item.id === sessionParam)
      if (!session?.messages.length) {
        void useChatStore.getState().loadHistory(sessionParam).then(() => restoreAgentTasks(sessionParam)).catch((err) => {
          if (useChatStore.getState().currentId === sessionParam) useChatStore.getState().setHistoryError(err instanceof Error ? err.message : '恢复长任务失败')
        })
      }
    }
  }, [sessionParam])

  useEffect(() => {
    if (window.matchMedia('(min-width: 768px)').matches) setSidebarOpen(true)
  }, [])
  useEffect(() => {
    if (sidebarOpen && window.matchMedia('(max-width: 767px)').matches) setSidebarOpen(false)
  }, [currentId])
  useEffect(() => {
    const target = scrollRef.current
    if (!target || !autoScrollRef.current) return
    target.scrollTo({ top: target.scrollHeight, behavior: 'smooth' })
  }, [messages, deltaText, loading])

  const newTask = () => {
    useChatStore.getState().newSession()
    router.push('/agent')
  }
  const title = currentSession?.title || '新任务'
  const activeIndex = messages.length - 1

  return <main className="relative flex min-h-0 flex-1 bg-[#fafbfc] text-[#1a1a2e]">
    <AgentSidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} onNewTask={newTask} budgetStatus={budgetStatus} budgetBlocked={budgetBlocked} />
    <section className="flex min-w-0 flex-1 flex-col">
      <header className="z-10 flex h-14 shrink-0 items-center gap-3 border-b border-slate-200/80 bg-white/80 px-3 backdrop-blur-xl sm:px-5">
        <button type="button" onClick={() => setSidebarOpen((value) => !value)} aria-label={sidebarOpen ? '收起任务栏' : '展开任务栏'} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-slate-500 transition hover:bg-slate-100 hover:text-slate-900">
          {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
        </button>
        <div className="min-w-0 flex-1"><h1 className="truncate text-sm font-semibold text-slate-900">{visibleTitle(title)}</h1><p className="mt-0.5 hidden text-[10px] text-slate-400 sm:block">企业助手 · 可调用已授权的业务能力</p></div>
        <button type="button" onClick={newTask} aria-label="新建任务" className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-500 shadow-sm transition hover:border-indigo-200 hover:text-indigo-600 sm:flex"><Plus size={16} /></button>
      </header>

      <div ref={scrollRef} onScroll={(event) => {
        const element = event.currentTarget
        autoScrollRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80
      }} className="min-h-0 flex-1 overflow-y-auto px-3 py-5 sm:px-6 sm:py-7">
        <div className="mx-auto max-w-4xl">
          {messages.length === 0 ? <div className="flex min-h-[min(64vh,650px)] flex-col items-center justify-center py-8 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl border border-indigo-100 bg-white text-indigo-600 shadow-sm"><Sparkles size={23} /></span>
            <h2 className="mt-5 text-xl font-semibold tracking-tight text-slate-900 sm:text-2xl">今天要处理什么任务？</h2>
            <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">说明目标和约束，企业助手会按实际执行过程逐步展示结果。</p>
            <div className="mt-6 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
              {['查询本月销售额并整理主要变化', '查找知识库中关于退款的处理规则'].map((example) => <button key={example} type="button" onClick={() => void send(example)} className="rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-left text-xs leading-5 text-slate-600 shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:text-indigo-700 motion-reduce:transform-none">{example}</button>)}
            </div>
          </div> : <div className="space-y-6 pb-6">
            {messages.map((message, index) => message.role === 'user' ? <article key={message.id} className="flex justify-end">
              <div className="max-w-[88%] rounded-2xl rounded-br-md border border-indigo-100 bg-indigo-50/80 px-4 py-3 text-sm leading-6 text-slate-800 sm:max-w-[75%] sm:px-5">{message.content}</div>
            </article> : <AssistantMessage
              key={message.id}
              message={message}
              active={loading && index === activeIndex}
              deltaText={deltaText}
              labels={nodeLabels}
              todos={todoItems}
              blocked={budgetBlocked}
              error={index === activeIndex ? error : null}
              onRetry={() => void regenerate(currentId, null)}
            />)}
            {clarification && <section className="ml-11 rounded-xl border border-indigo-100 bg-indigo-50/60 p-3.5 sm:ml-12">
              <p className="text-xs font-medium leading-5 text-slate-800">{clarification.question}</p>
              <div className="mt-2.5 flex flex-wrap gap-2">{clarification.options.map((option) => <button key={option.id} type="button" disabled={loading || budgetBlocked} onClick={() => void send(option.label)} className="rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">{option.label}</button>)}</div>
            </section>}
          </div>}
          <div className="h-1" />
        </div>
      </div>
      <Composer onSend={(text) => void send(text)} onStop={() => void stopStream()} loading={loading} blocked={budgetBlocked} status={budgetStatus} />
    </section>
  </main>
}
