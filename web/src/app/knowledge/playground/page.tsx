'use client'

import { useState } from 'react'
import { Search, Loader2 } from 'lucide-react'

export default function PlaygroundPage() {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      const res = await fetch('/api/rag/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      })
      const data = await res.json()
      setResult(data)
    } catch { setResult({ error: '检索失败' }) }
    finally { setLoading(false) }
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">检索测试</h1>
          <p className="text-xs text-text-muted mt-1">真实 RAG 检索：Query → CustomRetriever (ChromaDB) → 语义检索结果</p>
        </div>

        <div className="flex items-end gap-3 mb-6">
          <div className="flex-1 bg-surface-base rounded-xl border border-border-subtle focus-within:border-accent/40 focus-within:shadow-input transition-all duration-200 px-4 py-3">
            <textarea value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSearch() } }}
              placeholder="例如：叶菜第二天还能销售吗？" rows={2}
              className="w-full bg-transparent outline-none text-sm text-text-primary placeholder-text-muted resize-none" />
          </div>
          <button onClick={handleSearch} className="shrink-0 px-5 py-3 rounded-xl bg-accent text-white text-sm hover:bg-accent-hover transition-colors flex items-center gap-2">
            <Search size={15} /> 检索
          </button>
        </div>

        {loading && (
          <div className="bg-surface-base rounded-xl border border-border-subtle p-8 text-center">
            <Loader2 size={24} className="animate-spin mx-auto mb-2 text-accent" />
            <p className="text-xs text-text-muted">检索中...</p>
          </div>
        )}

        {result && !loading && (
          <div className="bg-surface-base rounded-xl border border-border-subtle p-4">
            <h3 className="text-xs font-semibold text-text-primary mb-3">
              {result.error ? '检索失败' : `检索完成 · ${result.total || 0} 条结果`}
            </h3>
            {result.error ? (
              <p className="text-xs text-red-500">{result.error}</p>
            ) : (
              <div className="space-y-2">
                {(result.results || []).map((r: any, i: number) => (
                  <div key={i} className="rounded-lg bg-surface-elevated p-3 border border-border-subtle">
                    <div className="flex items-center gap-2 mb-1.5">
                      {r.score != null && (
                        <span className="text-[10px] font-mono bg-accent/10 text-accent px-1.5 py-0.5 rounded">
                          score: {r.score.toFixed(2)}
                        </span>
                      )}
                      <span className="text-[10px] text-text-muted">
                        {r.metadata?.source_file || r.metadata?.file_path?.split('/').pop() || `#${r.index}`}
                      </span>
                    </div>
                    <p className="text-sm text-text-primary leading-relaxed">{r.content}</p>
                  </div>
                ))}
                {(!result.results || result.results.length === 0) && (
                  <p className="text-xs text-text-muted">无结果</p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
