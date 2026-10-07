'use client'

/**
 * 问题收集面板（2026-10-08 #13）—— data-explorer 第三 tab。
 *
 * 线上用户问题台账（ai.question_ledger）：按域浏览 → 勾选 → 一键转候选
 * 评测集（走 /api/question-ledger/to-candidates，approve 在评测治理面板
 * 完成）。加载失败降级为空态文案，不阻塞另外两个 tab。
 */
import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Sparkles } from 'lucide-react'
import {
  questionLedgerService,
  QUESTION_DOMAIN_LABELS,
  QUESTION_STATUS_LABELS,
  type QuestionLedgerItem,
} from '@/api/questionLedger'

const PAGE_SIZE = 20

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-slate-100 text-slate-600 border-slate-200',
  accepted: 'bg-green-50 text-green-700 border-green-200',
  dismissed: 'bg-slate-50 text-slate-400 border-slate-200',
}

function timeLabel(ts: string): string {
  if (!ts) return '--'
  return ts.replace('T', ' ').slice(0, 19)
}

export default function QuestionLedgerPanel() {
  const [domain, setDomain] = useState('')
  const [status, setStatus] = useState('pending')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<QuestionLedgerItem[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<Record<string, number>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await questionLedgerService.list({ domain, status, page, page_size: PAGE_SIZE })
      setItems(data.items || [])
      setTotal(data.total || 0)
      setStats(data.stats_by_domain || {})
      setSelected(new Set())
    } catch (e) {
      setError((e as Error).message)
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [domain, status, page])

  useEffect(() => { load() }, [load])

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const convertSelected = async () => {
    if (selected.size === 0) return
    setNotice('')
    try {
      const out = await questionLedgerService.toCandidates([...selected])
      setNotice(
        `已转候选 ${out.converted.length} 条` +
        (out.skipped.length ? `，跳过 ${out.skipped.length} 条` : '') +
        '——到「评测中心 → 数据集治理」审核后进入不可变版本目录。')
      await load()
    } catch (e) {
      setNotice(`转候选失败: ${(e as Error).message}`)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-3">
      {/* 头部：域统计 + 筛选 + 批量操作 */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-text-muted">近24h：</span>
        {Object.entries(QUESTION_DOMAIN_LABELS).map(([d, label]) => (
          <span key={d} className="text-[11px] px-1.5 py-0.5 rounded bg-surface-elevated border border-border-subtle text-text-muted">
            {label} <b className="font-mono text-text-primary">{stats[d] ?? 0}</b>
          </span>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => { setPage(1); load() }}
          className="text-xs text-text-muted border border-border-subtle rounded px-3 py-1.5 hover:bg-surface-hover flex items-center gap-1"
        >
          <RefreshCw size={12} /> 刷新
        </button>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={domain}
          onChange={(e) => { setDomain(e.target.value); setPage(1) }}
          className="text-xs border border-border-subtle rounded px-2 py-1.5 bg-surface"
        >
          <option value="">全部域</option>
          {Object.entries(QUESTION_DOMAIN_LABELS).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <select
          value={status}
          onChange={(e) => { setStatus(e.target.value); setPage(1) }}
          className="text-xs border border-border-subtle rounded px-2 py-1.5 bg-surface"
        >
          <option value="">全部状态</option>
          {Object.entries(QUESTION_STATUS_LABELS).map(([v, label]) => (
            <option key={v} value={v}>{label}</option>
          ))}
        </select>
        <div className="flex-1" />
        {selected.size > 0 && (
          <button
            onClick={convertSelected}
            className="text-xs font-medium rounded px-3 py-1.5 bg-violet-600 text-white hover:bg-violet-700 flex items-center gap-1"
          >
            <Sparkles size={12} /> 转候选评测集（{selected.size}）
          </button>
        )}
      </div>

      {notice && (
        <div className="text-xs px-3 py-2 rounded border border-border-subtle bg-surface-elevated text-text-muted">{notice}</div>
      )}

      {/* 列表 */}
      <div className="border border-border-subtle rounded-lg overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-xs text-text-muted">加载中…</div>
        ) : error ? (
          <div className="p-8 text-center text-xs text-red-500">加载失败: {error}</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-xs text-text-muted">
            暂无收集到的问题——各域请求完成时会自动入账（旁路软失败），跑几轮对话后再来看。
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-surface-elevated border-b border-border-subtle">
                <th className="px-2 py-2 w-8"><span className="sr-only">选择</span></th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">域</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">问题</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">用户</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">来源</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">状态</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">时间</th>
                <th className="px-3 py-2 text-left font-medium text-text-muted">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-b border-border-subtle last:border-0 hover:bg-surface-hover">
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      className="rounded"
                      checked={selected.has(it.id)}
                      onChange={() => toggle(it.id)}
                    />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="px-1.5 py-0.5 rounded text-[10px] bg-surface-elevated border border-border-subtle">
                      {QUESTION_DOMAIN_LABELS[it.domain] ?? it.domain}
                    </span>
                  </td>
                  <td className="px-3 py-2 max-w-[420px]">
                    <p className="truncate text-text-primary" title={it.question}>{it.question}</p>
                    {it.answer_summary && (
                      <p className="truncate text-[10px] text-text-muted" title={it.answer_summary}>{it.answer_summary}</p>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-text-muted whitespace-nowrap">{it.user_id || '--'}</td>
                  <td className="px-3 py-2 text-text-muted whitespace-nowrap">{it.source}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] border ${STATUS_BADGE[it.status] || 'bg-slate-100 text-slate-600 border-slate-200'}`}>
                      {QUESTION_STATUS_LABELS[it.status] ?? it.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-text-muted whitespace-nowrap">{timeLabel(it.created_at)}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <button
                      onClick={async () => { await questionLedgerService.setStatus(it.id, 'dismissed'); load() }}
                      className="text-[11px] text-text-muted hover:text-slate-700"
                    >
                      忽略
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 分页 */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-end gap-2 text-xs text-text-muted">
          <button
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="border border-border-subtle rounded px-2 py-1 disabled:opacity-40 hover:bg-surface-hover"
          >
            上一页
          </button>
          <span>{page} / {totalPages}（共 {total} 条）</span>
          <button
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="border border-border-subtle rounded px-2 py-1 disabled:opacity-40 hover:bg-surface-hover"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
