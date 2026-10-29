'use client'

import { useEffect, useState } from 'react'
import { Clock, RefreshCw, Check, X, Play, Zap, Power } from 'lucide-react'
import { clsx } from 'clsx'
import { fetchRaw } from '@/api/client'
import EmptyState from '@/components/shared/EmptyState'

interface Schedule {
  id: string
  workflow: string
  trigger: string
  description?: string
  next_run_time?: string | null
}

// 用户自动化（决策 #27）：跨租户总览 + 最近失败 + 停用开关
interface UserAutomation {
  id: string
  user_id: string
  tenant_id: string
  name: string
  trigger_type: string
  cron_expr: string
  enabled: boolean
  last_status: string
  last_run_at?: string | null
}

interface AutomationOverview {
  total: number
  enabled: number
  by_trigger: { cron: number; webhook: number }
  recent_failures: Array<{
    event_id: number
    automation_id: string
    automation_name: string
    tenant_id: string
    source: string
    detail: string
    created_at: string | null
  }>
  automations: UserAutomation[]
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [editHour, setEditHour] = useState(9)
  const [editMinute, setEditMinute] = useState(0)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [overview, setOverview] = useState<AutomationOverview | null>(null)
  const [overviewLoading, setOverviewLoading] = useState(true)
  const [toggling, setToggling] = useState<string | null>(null)

  const WORKFLOW_LABELS: Record<string, string> = {
    daily_report: '经营日报',
    inventory_alert: '库存预警',
    weekly_eval: 'Golden 评测',
  }

  async function loadSchedules() {
    setLoading(true)
    try {
      const res = await fetchRaw('/api/schedules')
      const data = await res.json()
      setSchedules(data.schedules || [])
    } catch {
      setMsg({ type: 'error', text: '加载失败' })
    } finally {
      setLoading(false)
    }
  }

  async function loadOverview() {
    setOverviewLoading(true)
    try {
      const res = await fetchRaw('/api/admin/automations/overview')
      if (res.ok) setOverview(await res.json())
    } catch {
      // 总览加载失败不打断主页面（非管理员/旧后端时静默）
    } finally {
      setOverviewLoading(false)
    }
  }

  useEffect(() => { loadSchedules(); loadOverview() }, [])

  async function toggleAutomation(a: UserAutomation) {
    if (!confirm(`确认${a.enabled ? '停用' : '启用'}「${a.name}」？`)) return
    setToggling(a.id)
    try {
      const res = await fetchRaw(`/api/admin/automations/${a.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !a.enabled }),
      })
      if (!res.ok) throw new Error('操作失败')
      await loadOverview()
    } catch (e) {
      setMsg({ type: 'error', text: (e as Error).message })
    } finally {
      setToggling(null)
    }
  }

  function startEdit(s: Schedule) {
    const match = s.trigger.match(/daily (\d{2}):(\d{2})/)
    setEditHour(match ? parseInt(match[1]) : 9)
    setEditMinute(match ? parseInt(match[2]) : 0)
    setEditing(s.workflow)
  }

  async function saveEdit(workflow: string) {
    setSaving(true)
    setMsg(null)
    try {
      const res = await fetchRaw(`/api/schedules/${workflow}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hour: editHour, minute: editMinute }),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: '保存失败' }))
        throw new Error(err.detail || '保存失败')
      }
      setEditing(null)
      setMsg({ type: 'success', text: `已更新为 ${editHour.toString().padStart(2, '0')}:${editMinute.toString().padStart(2, '0')}` })
      await loadSchedules()
    } catch (e) {
      setMsg({ type: 'error', text: (e as Error).message })
    } finally {
      setSaving(false)
    }
  }

  // 立即运行（2026-08-11）
  async function runNow(workflow: string) {
    if (!confirm(`确认立即运行 ${workflow}？`)) return
    setRunning(workflow)
    setMsg(null)
    try {
      const res = await fetchRaw(`/api/schedules/${workflow}/run`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok || data.ok === false) {
        throw new Error(data.error || data.detail || '运行失败')
      }
      // weekly_eval 特殊：返回 summary
      if (workflow === 'weekly_eval' && data.summary) {
        const s = data.summary
        setMsg({
          type: 'success',
          text: `评测完成: top1=${(s.top1_accuracy * 100).toFixed(1)}% pass=${(s.pass_rate * 100).toFixed(1)}% rej=${(s.reject_accuracy * 100).toFixed(1)}%`,
        })
      } else {
        setMsg({ type: 'success', text: `${workflow} 已启动（job_id: ${data.job_id || '—'}）` })
      }
    } catch (e) {
      setMsg({ type: 'error', text: (e as Error).message })
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">定时任务配置</h1>
            <p className="text-xs text-text-muted mt-1">修改后即时生效，无需重启服务</p>
          </div>
          <button
            onClick={loadSchedules}
            className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            <RefreshCw size={14} /> 刷新
          </button>
        </div>

        {msg && (
          <div className={clsx(
            'mb-4 px-4 py-2.5 rounded-lg text-xs flex items-center gap-2',
            msg.type === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'
          )}>
            {msg.type === 'success' ? <Check size={14} /> : <X size={14} />}
            {msg.text}
          </div>
        )}

        {loading && <p className="text-xs text-text-muted py-4">加载中...</p>}

        <div className="space-y-3">
          {schedules.map(s => {
            const isEditing = editing === s.workflow
            const match = s.trigger.match(/daily (\d{2}):(\d{2})/)
            const time = match ? `${match[1]}:${match[2]}` : s.trigger

            return (
              <div key={s.id} className="bg-surface-base rounded-xl border border-border-subtle p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-9 h-9 rounded-lg bg-accent/5 flex items-center justify-center">
                      <Clock size={18} className="text-accent" />
                    </div>
                    <div>
                      <span className="text-sm text-text-primary">
                        {WORKFLOW_LABELS[s.workflow] || s.workflow}
                      </span>
                      <span className="text-[10px] text-text-muted ml-2">{s.description || ''}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {isEditing ? (
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0} max={23}
                          value={editHour}
                          onChange={e => setEditHour(Number(e.target.value))}
                          className="w-12 text-center text-sm border border-border-subtle rounded-md px-1 py-0.5 bg-white"
                        />
                        <span className="text-sm text-text-primary">:</span>
                        <input
                          type="number"
                          min={0} max={59}
                          value={editMinute}
                          onChange={e => setEditMinute(Number(e.target.value))}
                          className="w-12 text-center text-sm border border-border-subtle rounded-md px-1 py-0.5 bg-white"
                        />
                        <button
                          onClick={() => saveEdit(s.workflow)}
                          disabled={saving}
                          className="text-xs text-accent hover:underline disabled:opacity-50"
                        >
                          保存
                        </button>
                        <button
                          onClick={() => setEditing(null)}
                          className="text-xs text-text-muted hover:text-text-primary"
                        >
                          取消
                        </button>
                      </div>
                    ) : (
                      <>
                        <span className="text-sm text-text-secondary tabular-nums">{time}</span>
                        {s.next_run_time && (
                          <span className="text-[10px] text-text-muted">
                            下次: {new Date(s.next_run_time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                          </span>
                        )}
                        <button
                          onClick={() => runNow(s.workflow)}
                          disabled={running === s.workflow}
                          className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20 disabled:opacity-50"
                          title="立即运行"
                        >
                          <Play size={10} />
                          {running === s.workflow ? '运行中' : '立即运行'}
                        </button>
                        <button
                          onClick={() => startEdit(s)}
                          className="text-xs text-accent hover:underline"
                        >
                          编辑
                        </button>
                      </>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>

        {!loading && schedules.length === 0 && (
          <EmptyState
            title="定时任务"
            description="创建一个定时任务，让工作流按计划自动运行"
            actionHref="/"
            actionLabel="返回管理端首页"
          />
        )}

        {/* ── 用户自动化（决策 #27）：总览 + 最近失败 + 停用开关 ── */}
        <div className="mt-10">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-text-primary flex items-center gap-1.5">
                <Zap size={14} className="text-accent" /> 用户自动化
              </h2>
              <p className="text-[10px] text-text-muted mt-0.5">
                用户级 cron / Webhook 自动任务（复用既有执行链；写操作仍走确认门）
              </p>
            </div>
            <button
              onClick={loadOverview}
              className="flex items-center gap-1 text-[10px] text-text-muted hover:text-text-primary"
            >
              <RefreshCw size={11} /> 刷新
            </button>
          </div>

          {overviewLoading && <p className="text-xs text-text-muted py-2">加载中...</p>}

          {!overviewLoading && overview && (
            <>
              <div className="grid grid-cols-4 gap-2 mb-3">
                <div className="bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
                  <p className="text-[10px] text-text-muted">总数</p>
                  <p className="text-sm font-semibold text-text-primary tabular-nums">{overview.total}</p>
                </div>
                <div className="bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
                  <p className="text-[10px] text-text-muted">启用中</p>
                  <p className="text-sm font-semibold text-emerald-600 tabular-nums">{overview.enabled}</p>
                </div>
                <div className="bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
                  <p className="text-[10px] text-text-muted">cron / webhook</p>
                  <p className="text-sm font-semibold text-text-primary tabular-nums">
                    {overview.by_trigger.cron} / {overview.by_trigger.webhook}
                  </p>
                </div>
                <div className="bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
                  <p className="text-[10px] text-text-muted">最近失败</p>
                  <p className={clsx('text-sm font-semibold tabular-nums',
                    overview.recent_failures.length > 0 ? 'text-red-600' : 'text-text-secondary')}>
                    {overview.recent_failures.length}
                  </p>
                </div>
              </div>

              {overview.recent_failures.length > 0 && (
                <div className="bg-surface-base rounded-lg border border-border-subtle p-3 mb-3">
                  <p className="text-[10px] text-text-muted mb-1.5">最近失败事件</p>
                  <div className="space-y-1">
                    {overview.recent_failures.slice(0, 5).map(f => (
                      <div key={f.event_id} className="text-[10px] text-text-secondary flex items-start gap-2">
                        <span className="text-red-500 shrink-0">✕</span>
                        <span className="truncate" title={f.detail}>
                          {f.automation_name || f.automation_id}
                          <span className="text-text-muted">（{f.source}，{f.tenant_id}）</span>
                          {f.detail ? ` — ${f.detail}` : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="space-y-2">
                {overview.automations.map(a => (
                  <div key={a.id} className="bg-surface-base rounded-lg border border-border-subtle px-3 py-2 flex items-center justify-between">
                    <div className="min-w-0">
                      <p className="text-xs text-text-primary truncate">
                        {a.name}
                        <span className="text-[10px] text-text-muted ml-2">
                          {a.trigger_type === 'cron' ? `cron ${a.cron_expr}` : 'webhook'}
                          <span className="ml-1.5">· {a.tenant_id} / {a.user_id}</span>
                        </span>
                      </p>
                      <p className="text-[10px] text-text-muted">
                        最近: {a.last_status || '—'}
                        {a.last_run_at ? ` @ ${new Date(a.last_run_at).toLocaleString('zh-CN')}` : ''}
                      </p>
                    </div>
                    <button
                      onClick={() => toggleAutomation(a)}
                      disabled={toggling === a.id}
                      className={clsx(
                        'flex items-center gap-1 px-2 py-1 rounded-md text-[10px] shrink-0 disabled:opacity-50',
                        a.enabled
                          ? 'bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20'
                          : 'bg-zinc-100 text-zinc-500 hover:bg-zinc-200'
                      )}
                      title={a.enabled ? '停用该自动任务' : '启用该自动任务'}
                    >
                      <Power size={10} />
                      {a.enabled ? '启用中' : '已停用'}
                    </button>
                  </div>
                ))}
                {overview.automations.length === 0 && (
                  <p className="text-[10px] text-text-muted py-2">暂无用户自动化任务</p>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
