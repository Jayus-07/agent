'use client'

import { useState } from 'react'
import { Search } from 'lucide-react'
import { evaluationService } from '@/api/evaluation'

interface EvidenceItem {
  doc_id: string
  chunk_id: string
  chunk_index: number
  title: string
  snippet: string
  content: string
  doc_version?: string
  score: number
}

export interface EvidenceClaimDraft {
  kb_id: string
  doc_id: string
  doc_version: string
  quote: string
}

/** 证据选择器：在知识库快照内检索切片，勾选后回填为证据锚点。
 * 引用只能来自快照内切片（与转正核验同一来源约束）；
 * 合成候选自带冻结快照 ID 时自动预填。 */
export default function EvidenceSearchPicker({ onInsert, defaultSnapshotId = '' }: { onInsert: (claim: EvidenceClaimDraft) => void; defaultSnapshotId?: string }) {
  const [snapshotId, setSnapshotId] = useState(defaultSnapshotId)
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<EvidenceItem[]>([])
  const [kbId, setKbId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)

  const search = async () => {
    setBusy(true); setError('')
    try {
      const result = await evaluationService.searchDatasetEvidence({ snapshot_id: snapshotId.trim(), query: query.trim(), top_k: 5 })
      setItems(result.items)
      setKbId(result.kb_id)
      setSearched(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : '证据检索失败')
    } finally { setBusy(false) }
  }

  const control = 'min-h-9 rounded-md border border-border-subtle bg-surface-base px-3 text-xs'
  return <div className="rounded-md border border-border-subtle p-3">
    <p className="text-xs font-medium text-text-primary">证据选择器<span className="ml-2 font-normal text-text-muted">引用只能来自快照内切片，选出的锚点可直接通过转正核验</span></p>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input aria-label="语料快照 ID" placeholder="语料快照 ID（snapshot_id）" value={snapshotId} onChange={event => setSnapshotId(event.target.value)} className={`${control} w-64`} />
      <input aria-label="证据检索关键词" placeholder="按问题或关键词检索切片" value={query} onChange={event => setQuery(event.target.value)} className={`${control} w-56`} onKeyDown={event => { if (event.key === 'Enter' && snapshotId.trim() && query.trim()) void search() }} />
      <button type="button" disabled={busy || !snapshotId.trim() || !query.trim()} onClick={() => void search()} className={`${control} inline-flex items-center gap-1 bg-accent text-white disabled:opacity-50`}><Search size={13} aria-hidden="true" />{busy ? '检索中…' : '检索'}</button>
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    {searched && items.length === 0 && <p className="mt-2 text-xs text-text-muted">没有命中切片：若该问题确实检索不到，可考虑把候选标为 no_evidence 拒答样本（对抗用例），不要硬凑证据。</p>}
    {items.length > 0 && <ul className="mt-2 max-h-56 space-y-2 overflow-auto">
      {items.map(item => <li key={item.chunk_id || `${item.doc_id}-${item.chunk_index}`} className="rounded-md border border-border-subtle p-2 text-xs">
        <p className="font-mono text-[11px] text-text-muted">{item.doc_id} · {item.doc_version || '版本未知'} · 切片 {item.chunk_index} · 相关度 {item.score}</p>
        <p className="mt-1 line-clamp-2 text-text-secondary">{item.snippet}</p>
        <button type="button" className="mt-1.5 text-accent hover:underline" onClick={() => onInsert({ kb_id: kbId, doc_id: item.doc_id, doc_version: item.doc_version || '', quote: item.content })}>填入证据锚点</button>
      </li>)}
    </ul>}
  </div>
}
