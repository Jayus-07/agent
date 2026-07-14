'use client'

import { useState, useRef, useEffect } from 'react'
import { FileText, Search, Trash2, RefreshCw, Grid3X3, Loader2, ChevronLeft, ChevronRight } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useDocuments } from '@/hooks/useKnowledge'
import { knowledgeService } from '@/services/knowledge'
import UploadDialog from '@/components/knowledge/UploadDialog'

const TYPE_ICONS: Record<string, string> = { pdf: '📄', md: '📝', docx: '📃', txt: '📋', unknown: '📎' }

// 统一状态徽章映射
const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  uploading:  { label: '上传中', className: 'bg-blue-50 text-blue-600' },
  parsing:    { label: '解析中', className: 'bg-purple-50 text-purple-600' },
  embedding:  { label: '向量化', className: 'bg-amber-50 text-amber-600' },
  active:     { label: '活跃',   className: 'bg-green-50 text-green-600' },
  failed:     { label: '失败',   className: 'bg-red-50 text-red-600' },
  deleted:    { label: '已删除', className: 'bg-gray-50 text-gray-400' },
  // 兼容旧状态
  done:       { label: '已完成', className: 'bg-green-50 text-green-600' },
  processing: { label: '处理中', className: 'bg-amber-50 text-amber-600' },
  error:      { label: '失败',   className: 'bg-red-50 text-red-600' },
  indexing:   { label: '索引中', className: 'bg-amber-50 text-amber-600' },
}

export default function DocumentsPage() {
  const router = useRouter()
  const { documents, loading, total, page, pageSize, keyword, setKeyword, setPage, refresh } = useDocuments()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [reindexing, setReindexing] = useState<Set<string>>(new Set())
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  // 防抖搜索
  const handleSearch = (value: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setKeyword(value), 300)
  }

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [])

  const handleDelete = async (id: string) => {
    await knowledgeService.deleteDocument(id)
    refresh()
  }

  const handleReindex = async (id: string) => {
    setReindexing(prev => new Set(prev).add(id))
    try {
      await knowledgeService.reindexDocument(id)
      refresh()
    } finally {
      setReindexing(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }

  const handleStopPropagation = (e: React.MouseEvent, fn: () => void) => {
    e.stopPropagation()
    fn()
  }

  const totalPages = Math.ceil(total / pageSize)

  return (
    <>
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">文档管理</h1>
          <p className="text-xs text-text-muted mt-1">管理 RAG 知识库文档，支持 PDF、TXT、Markdown、DOCX</p>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-3 mb-4">
          <div className="flex items-center gap-2 flex-1 bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
            <Search size={14} className="text-text-muted" />
            <input
              placeholder="搜索文档..."
              className="bg-transparent outline-none text-xs text-text-primary flex-1"
              defaultValue={keyword}
              onChange={e => handleSearch(e.target.value)}
            />
            {keyword && (
              <button onClick={() => handleSearch('')} className="text-text-muted hover:text-text-primary text-[10px]">✕ 清除</button>
            )}
          </div>
          <button onClick={() => setUploadOpen(true)} className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">+ 上传文档</button>
        </div>

        {/* Table */}
        {loading && <div className="bg-surface-base rounded-xl border border-border-subtle p-3 text-center text-xs text-text-muted animate-pulse mb-2">加载中...</div>}
        <div className="bg-surface-base rounded-xl border border-border-subtle overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="border-b border-border-subtle bg-surface-elevated text-text-muted">
              {['文件名', '类型', '大小', 'Chunks', '状态', '索引时间', '操作'].map(h => <th key={h} className="text-left px-4 py-2.5 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>
              {documents.map(d => (
                <tr key={d.id} className="border-b border-border-subtle hover:bg-surface-hover transition-colors cursor-pointer"
                  onClick={() => router.push(`/knowledge/documents/${d.id}`)}>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <FileText size={14} className="text-text-muted" />
                      <span className="text-text-primary hover:text-accent transition-colors truncate max-w-[240px]">{d.name}</span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-text-muted">{TYPE_ICONS[d.type] || TYPE_ICONS.unknown} {d.type.toUpperCase()}</td>
                  <td className="px-4 py-2.5 text-text-muted">{d.size < 1024 ? d.size + ' B' : (d.size / 1024).toFixed(1) + ' KB'}</td>
                  <td className="px-4 py-2.5 text-text-muted">{d.chunk_count ?? d.chunks ?? 0}</td>
                  <td className="px-4 py-2.5"><StatusBadge status={d.status} /></td>
                  <td className="px-4 py-2.5 text-text-muted">{(d.last_indexed || d.created_at || '').slice(0, 10)}</td>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1">
                      <button onClick={(e) => handleStopPropagation(e, () => router.push(`/knowledge/chunks?docId=${d.id}`))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors" title="查看Chunks"><Grid3X3 size={13} /></button>
                      <button onClick={(e) => handleStopPropagation(e, () => handleReindex(d.id))} disabled={reindexing.has(d.id)} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-accent transition-colors disabled:opacity-50" title="重新解析">
                        {reindexing.has(d.id) ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                      </button>
                      <button onClick={(e) => handleStopPropagation(e, () => handleDelete(d.id))} className="p-1 rounded hover:bg-black/5 text-text-muted hover:text-red-500 transition-colors" title="删除"><Trash2 size={13} /></button>
                    </div>
                  </td>
                </tr>
              ))}
              {!loading && documents.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-8 text-center text-text-muted">暂无文档</td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between mt-3 text-xs text-text-muted">
            <span>共 {total} 篇文档</span>
            <div className="flex items-center gap-2">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronLeft size={14} /></button>
              <span>{page} / {totalPages}</span>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1 rounded hover:bg-surface-base disabled:opacity-30 transition-colors"><ChevronRight size={14} /></button>
            </div>
          </div>
        )}
      </div>
    </div>
    <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} onSuccess={refresh} />
    </>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.active
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${config.className}`}>{config.label}</span>
}
