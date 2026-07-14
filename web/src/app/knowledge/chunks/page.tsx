'use client'

import { Suspense, useState, useEffect } from 'react'
import { useSearchParams, useRouter } from 'next/navigation'
import { Search, Beaker } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'
import { KB_CHUNKS } from '@/services/mock/knowledge'

interface ChunkItem { id: string; content: string; metadata: Record<string,unknown>; token_count: number }

function ChunksContent() {
  const [search, setSearch] = useState('')
  const [chunks, setChunks] = useState<any[]>(KB_CHUNKS)
  const params = useSearchParams()
  const router = useRouter()
  const docId = params.get('docId')

  useEffect(() => {
    const id = docId || ''
    if (!id) return
    knowledgeService.getChunks(id)
      .then(d => { if (d.chunks?.length) setChunks(d.chunks.map((c: ChunkItem) => ({ id: c.id, docId: id, content: c.content, tokenCount: c.token_count, metadata: c.metadata }))) })
      .catch(() => {})
  }, [docId])

  const filtered = (search ? chunks.filter((c: any) => (c.content||'').includes(search) || c.id.includes(search)) : docId ? chunks.filter((c: any) => c.docId === docId) : chunks)

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">Chunk 查看</h1>
          <p className="text-xs text-text-muted mt-1">浏览文档切片结果，查看 token 数、metadata</p>
          {docId && <p className="text-xs text-accent mt-1">筛选: 文档 {docId} · {filtered.length} 个 Chunk</p>}
        </div>
        <div className="flex items-center gap-2 mb-4 bg-surface-base rounded-lg border border-border-subtle px-3 py-2 max-w-sm">
          <Search size={14} className="text-text-muted" />
          <input placeholder="搜索 Chunk 内容..." value={search} onChange={e => setSearch(e.target.value)} className="bg-transparent outline-none text-xs text-text-primary flex-1" />
        </div>
        <div className="grid gap-3">
          {filtered.map(c => (
            <div key={c.id} className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card transition-shadow duration-200">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono bg-surface-elevated px-2 py-0.5 rounded text-text-muted">{c.id}</span>
                  <span className="text-[10px] text-text-muted">doc: {c.docId}</span>
                  <span className="text-[10px] text-text-muted">tokens: {c.tokenCount}</span>
                </div>
                <button onClick={() => router.push('/knowledge/playground')}
                  className="flex items-center gap-1 text-[10px] text-accent hover:underline"><Beaker size={11} /> 检索测试</button>
              </div>
              <p className="text-sm text-text-primary leading-relaxed mb-2">{c.content}</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(c.metadata || {}).map(([k, v]: [string, any]) => (
                  <span key={k} className="text-[10px] bg-accent/5 text-accent px-2 py-0.5 rounded-full">{k}: {v}</span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function ChunksPage() {
  return <Suspense><ChunksContent /></Suspense>
}
