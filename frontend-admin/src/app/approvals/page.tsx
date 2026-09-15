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
 */
import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/layout/PageHeader'
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
  pending: { text: '#633806', bg: '#FAEEDA' },
  approved: { text: '#2F7D32', bg: '#E8F3E9' },
  rejected: { text: '#791F1F', bg: '#FCEBEB' },
  executed: { text: '#1D4ED8', bg: '#E3EAFB' },
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
    <div>
      <PageHeader
        title="工具审批"
        desc="写操作工具的人工审批门 · 批准后 TTL 内重试相同操作即可执行 · 审计字段记录审批人与理由"
      />

      {/* 状态 tab */}
      <div className="mb-4 flex items-center gap-1 rounded-lg bg-black/[0.03] p-0.5 text-[12px]" style={{ width: 'fit-content' }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={clsx('rounded-[6px] px-3 py-1.5 transition-colors',
              tab === t.key ? 'bg-white text-accent font-medium shadow-sm' : 'text-text-secondary hover:text-text-primary')}
          >
            {t.label}
          </button>
        ))}
        <button
          onClick={() => load()}
          className="ml-1 flex items-center gap-1 rounded-[6px] px-2 py-1.5 text-text-muted transition-colors hover:text-text-primary"
          title="刷新"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {tab === 'pending' && (
        <div className="mb-3 flex items-center gap-1.5 rounded-lg px-3 py-2 text-[12px]" style={{ background: '#FAEEDA', color: '#633806' }}>
          <ShieldCheck size={14} />
          以下写操作被审批门拦下，请核对参数后处置 · 15 秒自动刷新
        </div>
      )}

      {/* 列表 */}
      <div className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
        {loading ? (
          <div className="p-10 text-center text-[13px] text-text-muted">加载中…</div>
        ) : error ? (
          <div className="p-10 text-center text-[13px]" style={{ color: '#791F1F' }}>{error}</div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center text-[13px] text-text-muted">
            {tab === 'pending' ? '没有待审批的操作' : '暂无记录'}
          </div>
        ) : (
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-black/5 text-left text-[12px] text-text-muted">
                <th className="px-4 py-2.5 font-normal">操作</th>
                <th className="px-4 py-2.5 font-normal">摘要</th>
                <th className="px-4 py-2.5 font-normal">发起人</th>
                <th className="px-4 py-2.5 font-normal">创建时间</th>
                <th className="px-4 py-2.5 font-normal">状态</th>
                <th className="px-4 py-2.5 font-normal text-right">处置</th>
              </tr>
            </thead>
            <tbody>
              {items.map((req) => {
                const open = expanded === req.id
                const st = STATUS_STYLE[req.status] ?? { text: '#6b7280', bg: 'rgba(0,0,0,0.04)' }
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

      {/* 决策弹窗（二次确认 + 理由必填） */}
      {deciding && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4" onClick={() => !submitting && setDeciding(null)}>
          <div className="w-full max-w-[520px] rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center gap-2">
              <span
                className="flex h-7 w-7 items-center justify-center rounded-lg"
                style={{ background: deciding.approve ? '#E8F3E9' : '#FCEBEB' }}
              >
                {deciding.approve
                  ? <Check size={15} style={{ color: '#2F7D32' }} />
                  : <X size={15} style={{ color: '#791F1F' }} />}
              </span>
              <h3 className="text-[15px] font-medium text-text-primary">
                {deciding.approve ? '批准' : '驳回'}该写操作？
              </h3>
            </div>

            <div className="mb-3 rounded-lg bg-surface-elevated p-3 text-[12px] leading-relaxed">
              <div className="font-medium text-text-primary">
                {deciding.req.tool_name}.{deciding.req.action}
              </div>
              {summarize(deciding.req) && (
                <div className="mt-1 break-all text-text-secondary">{summarize(deciding.req)}</div>
              )}
              <div className="mt-1 text-text-muted">发起人 {deciding.req.user_id || 'unknown'} · {fmtTime(deciding.req.created_at)}</div>
            </div>

            <label className="mb-1.5 block text-[12px] text-text-secondary">
              审批理由 <span style={{ color: '#791F1F' }}>*</span>
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              autoFocus
              placeholder={deciding.approve ? '例如：已核对 SQL 仅更新目标行' : '例如：参数范围超出预期，驳回重发'}
              className="w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-[13px] outline-none transition-shadow focus:border-accent"
              style={{ boxShadow: 'var(--shadow-input, none)' }}
            />

            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeciding(null)}
                disabled={submitting}
                className="rounded-lg border border-black/10 px-4 py-2 text-[13px] text-text-secondary transition-colors hover:bg-black/[0.03] disabled:opacity-60"
              >
                取消
              </button>
              <button
                onClick={submitDecision}
                disabled={submitting || !reason.trim()}
                className="rounded-lg px-4 py-2 text-[13px] font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60"
                style={{ background: deciding.approve ? '#2F7D32' : '#B91C1C' }}
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
      <tr className="border-b border-black/[0.04] transition-colors hover:bg-black/[0.02]">
        <td className="px-4 py-3">
          <button onClick={onToggle} className="flex items-center gap-1.5 text-left font-medium text-text-primary hover:text-accent">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {req.tool_name}.{req.action}
          </button>
        </td>
        <td className="max-w-[320px] truncate px-4 py-3 text-text-secondary" title={summarize(req)}>
          {summarize(req) || '—'}
        </td>
        <td className="px-4 py-3 text-text-secondary">{req.user_id || 'unknown'}</td>
        <td className="px-4 py-3 text-text-muted">{fmtTime(req.created_at)}</td>
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
                className="rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors"
                style={{ background: '#E8F3E9', color: '#2F7D32' }}
              >
                批准
              </button>
              <button
                onClick={() => onDecide(false)}
                className="rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors"
                style={{ background: '#FCEBEB', color: '#791F1F' }}
              >
                驳回
              </button>
            </span>
          ) : (
            <span className="text-[12px] text-text-muted">
              {req.reviewer ? `${req.reviewer} · ${fmtTime(req.decided_at)}` : '—'}
            </span>
          )}
        </td>
      </tr>
      {open && (
        <tr className="border-b border-black/[0.04]" style={{ background: '#fafbfc' }}>
          <td colSpan={6} className="px-4 py-3">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div>
                <div className="mb-1 text-[11px] font-medium text-text-muted">调用参数</div>
                <pre className="max-h-56 overflow-auto rounded-lg bg-white p-3 text-[12px] leading-relaxed" style={{ border: '1px solid var(--border-subtle)' }}>
                  {JSON.stringify(req.detail ?? {}, null, 2)}
                </pre>
              </div>
              <div className="text-[12px] leading-relaxed text-text-secondary">
                <div className="mb-1 text-[11px] font-medium text-text-muted">审批信息</div>
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
