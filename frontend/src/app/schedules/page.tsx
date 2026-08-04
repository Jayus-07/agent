'use client'

import { useEffect, useState } from 'react'
import { Clock, RefreshCw, Check, X } from 'lucide-react'
import { clsx } from 'clsx'

interface Schedule {
  id: string
  workflow: string
  trigger: string
  description?: string
  next_run_time?: string | null
}

export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<string | null>(null)
  const [editHour, setEditHour] = useState(9)
  const [editMinute, setEditMinute] = useState(0)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const WORKFLOW_LABELS: Record<string, string> = {
    daily_report: '经营日报',
    inventory_alert: '库存预警',
  }

  async function loadSchedules() {
    setLoading(true)
    try {
      const res = await fetch('/api/schedules')
      const data = await res.json()
      setSchedules(data.schedules || [])
    } catch {
      setMsg({ type: 'error', text: '加载失败' })
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadSchedules() }, [])

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
      const res = await fetch(`/api/schedules/${workflow}`, {
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
          <p className="text-xs text-text-muted py-4">暂无定时任务</p>
        )}
      </div>
    </div>
  )
}
