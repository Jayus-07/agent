'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, FileText, Clock } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'

interface PendingDoc {
  id: string
  doc_id: string
  file_name: string
  doc_type: string
  business_domain: string
  confidence: number
  kb_id: string
  summary?: string
  quality_score?: number
  updated_at: string
}

export default function PendingReviewPage() {
  const [items, setItems] = useState<PendingDoc[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [acting, setActing] = useState<string | null>(null)
  const [error, setError] = useState('')

  const refresh = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await knowledgeService.getPendingDocs({ page: 1, page_size: 50 })
      setItems(res.items || [])
      setTotal(res.total || 0)
    } catch (e) {
      setError('加载失败: ' + String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const handleApprove = async (doc_id: string) => {
    if (!confirm(`确认批准文档 ${doc_id}？激活后即可被 RAG 检索。`)) return
    setActing(doc_id)
    try {
      const res = await knowledgeService.approvePendingDoc(doc_id)
      if (res.ok) {
        await refresh()
      } else {
        alert('批准失败: ' + (res.error || '未知错误'))
      }
    } finally {
      setActing(null)
    }
  }

  const handleReject = async (doc_id: string) => {
    if (!confirm(`确认拒绝文档 ${doc_id}？此操作将软删除文档。`)) return
    setActing(doc_id)
    try {
      const res = await knowledgeService.rejectPendingDoc(doc_id)
      if (res.ok) {
        await refresh()
      } else {
        alert('拒绝失败: ' + (res.error || '未知错误'))
      }
    } finally {
      setActing(null)
    }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">📋 文档审核队列</h1>
            <p className="text-xs text-text-muted mt-1">
              自动标注置信度低或业务域标注存疑的文档，需人工确认
            </p>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-text-muted">待审核</span>
            <span className="px-3 py-1 rounded-full bg-amber-500/10 text-amber-500 text-sm font-medium">
              {total}
            </span>
            <button
              onClick={refresh}
              className="ml-2 px-3 py-1 text-xs rounded border border-border-subtle hover:bg-surface-elevated"
            >
              🔄 刷新
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-4 px-4 py-3 rounded-lg bg-red-500/10 text-red-500 text-sm">
            {error}
          </div>
        )}

        {loading && (
          <div className="text-center text-text-muted py-12">加载中...</div>
        )}

        {!loading && items.length === 0 && (
          <div className="text-center text-text-muted py-12 bg-surface-base rounded-xl border border-border-subtle">
            <CheckCircle2 className="mx-auto mb-3 text-emerald-500" size={32} />
            <div>无待审核文档</div>
            <div className="text-xs mt-1">所有文档已通过自动标注</div>
          </div>
        )}

        {!loading && items.length > 0 && (
          <div className="space-y-3">
            {items.map((doc) => (
              <div
                key={doc.doc_id}
                className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:border-accent/40 transition-colors"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <FileText size={16} className="text-text-muted shrink-0" />
                      <h3 className="text-sm font-medium text-text-primary truncate">
                        {doc.file_name}
                      </h3>
                      <span className="text-xs text-text-muted shrink-0">
                        [{doc.kb_id}]
                      </span>
                    </div>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
                      <div>
                        <span className="text-text-muted">doc_type: </span>
                        <span className="text-text-primary">{doc.doc_type || '—'}</span>
                      </div>
                      <div>
                        <span className="text-text-muted">business_domain: </span>
                        <span className="text-text-primary">{doc.business_domain || '—'}</span>
                      </div>
                      <div>
                        <span className="text-text-muted">confidence: </span>
                        <span className={`font-medium ${doc.confidence < 0.5 ? 'text-red-500' : doc.confidence < 0.7 ? 'text-amber-500' : 'text-emerald-500'}`}>
                          {(doc.confidence || 0).toFixed(2)}
                        </span>
                      </div>
                      <div>
                        <span className="text-text-muted">quality: </span>
                        <span className="text-text-primary">{(doc.quality_score || 0).toFixed(0)}</span>
                      </div>
                    </div>
                    {doc.summary && (
                      <p className="text-xs text-text-muted mt-2 line-clamp-2">
                        {doc.summary}
                      </p>
                    )}
                    <div className="flex items-center gap-2 mt-2 text-xs text-text-muted">
                      <Clock size={12} />
                      <span>{doc.updated_at}</span>
                    </div>
                  </div>
                  <div className="flex flex-col gap-2 shrink-0">
                    <button
                      onClick={() => handleApprove(doc.doc_id)}
                      disabled={acting === doc.doc_id}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-500/10 text-emerald-500 text-xs hover:bg-emerald-500/20 disabled:opacity-50"
                    >
                      <CheckCircle2 size={14} />
                      批准
                    </button>
                    <button
                      onClick={() => handleReject(doc.doc_id)}
                      disabled={acting === doc.doc_id}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-500 text-xs hover:bg-red-500/20 disabled:opacity-50"
                    >
                      <XCircle size={14} />
                      拒绝
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
