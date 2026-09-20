'use client'

import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, RefreshCw, Rocket, X } from 'lucide-react'
import RoleGate from '@/components/auth/RoleGate'
import PageHeader from '@/components/layout/PageHeader'
import { useToast } from '@/components/shared/Toast'
import {
  canPromoteCandidate,
  listFeedbackCandidates,
  promoteFeedbackCandidate,
  reviewFeedbackCandidate,
  type CandidateStatus,
  type FeedbackCandidate,
} from '@/api/feedbackCandidates'

const FILTERS: Array<{ value?: CandidateStatus; label: string }> = [
  { label: '全部' }, { value: 'pending', label: '待审核' }, { value: 'approved', label: '已批准' }, { value: 'rejected', label: '已拒绝' }, { value: 'promoted', label: '已提升' },
]

export default function FeedbackCandidatesPage() {
  const toast = useToast()
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<CandidateStatus | undefined>()
  const [busy, setBusy] = useState('')
  const candidates = useQuery({ queryKey: ['feedback-candidates', filter], queryFn: () => listFeedbackCandidates(filter), refetchInterval: 30_000 })

  async function review(item: FeedbackCandidate, decision: 'approve' | 'reject') {
    const note = window.prompt(decision === 'approve' ? '审核备注（可选）' : '请输入拒绝原因', '') ?? ''
    if (decision === 'reject' && !note.trim()) return
    setBusy(item.candidate_id)
    try {
      await reviewFeedbackCandidate(item.candidate_id, decision, note)
      toast.success(decision === 'approve' ? '候选已批准' : '候选已拒绝')
      await queryClient.invalidateQueries({ queryKey: ['feedback-candidates'] })
    } catch (error) { toast.error(error instanceof Error ? error.message : '候选审核失败') } finally { setBusy('') }
  }

  async function promote(item: FeedbackCandidate) {
    setBusy(item.candidate_id)
    try {
      await promoteFeedbackCandidate(item.candidate_id)
      toast.success('候选已写入评测集')
      await queryClient.invalidateQueries({ queryKey: ['feedback-candidates'] })
    } catch (error) { toast.error(error instanceof Error ? error.message : '候选提升失败') } finally { setBusy('') }
  }

  return <RoleGate minRole="admin" pageName="反馈候选治理"><div className="flex-1 overflow-y-auto"><div className="mx-auto max-w-6xl px-6 py-8"><PageHeader title="反馈候选治理" desc="负反馈与纠错先进入 pending 候选；审核通过后才允许写入评测集，避免反馈直接污染基准。" /><div className="mb-4 flex items-center justify-between gap-3"><div className="flex gap-1 rounded-lg bg-black/[0.03] p-1">{FILTERS.map((item) => <button key={item.label} onClick={() => setFilter(item.value)} className={`rounded-md px-3 py-1.5 text-xs ${filter === item.value ? 'bg-white font-medium text-accent shadow-sm' : 'text-text-secondary hover:text-text-primary'}`}>{item.label}</button>)}</div><button onClick={() => candidates.refetch()} className="flex items-center gap-1 rounded-lg border border-black/10 px-3 py-1.5 text-xs text-text-secondary hover:text-accent"><RefreshCw size={13} className={candidates.isFetching ? 'animate-spin' : ''} />刷新</button></div><div className="space-y-3">{(candidates.data?.items ?? []).map((item) => <CandidateCard key={item.candidate_id} item={item} busy={busy === item.candidate_id} onReview={review} onPromote={promote} />)}{!candidates.isLoading && (candidates.data?.items ?? []).length === 0 && <div className="rounded-xl border border-black/5 bg-white px-4 py-12 text-center text-xs text-text-muted">暂无反馈候选</div>}</div></div></div></RoleGate>
}

function CandidateCard({ item, busy, onReview, onPromote }: { item: FeedbackCandidate; busy: boolean; onReview: (item: FeedbackCandidate, decision: 'approve' | 'reject') => void; onPromote: (item: FeedbackCandidate) => void }) {
  const [expanded, setExpanded] = useState(false)
  const statusColor = item.status === 'promoted' ? 'bg-emerald-50 text-emerald-700' : item.status === 'approved' ? 'bg-blue-50 text-blue-700' : item.status === 'rejected' ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-700'
  return <article className="rounded-xl border border-black/5 bg-white shadow-card"><button onClick={() => setExpanded((value) => !value)} className="flex w-full items-center gap-3 px-4 py-3 text-left"><ChevronDown size={14} className={`text-text-muted transition-transform ${expanded ? 'rotate-180' : ''}`} /><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-mono text-xs font-medium text-text-primary">{item.candidate_id}</span><span className={`rounded-full px-2 py-1 text-[10px] ${statusColor}`}>{item.status}</span><span className="text-[10px] text-text-muted">{item.module}</span></div><div className="mt-1 truncate text-xs text-text-secondary">Trace: {item.trace_id}</div></div><time className="text-[10px] text-text-muted">{new Date(item.created_at).toLocaleString('zh-CN')}</time></button>{expanded && <div className="border-t border-slate-100 px-4 py-4"><div className="grid gap-3 text-xs md:grid-cols-2"><div><div className="mb-1 text-[10px] text-text-muted">纠错内容</div><div className="rounded-lg bg-slate-50 p-3 whitespace-pre-wrap text-text-secondary">{item.correction_text || '未填写'}</div></div><div><div className="mb-1 text-[10px] text-text-muted">期望答案</div><div className="rounded-lg bg-slate-50 p-3 whitespace-pre-wrap text-text-secondary">{item.expected_answer || '未填写'}</div></div></div><div className="mt-4 flex justify-end gap-2">{item.status === 'pending' && <><button disabled={busy} onClick={() => onReview(item, 'reject')} className="flex items-center gap-1 rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-700 disabled:opacity-50"><X size={13} />拒绝</button><button disabled={busy} onClick={() => onReview(item, 'approve')} className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white disabled:opacity-50"><Check size={13} />批准</button></>}{canPromoteCandidate(item.status) && <button disabled={busy} onClick={() => onPromote(item)} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-1.5 text-xs text-white disabled:opacity-50"><Rocket size={13} />提升到评测集</button>}</div></div>}</article>
}
