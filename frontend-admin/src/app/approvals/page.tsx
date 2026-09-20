'use client'

/**
 * /approvals — 工具审批（human-in-the-loop）
 *
 * 后端 /approvals：写操作工具触发审批单（ai.tool_approval_requests），
 * 管理员在此批准/驳回。批准后 TTL 内重试相同指纹即可执行。
 *
 * 红线（全局规范：写库/发消息/删数据/花钱必须人工确认）：
 * - 批准/驳回理由必填（审计留痕）；
 * - 确认在弹窗内二次完成，列表按钮只打开弹窗；
 * - reviewer 不传，后端取网关注入的身份头（缺省记 unknown，不冒充 admin）。
 * - pending 列表 15s 轮询，避免管理员盯着手动刷新。
 *
 * 布局对齐 /observability/traces（2026-09-18）：min-h 容器 +
 * 左标题右操作头部 + slate 白卡体系。
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { clsx } from 'clsx'
import { useToast } from '@/components/shared/Toast'
import { approvalService, type ApprovalRequest, type ApprovalStatus } from '@/api/approvals'

const TABS: { key: ApprovalStatus | ''; label: string }[] = [
  { key: 'pending', label: '待审批' },
  { key: 'approved', label: '已批准' },
  { key: 'rejected', label: '已驳回' },
  { key: 'executed', label: '已执行' },
  { key: '', label: '全部' },
]

const STATUS_STYLE: Record<string, { text: string; bg: string }> = {
  pending: { text: '#b45309', bg: '#fef3c7' },
  approved: { text: '#047857', bg: '#d1fae5' },
  rejected: { text: '#b91c1c', bg: '#fee2e2' },
  executed: { text: '#1d4ed8', bg: '#dbeafe' },
}

/** 审批单的副作用摘要：尽量给出人能读的一行说明 */
function summarize(req: ApprovalRequest): string {
  const d = (req.detail ?? {}) as Record<string, unknown>
  const parts: string[] = []
  for (const key of ['sql', 'query', 'path', 'file_path', 'url', 'recipient', 'to', 'subject', 'collection', 'doc_id']) {
    const v = d[key]
    if (typeof v === 'string' && v) parts.push(`${key}: ${v}`)
  }
  return parts.join('　·　')
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 19)
}

export default function ApprovalsPage() {
  const toast = useToast()
  const [tab, setTab] = useState<ApprovalStatus | ''>('pending')
  const [items, setItems] = useState<ApprovalRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  /** 弹窗：待决策的审批单 + 方向 */
  const [deciding, setDeciding] = useState<{ req: ApprovalRequest; approve: boolean } | null>(null)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const r = await approvalService.list(tab, 100)
      setItems(r.items ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载审批单失败')
    } finally {
      setLoading(false)
    }
  }, [tab])

  useEffect(() => { load() }, [load])

  // 仅待审批 tab 轮询：新审批单自动出现
  useEffect(() => {
    if (tab !== 'pending') return
    const t = setInterval(() => { load(true) }, 15000)
    return () => clearInterval(t)
  }, [tab, load])

  async function submitDecision() {
    if (!deciding) return
    if (!reason.trim()) {
      toast.warning('请填写审批理由（审计留痕）')
      return
    }
    setSubmitting(true)
    try {
      if (deciding.approve) {
        await approvalService.approve(deciding.req.id, { reason: reason.trim() })
        toast.success('已批准')
      } else {
        await approvalService.reject(deciding.req.id, { reason: reason.trim() })
        toast.success('已驳回')
      }
      setDeciding(null)
      setReason('')
      load(true)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="max-w-[1440px] mx-auto px-6 py-6 space-y-5">
        {/* Header：左标题 + 右操作（计数 / 刷新），同 traces 页布局 */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-800">工具审批</h1>
            <p className="text-xs text-slate-500 mt-0.5">
              写操作工具的人工审批门 · 批准后 TTL 内重试相同操作即可执行 · 审计字段记录审批人与理由
            </p>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-400">
            {!loading && !error && (
              <span>共 {items.length.toLocaleString('zh-CN')} 条</span>
            )}
            <button
              onClick={() => load()}
              disabled={loading}
              className="flex items-center gap-1.5 text-slate-500 hover:text-slate-700 bg-white border border-slate-200 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-50"
              title="刷新"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
            </button>
          </div>
        </div>

        {/* 状态 tab */}
        <div className="flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white p-0.5 text-xs" style={{ width: 'fit-content' }}>
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={clsx('rounded-md px-3 py-1.5 transition-colors',
                tab === t.key ? 'bg-violet-50 font-medium text-violet-700' : 'text-slate-500 hover:text-slate-800')}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'pending' && (
          <div className="flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
            <ShieldCheck size={14} />
            以下写操作被审批门拦下，请核对参数后处置 · 15 秒自动刷新
          </div>
        )}

        {/* 列表 */}
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          {loading ? (
            <div className="py-12 text-center text-sm text-slate-400">加载中…</div>
          ) : error ? (
            <div className="py-12 text-center text-sm text-red-500">{error}</div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-sm text-slate-400">
              {tab === 'pending' ? '没有待审批的操作' : '暂无记录'}
            </div>
          ) : (
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-slate-200 text-left text-xs font-medium text-slate-500">
                  <th className="px-4 py-2.5 font-medium">操作</th>
                  <th className="px-4 py-2.5 font-medium">摘要</th>
                  <th className="px-4 py-2.5 font-medium">发起人</th>
                  <th className="px-4 py-2.5 font-medium">创建时间</th>
                  <th className="px-4 py-2.5 font-medium">状态</th>
                  <th className="px-4 py-2.5 font-medium text-right">处置</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((req) => {
                  const open = expanded === req.id
                  const st = STATUS_STYLE[req.status] ?? { text: '#64748b', bg: '#f1f5f9' }
                  return (
                    <FragmentRow
                      key={req.id}
                      req={req}
                      open={open}
                      st={st}
                      onToggle={() => setExpanded(open ? null : req.id)}
                      onDecide={(approve) => { setReason(''); setDeciding({ req, approve }) }}
                      showActions={req.status === 'pending'}
                    />
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* 决策弹窗（二次确认 + 理由必填） */}
      {deciding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4" onClick={() => !submitting && setDeciding(null)}>
          <div className="w-full max-w-[520px] rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center gap-2">
              <span
                className="flex h-7 w-7 items-center justify-center rounded-lg"
                style={{ background: deciding.approve ? '#d1fae5' : '#fee2e2' }}
              >
                {deciding.approve
                  ? <Check size={15} style={{ color: '#047857' }} />
                  : <X size={15} style={{ color: '#b91c1c' }} />}
              </span>
              <h3 className="text-[15px] font-medium text-slate-800">
                {deciding.approve ? '批准' : '驳回'}该写操作？
              </h3>
            </div>

            <div className="mb-3 rounded-lg bg-slate-50 border border-slate-200 p-3 text-[12px] leading-relaxed">
              <div className="font-medium text-slate-800">
                {deciding.req.tool_name}.{deciding.req.action}
              </div>
              {summarize(deciding.req) && (
                <div className="mt-1 break-all text-slate-600">{summarize(deciding.req)}</div>
              )}
              <div className="mt-1 text-slate-400">发起人 {deciding.req.user_id || 'unknown'} · {fmtTime(deciding.req.created_at)}</div>
            </div>

            <label className="mb-1.5 block text-[12px] text-slate-500">
              审批理由 <span className="text-red-500">*</span>
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              autoFocus
              placeholder={deciding.approve ? '例如：已核对 SQL 仅更新目标行' : '例如：参数范围超出预期，驳回重发'}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-700 outline-none transition-colors focus:border-violet-400"
            />

            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeciding(null)}
                disabled={submitting}
                className="rounded-lg border border-slate-200 px-4 py-2 text-[13px] text-slate-500 transition-colors hover:bg-slate-50 disabled:opacity-60"
              >
                取消
              </button>
              <button
                onClick={submitDecision}
                disabled={submitting || !reason.trim()}
                className="rounded-lg px-4 py-2 text-[13px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                style={{ background: deciding.approve ? '#047857' : '#dc2626' }}
              >
                {submitting ? '提交中…' : deciding.approve ? '确认批准' : '确认驳回'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** 表格行 + 展开详情（<tr> 不能套 div，用 fragment 包两行） */
function FragmentRow(props: {
  req: ApprovalRequest
  open: boolean
  st: { text: string; bg: string }
  onToggle: () => void
  onDecide: (approve: boolean) => void
  showActions: boolean
}) {
  const { req, open, st, onToggle, onDecide, showActions } = props
  return (
    <>
      <tr className="transition-colors hover:bg-slate-50/60">
        <td className="px-4 py-3">
          <button onClick={onToggle} className="flex items-center gap-1.5 text-left font-medium text-slate-800 hover:text-violet-600">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {req.tool_name}.{req.action}
          </button>
        </td>
        <td className="max-w-[320px] truncate px-4 py-3 text-slate-500" title={summarize(req)}>
          {summarize(req) || '—'}
        </td>
        <td className="px-4 py-3 text-slate-500">{req.user_id || 'unknown'}</td>
        <td className="px-4 py-3 text-slate-400">{fmtTime(req.created_at)}</td>
        <td className="px-4 py-3">
          <span className="rounded-full px-2 py-0.5 text-[11px]" style={{ color: st.text, background: st.bg }}>
            {TABS.find((t) => t.key === req.status)?.label ?? req.status}
          </span>
        </td>
        <td className="px-4 py-3 text-right">
          {showActions ? (
            <span className="inline-flex gap-1.5">
              <button
                onClick={() => onDecide(true)}
                className="rounded-md bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
              >
                批准
              </button>
              <button
                onClick={() => onDecide(false)}
                className="rounded-md bg-red-50 px-2.5 py-1 text-[12px] font-medium text-red-700 transition-colors hover:bg-red-100"
              >
                驳回
              </button>
            </span>
          ) : (
            <span className="text-[12px] text-slate-400">
              {req.reviewer ? `${req.reviewer} · ${fmtTime(req.decided_at)}` : '—'}
            </span>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-slate-50/80">
          <td colSpan={6} className="px-4 py-3">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div>
                <div className="mb-1 text-[11px] font-medium text-slate-400">调用参数</div>
                <pre className="max-h-56 overflow-auto rounded-lg border border-slate-200 bg-white p-3 text-[12px] leading-relaxed text-slate-700">
                  {JSON.stringify(req.detail ?? {}, null, 2)}
                </pre>
              </div>
              <div className="text-[12px] leading-relaxed text-slate-500">
                <div className="mb-1 text-[11px] font-medium text-slate-400">审批信息</div>
                <div>单号：<span className="font-mono">{req.id}</span></div>
                <div>指纹：<span className="break-all font-mono text-[11px]">{req.fingerprint}</span></div>
                <div>批准后 TTL 内重试相同操作即可执行；驳回后同指纹再次触发会新建审批单。</div>
                {req.reason && <div className="mt-1">理由：{req.reason}</div>}
                {req.executed_at && <div>执行时间：{fmtTime(req.executed_at)}</div>}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
