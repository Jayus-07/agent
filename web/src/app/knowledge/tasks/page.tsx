'use client'

import { Cpu, Database, FileText, CheckCircle2, Clock, AlertCircle } from 'lucide-react'

const TASKS = [
  { id: 't1', type: '上传', doc: '商品运营规则.md', status: 'done', elapsed: '12s', startTime: '10:30', detail: '35 chunks 已入库' },
  { id: 't2', type: '索引', doc: '供应链管理手册.pdf', status: 'done', elapsed: '45s', startTime: '09:15', detail: '50 chunks, 512 维向量' },
  { id: 't3', type: 'Embedding', doc: 'FBA发货SOP.docx', status: 'done', elapsed: '28s', startTime: '昨天', detail: 'bge-small-zh-v1.5, 28 chunks' },
  { id: 't4', type: '解析', doc: '广告投放策略.pdf', status: 'running', elapsed: '—', startTime: '刚刚', detail: 'PDF 文本提取中...' },
  { id: 't5', type: '删除', doc: '旧版FAQ.txt', status: 'done', elapsed: '2s', startTime: '昨天', detail: '已清理 8 chunks + 向量' },
]

const INDEX_INFO = {
  embeddingModel: 'BAAI/bge-small-zh-v1.5',
  dimension: 512,
  chunkSize: 500,
  chunkOverlap: 50,
  vectorDb: 'Chroma',
  collection: 'default',
  vectorCount: 47,
  storageSize: '12.4 MB',
  embeddingVersion: '1.0',
}

export default function TasksPage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">索引 & 任务</h1>
          <p className="text-xs text-text-muted mt-1">Embedding 配置 + 后台任务状态</p>
        </div>

        {/* Index Info */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-5 mb-6">
          <div className="flex items-center gap-2 mb-4">
            <Cpu size={14} className="text-accent" />
            <h2 className="text-sm font-semibold text-text-primary">索引信息</h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            {[
              { label: 'Embedding 模型', value: INDEX_INFO.embeddingModel },
              { label: '维度', value: String(INDEX_INFO.dimension) },
              { label: 'Chunk Size', value: String(INDEX_INFO.chunkSize) },
              { label: 'Chunk Overlap', value: String(INDEX_INFO.chunkOverlap) },
              { label: '向量数据库', value: INDEX_INFO.vectorDb },
              { label: 'Collection', value: INDEX_INFO.collection },
              { label: '向量数量', value: String(INDEX_INFO.vectorCount) },
              { label: '存储大小', value: INDEX_INFO.storageSize },
            ].map(i => (
              <div key={i.label}><span className="text-text-muted">{i.label}</span><div className="text-text-primary font-medium mt-0.5">{i.value}</div></div>
            ))}
          </div>
        </div>

        {/* Background Tasks */}
        <div className="bg-surface-base rounded-xl border border-border-subtle p-5">
          <div className="flex items-center gap-2 mb-4">
            <Database size={14} className="text-accent" />
            <h2 className="text-sm font-semibold text-text-primary">后台任务</h2>
          </div>
          <div className="space-y-2">
            {TASKS.map(t => (
              <div key={t.id} className="flex items-center gap-3 rounded-lg bg-surface-elevated px-4 py-2.5 text-xs">
                {t.status === 'done' ? <CheckCircle2 size={14} className="text-green-500 shrink-0" />
                  : t.status === 'running' ? <Clock size={14} className="text-amber-500 animate-pulse shrink-0" />
                  : <AlertCircle size={14} className="text-red-500 shrink-0" />}
                <span className="bg-surface-base px-1.5 py-0.5 rounded text-text-muted font-medium w-16 text-center">{t.type}</span>
                <FileText size={12} className="text-text-muted" />
                <span className="text-text-primary flex-1">{t.doc}</span>
                <span className="text-text-muted">{t.elapsed}</span>
                <span className="text-text-muted">{t.startTime}</span>
                <span className="text-text-muted hidden md:inline">{t.detail}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
