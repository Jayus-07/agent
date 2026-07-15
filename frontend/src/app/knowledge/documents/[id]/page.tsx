'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, FileText, Grid3X3, Clock, Hash, Database, RefreshCw, Loader2, Layers, Cpu } from 'lucide-react'
import { useDocument } from '@/hooks/useKnowledge'
import { knowledgeService } from '@/services/knowledge'

const TYPE_ICONS: Record<string, string> = { pdf: '📄', md: '📝', docx: '📃', txt: '📋', unknown: '📎' }

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  uploading:  { label: '上传中', className: 'bg-blue-50 text-blue-600' },
  parsing:    { label: '解析中', className: 'bg-purple-50 text-purple-600' },
  embedding:  { label: '向量化', className: 'bg-amber-50 text-amber-600' },
  active:     { label: '活跃',   className: 'bg-green-50 text-green-600' },
  failed:     { label: '失败',   className: 'bg-red-50 text-red-600' },
  deleted:    { label: '已删除', className: 'bg-gray-50 text-gray-400' },
}

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const { doc, loading, error, refresh } = useDocument(id)
  const [reindexing, setReindexing] = useState(false)

  const handleReindex = async () => {
    setReindexing(true)
    try {
      await knowledgeService.reindexDocument(id)
      refresh()
    } finally {
      setReindexing(false)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-8 text-center text-sm text-text-muted animate-pulse">加载中...</div>
      </div>
    )
  }

  if (!doc) {
    return (
      <div className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-6 py-8 text-center text-sm text-text-muted">
          {error || '文档不存在'}
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <button onClick={() => router.back()} className="flex items-center gap-1.5 text-xs text-text-muted hover:text-text-primary transition-colors">
            <ArrowLeft size={13} /> 返回文档列表
          </button>
          <button
            onClick={handleReindex}
            disabled={reindexing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-accent/8 text-accent hover:bg-accent/15 transition-colors disabled:opacity-50"
          >
            {reindexing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {reindexing ? '解析中...' : '重新解析'}
          </button>
        </div>

        <div className="flex items-center gap-3 mb-6">
          <div className="w-10 h-10 rounded-xl bg-accent/8 flex items-center justify-center">
            <FileText size={20} className="text-accent" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-text-primary">{doc.name}</h1>
            <p className="text-xs text-text-muted">{doc.path || `/${doc.kb_id}/${doc.name}`}</p>
          </div>
        </div>

        {/* 元数据卡片 */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
          <MetaCard icon={FileText} label="类型" value={`${TYPE_ICONS[doc.type] || TYPE_ICONS.unknown} ${doc.type.toUpperCase()}`} />
          <MetaCard icon={Database} label="大小" value={doc.size < 1024 ? doc.size + ' B' : (doc.size / 1024).toFixed(1) + ' KB'} />
          <MetaCard icon={Grid3X3} label="Chunks" value={String(doc.chunk_count ?? doc.chunks ?? 0)} />
          <MetaCard icon={Clock} label="索引时间" value={(doc.last_indexed || doc.created_at || '').slice(0, 10)} />
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
          <MetaCard icon={Cpu} label="Embedding 模型" value={doc.embedding_model || '—'} />
          <MetaCard icon={Layers} label="Chunk Size" value={doc.chunk_size ? String(doc.chunk_size) : '—'} />
          <MetaCard icon={Layers} label="Overlap" value={doc.overlap ? String(doc.overlap) : '—'} />
          <MetaCard icon={Hash} label="索引版本" value={doc.index_version ? `v${doc.index_version}` : '—'} />
        </div>

        {/* 详情面板 */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-4 mb-8">
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-text-muted">Hash</span>
              <div className="text-text-primary font-mono mt-0.5 truncate text-[11px]">{doc.hash || '—'}</div>
            </div>
            <div>
              <span className="text-text-muted">状态</span>
              <div className="mt-0.5"><StatusBadge status={doc.status} /></div>
            </div>
            <div>
              <span className="text-text-muted">知识库</span>
              <div className="text-text-primary mt-0.5">{doc.kb_id}</div>
            </div>
            <div>
              <span className="text-text-muted">文件路径</span>
              <div className="text-text-primary mt-0.5 truncate text-[11px]">{doc.path || '—'}</div>
            </div>
            <div>
              <span className="text-text-muted">创建时间</span>
              <div className="text-text-primary mt-0.5">{doc.created_at ? doc.created_at.slice(0, 10) : '—'}</div>
            </div>
            <div>
              <span className="text-text-muted">更新时间</span>
              <div className="text-text-primary mt-0.5">{doc.updated_at ? doc.updated_at.slice(0, 10) : '—'}</div>
            </div>
          </div>
        </div>

        {/* Chunks 入口 */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
          <h3 className="text-sm font-semibold text-text-primary mb-3">Chunk 列表</h3>
          <p className="text-xs text-text-muted mb-3">该文档共 {doc.chunk_count ?? doc.chunks ?? 0} 个 Chunk</p>
          <button onClick={() => router.push(`/knowledge/chunks?docId=${doc.id}`)}
            className="px-4 py-2 text-xs rounded-lg bg-accent/8 text-accent hover:bg-accent/15 transition-colors">
            查看 Chunks →
          </button>
        </div>
      </div>
    </div>
  )
}

function MetaCard({ icon: Icon, label, value }: { icon: any; label: string; value: string }) {
  return (
    <div className="bg-surface-base rounded-xl border border-border-subtle p-3">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon size={12} className="text-text-muted" />
        <span className="text-[10px] text-text-muted">{label}</span>
      </div>
      <div className="text-sm font-semibold text-text-primary truncate">{value}</div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.active
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${config.className}`}>{config.label}</span>
}
