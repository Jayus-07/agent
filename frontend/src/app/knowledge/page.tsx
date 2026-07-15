'use client'

import { BookOpen, FileText, Grid3X3, Cpu, Database, ArrowRight } from 'lucide-react'
import { useRouter } from 'next/navigation'
import { useKnowledgeStats, useDocuments } from '@/hooks/useKnowledge'

export default function KnowledgePage() {
  const stats = useKnowledgeStats()
  const { documents: docs } = useDocuments()
  const router = useRouter()

  const STAT_CARDS = [
  { icon: BookOpen, label: '知识库', value: stats.kb_count, color: '#4E79A7' },
  { icon: FileText, label: '文档', value: stats.doc_count, color: '#F28E2B' },
  { icon: Grid3X3, label: 'Chunks', value: stats.chunk_count, color: '#59A14F' },
  { icon: Cpu, label: 'Embedding 模型', value: stats.embedding_model.split('/').pop()!, color: '#B07AA1' },
]

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">RAG 知识库中心</h1>
          <p className="text-xs text-text-muted mt-1">管理文档、查看解析流程、测试检索效果</p>
        </div>

        {stats.loading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            {[1,2,3,4].map(i => <div key={i} className="bg-surface-base rounded-xl border border-border-subtle p-4 animate-pulse"><div className="h-4 bg-surface-elevated rounded w-16 mb-2" /><div className="h-8 bg-surface-elevated rounded w-12" /></div>)}
          </div>
        ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {STAT_CARDS.map(s => (
            <div key={s.label} className="bg-surface-base rounded-xl border border-border-subtle p-4">
              <div className="flex items-center gap-2 mb-2">
                <s.icon size={15} style={{ color: s.color }} />
                <span className="text-[11px] text-text-muted">{s.label}</span>
              </div>
              <div className="text-xl font-bold text-text-primary">{s.value}</div>
            </div>
          ))}
        </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
          <InfoCard label="向量数据库" value={stats.vector_db} />
          <InfoCard label="Embedding 模型" value={stats.embedding_model} />
          <InfoCard label="Chunk 策略" value="类型感知分块, 500字符" />
        </div>

        <div className="bg-surface-base rounded-xl border border-border-subtle p-4 mb-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-text-primary">最近文档</h2>
            <button onClick={() => router.push('/knowledge/documents')} className="text-xs text-accent hover:underline flex items-center gap-1">查看全部 <ArrowRight size={12} /></button>
          </div>
          <div className="space-y-1">
            {docs.slice(0, 5).map(d => (
              <div key={d.id} className="flex items-center justify-between py-2 px-3 rounded-lg hover:bg-surface-hover transition-colors text-xs">
                <div className="flex items-center gap-2">
                  <span className="text-text-muted font-mono">{d.type.toUpperCase()}</span>
                  <span className="text-text-primary">{d.name}</span>
                </div>
                <div className="flex items-center gap-4 text-text-muted">
                  <span>{d.chunk_count ?? d.chunks ?? 0} chunks</span>
                  <StatusBadge status={d.status} />
                  <span>{(d.last_indexed || d.created_at || '').slice(0, 10)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {[{ label: '文档管理', desc: '上传、查看、删除文档', path: '/knowledge/documents' }, { label: '检索测试', desc: '输入问题测试检索效果', path: '/knowledge/playground' }, { label: 'Chunk 查看', desc: '浏览文档切片结果', path: '/knowledge/chunks' }].map(a => (
            <button key={a.path} onClick={() => router.push(a.path)} className="text-left bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card hover:border-accent/20 transition-all duration-200">
              <div className="text-sm font-medium text-text-primary mb-1">{a.label}</div>
              <div className="text-xs text-text-muted">{a.desc}</div>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; className: string }> = {
    uploading:  { label: '上传中', className: 'bg-blue-50 text-blue-600' },
    parsing:    { label: '解析中', className: 'bg-purple-50 text-purple-600' },
    embedding:  { label: '向量化', className: 'bg-amber-50 text-amber-600' },
    active:     { label: '活跃',   className: 'bg-green-50 text-green-600' },
    failed:     { label: '失败',   className: 'bg-red-50 text-red-600' },
    deleted:    { label: '已删除', className: 'bg-gray-50 text-gray-400' },
    done:       { label: '已完成', className: 'bg-green-50 text-green-600' },
    processing: { label: '处理中', className: 'bg-amber-50 text-amber-600' },
    error:      { label: '失败',   className: 'bg-red-50 text-red-600' },
  }
  const c = config[status] || config.active
  return <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${c.className}`}>{c.label}</span>
}
function InfoCard({ label, value }: { label: string; value: string }) {
  return <div className="bg-surface-base rounded-xl border border-border-subtle p-4"><div className="text-[11px] text-text-muted mb-1">{label}</div><div className="text-sm font-medium text-text-primary">{value}</div></div>
}
