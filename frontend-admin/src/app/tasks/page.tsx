'use client'

/**
 * /tasks — 任务中心（管理端）
 *
 * 数据源：/api/admin/tasks/*（管理员闸；403 时页面提示需要管理员）。
 * 结构：统计卡（24h）→ 筛选条 → 任务表 → 详情抽屉（时间线 + traceback +
 * checkpoints 历史）→ 重试/撤销（二次确认）。
 * 操作走后端 60s 冷却；重试复用 checkpoint 续跑语义（已跑节点不重跑）。
 */
import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Clock, RefreshCw, XCircle } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/layout/PageHeader'
import {
  TASK_STATUSES,
  getAdminTask,
  getAdminTaskCheckpoints,
  getAdminTaskStats,
  listAdminTasks,
  retryAdminTask,
  revokeAdminTask,
  type TaskRow,
} from '@/api/adminTasks'
import { ApiError } from '@/api/client'

const STATUS_BADGE: Record<string, string> = {
  PENDING: 'bg-slate-100 text-slate-700',
  RUNNING: 'bg-blue-100 text-blue-700',
  WAITING_USER: 'bg-amber-100 text-amber-700',
  PAUSED: 'bg-amber-100 text-amber-700',
  SUCCESS: 'bg-emerald-100 text-emerald-700',
  FAILED: 'bg-red-100 text-red-700',
  CANCELLED: 'bg-zinc-200 text-zinc-600',
}

/** 操作进行中/弹窗确认状态 */
interface ConfirmState {
  action: 'retry' | 'revoke'
  task: TaskRow
}

function fmtTime(t: string | null) {
  if (!t) return '-'
  return t.replace('T', ' ').slice(0, 19)
}

function fmtDuration(ms: number | null) {
  if (ms === null || ms === undefined) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m${Math.round((ms % 60_000) / 1000)}s`
}

function StatCard(props: { label: string; value: string; icon: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <div className={clsx('rounded-full p-2', props.tone ?? 'bg-slate-100 text-slate-600')}>
        {props.icon}
      </div>
      <div>
        <div className="text-xl font-semibold text-slate-800">{props.value}</div>
        <div className="text-xs text-slate-500">{props.label}</div>
      </div>
    </div>
  )
}

export default function TasksPage() {
  const qc = useQueryClient()
  const [status, setStatus] = useState('')
  const [hours, setHours] = useState(168)
  const [retriedOnly, setRetriedOnly] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [detailId, setDetailId] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmState | null>(null)
  const [opMessage, setOpMessage] = useState('')
  const [opError, setOpError] = useState('')

  const listQuery = useQuery({
    queryKey: ['admin-tasks', status, hours, retriedOnly],
    queryFn: () =>
      listAdminTasks({
        status,
        hours,
        retries_gt: retriedOnly ? 0 : undefined,
        limit: 50,
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  })

  const statsQuery = useQuery({
    queryKey: ['admin-task-stats'],
    queryFn: () => getAdminTaskStats(24),
    refetchInterval: 30_000,
  })

  const detailQuery = useQuery({
    queryKey: ['admin-task-detail', detailId],
    queryFn: () => getAdminTask(detailId!),
    enabled: !!detailId,
  })

  const checkpointQuery = useQuery({
    queryKey: ['admin-task-checkpoints', detailId],
    queryFn: () => getAdminTaskCheckpoints(detailId!),
    enabled: !!detailId,
  })

  // 30s 自动轮询时清掉过期提示
  useEffect(() => {
    if (opMessage || opError) {
      const t = setTimeout(() => {
        setOpMessage('')
        setOpError('')
      }, 5000)
      return () => clearTimeout(t)
    }
  }, [opMessage, opError])

  async function runOp() {
    if (!confirm) return
    try {
      const fn = confirm.action === 'retry' ? retryAdminTask : revokeAdminTask
      const res = await fn(confirm.task.task_id)
      setOpMessage(res.message ?? '操作成功')
      setOpError('')
      void qc.invalidateQueries({ queryKey: ['admin-tasks'] })
      void qc.invalidateQueries({ queryKey: ['admin-task-stats'] })
      if (detailId === confirm.task.task_id) {
        void qc.invalidateQueries({ queryKey: ['admin-task-detail', detailId] })
      }
    } catch (e) {
      setOpMessage('')
      setOpError(e instanceof ApiError ? `${e.message}（${e.status}）` : '操作失败')
    } finally {
      setConfirm(null)
    }
  }

  const tasks = listQuery.data?.tasks ?? []
  const stats = statsQuery.data
  const detail = detailQuery.data

  const filtered = keyword
    ? tasks.filter(
        (t) =>
          t.task_id.includes(keyword) ||
          t.error_message.includes(keyword) ||
          t.worker.includes(keyword) ||
          t.trace_id.includes(keyword) ||
          t.biz_id.includes(keyword),
      )
    : tasks

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <PageHeader title="任务中心" desc="异步 Agent 任务：状态 / 耗时 / 失败归因 / 重试与撤销（管理员）" />

      {/* 统计卡（24h） */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <StatCard label="24h 任务总数" value={String(stats?.total ?? '-')} icon={<Clock size={18} />} />
        <StatCard
          label="成功率（终态）"
          value={stats?.success_rate === null || stats === undefined ? '-' : `${(stats.success_rate * 100).toFixed(1)}%`}
          icon={<CheckCircle2 size={18} />}
          tone="bg-emerald-100 text-emerald-600"
        />
        <StatCard
          label="失败率（终态）"
          value={stats?.failure_rate === null || stats === undefined ? '-' : `${(stats.failure_rate * 100).toFixed(1)}%`}
          icon={<XCircle size={18} />}
          tone="bg-red-100 text-red-600"
        />
        <StatCard
          label="P95 耗时"
          value={stats?.p95_duration_ms ? fmtDuration(stats.p95_duration_ms) : '-'}
          icon={<Clock size={18} />}
          tone="bg-blue-100 text-blue-600"
        />
        <StatCard
          label="重试过任务数"
          value={String(stats?.retried_count ?? '-')}
          icon={<AlertTriangle size={18} />}
          tone="bg-amber-100 text-amber-600"
        />
      </div>

      {/* 筛选条 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">全部状态</option>
          {TASK_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          value={hours}
          onChange={(e) => setHours(Number(e.target.value))}
        >
          <option value={1}>近 1 小时</option>
          <option value={24}>近 24 小时</option>
          <option value={168}>近 7 天</option>
          <option value={720}>近 30 天</option>
        </select>
        <label className="flex items-center gap-1 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={retriedOnly}
            onChange={(e) => setRetriedOnly(e.target.checked)}
          />
          仅看重试过
        </label>
        <input
          className="w-64 rounded border border-slate-300 px-2 py-1.5 text-sm"
          placeholder="task_id / worker / trace / biz / 错误关键字"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
        />
        <button
          className="flex items-center gap-1 rounded border border-slate-300 px-2 py-1.5 text-sm hover:bg-slate-50"
          onClick={() => void listQuery.refetch()}
        >
          <RefreshCw size={14} className={listQuery.isFetching ? 'animate-spin' : ''} /> 刷新
        </button>
        <span className="text-xs text-slate-400">共 {listQuery.data?.total ?? 0} 条 · 15s 自动轮询</span>
      </div>

      {opMessage && (
        <div className="mb-3 rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {opMessage}
        </div>
      )}
      {opError && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {opError}
        </div>
      )}

      {/* 任务表 */}
      <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2">任务</th>
              <th className="px-3 py-2">状态</th>
              <th className="px-3 py-2">重试</th>
              <th className="px-3 py-2">耗时</th>
              <th className="px-3 py-2">Worker</th>
              <th className="px-3 py-2">创建时间</th>
              <th className="px-3 py-2">错误摘要</th>
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {listQuery.isLoading && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                  加载中…
                </td>
              </tr>
            )}
            {filtered.length === 0 && !listQuery.isLoading && (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                  时间窗内没有匹配任务
                </td>
              </tr>
            )}
            {filtered.map((t) => {
              const canRetry = ['FAILED', 'PAUSED', 'WAITING_USER'].includes(t.status)
              const canRevoke = !['SUCCESS', 'FAILED', 'CANCELLED'].includes(t.status)
              return (
                <tr key={t.task_id} className="border-b border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-2">
                    <button
                      className="font-mono text-xs text-blue-600 hover:underline"
                      onClick={() => setDetailId(t.task_id)}
                    >
                      {t.task_id.slice(0, 8)}…
                    </button>
                    <div className="text-xs text-slate-400">{t.graph_name}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span className={clsx('rounded px-1.5 py-0.5 text-xs font-medium', STATUS_BADGE[t.status] ?? 'bg-slate-100 text-slate-600')}>
                      {t.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {t.retry_count > 0 ? `${t.retry_count}/${t.max_retries}` : '-'}
                  </td>
                  <td className="px-3 py-2 text-xs">{fmtDuration(t.duration_ms)}</td>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500">{t.worker || '-'}</td>
                  <td className="px-3 py-2 text-xs text-slate-500">{fmtTime(t.created_at)}</td>
                  <td className="max-w-52 truncate px-3 py-2 text-xs text-red-600" title={t.error_message}>
                    {t.error_message || '-'}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      <button
                        disabled={!canRetry}
                        className="rounded border border-slate-300 px-1.5 py-0.5 text-xs disabled:opacity-40 hover:bg-slate-100"
                        onClick={() => setConfirm({ action: 'retry', task: t })}
                      >
                        重试
                      </button>
                      <button
                        disabled={!canRevoke}
                        className="rounded border border-red-200 px-1.5 py-0.5 text-xs text-red-600 disabled:opacity-40 hover:bg-red-50"
                        onClick={() => setConfirm({ action: 'revoke', task: t })}
                      >
                        撤销
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* 详情抽屉 */}
      {detailId && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={() => setDetailId(null)}>
          <div
            className="h-full w-full max-w-2xl overflow-y-auto bg-white p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-800">任务详情</h2>
              <button className="text-slate-400 hover:text-slate-600" onClick={() => setDetailId(null)}>
                ✕
              </button>
            </div>
            {detailQuery.isLoading && <p className="text-sm text-slate-400">加载中…</p>}
            {detail && (
              <div className="space-y-4 text-sm">
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  <div>
                    <div className="text-xs text-slate-400">task_id</div>
                    <div className="font-mono text-xs break-all">{detail.task_id}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">状态 / 队列</div>
                    <div>
                      {detail.status} · {detail.queue}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">worker</div>
                    <div className="font-mono text-xs">{detail.worker || '-'}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">trace_id</div>
                    <div className="font-mono text-xs break-all">{detail.trace_id || '-'}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">celery_task_id</div>
                    <div className="font-mono text-xs break-all">{detail.celery_task_id || '-'}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">thread_id（checkpoint）</div>
                    <div className="font-mono text-xs break-all">{detail.thread_id || '-'}</div>
                  </div>
                </div>

                {/* 时间线 */}
                <div className="rounded border border-slate-200 p-3">
                  <div className="mb-2 text-xs font-semibold text-slate-500">时间线</div>
                  <div className="space-y-1 text-xs text-slate-600">
                    <div>创建：{fmtTime(detail.created_at)}</div>
                    <div>入队：{fmtTime(detail.queued_at)}</div>
                    <div>开始：{fmtTime(detail.started_at)}</div>
                    <div>结束：{fmtTime(detail.finished_at)}</div>
                    <div>耗时：{fmtDuration(detail.duration_ms)} · 重试 {detail.retry_count}/{detail.max_retries}</div>
                  </div>
                </div>

                {/* 输入 / 输出 */}
                <div>
                  <div className="mb-1 text-xs font-semibold text-slate-500">输入</div>
                  <pre className="max-h-40 overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap">
                    {JSON.stringify(detail.input ?? {}, null, 2)}
                  </pre>
                </div>
                {detail.result !== null && detail.result !== undefined && (
                  <div>
                    <div className="mb-1 text-xs font-semibold text-slate-500">输出</div>
                    <pre className="max-h-40 overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap">
                      {JSON.stringify(detail.result, null, 2)}
                    </pre>
                  </div>
                )}

                {/* 异常 */}
                {detail.error_message && (
                  <div className="rounded border border-red-200 bg-red-50 p-3">
                    <div className="text-xs font-semibold text-red-600">
                      {detail.error_type || 'ERROR'} · {detail.error_message}
                    </div>
                    {detail.traceback && (
                      <pre className="mt-2 max-h-56 overflow-auto text-xs whitespace-pre-wrap text-red-800">
                        {detail.traceback}
                      </pre>
                    )}
                  </div>
                )}

                {/* checkpoint 历史 */}
                <div>
                  <div className="mb-1 text-xs font-semibold text-slate-500">节点历史（checkpoints）</div>
                  {checkpointQuery.data && checkpointQuery.data.length > 0 ? (
                    <ol className="space-y-1 border-l border-slate-200 pl-3 text-xs text-slate-600">
                      {checkpointQuery.data.map((c) => (
                        <li key={c.id}>
                          <span className="font-medium">{c.node_name}</span>
                          <span className="ml-2 text-slate-400">{fmtTime(c.created_at)}</span>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p className="text-xs text-slate-400">无节点记录</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 二次确认弹窗 */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-96 rounded-lg bg-white p-5 shadow-xl">
            <h3 className="mb-2 font-semibold text-slate-800">
              确认{confirm.action === 'retry' ? '重试' : '撤销'}任务？
            </h3>
            <p className="mb-4 font-mono text-xs text-slate-500">{confirm.task.task_id}</p>
            {confirm.action === 'retry' && (
              <p className="mb-4 rounded bg-amber-50 p-2 text-xs text-amber-700">
                将从最近 checkpoint 续跑（已完成的节点不会重跑）；同一任务 60s 内只能操作一次。
              </p>
            )}
            {confirm.action === 'revoke' && (
              <p className="mb-4 rounded bg-red-50 p-2 text-xs text-red-700">
                队列内任务直接 revoke；执行中的任务在下一节点边界生效。操作会记入审计日志。
              </p>
            )}
            <div className="flex justify-end gap-2">
              <button
                className="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50"
                onClick={() => setConfirm(null)}
              >
                取消
              </button>
              <button
                className={clsx(
                  'rounded px-3 py-1.5 text-sm text-white',
                  confirm.action === 'retry' ? 'bg-blue-600 hover:bg-blue-700' : 'bg-red-600 hover:bg-red-700',
                )}
                onClick={() => void runOp()}
              >
                确认{confirm.action === 'retry' ? '重试' : '撤销'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
