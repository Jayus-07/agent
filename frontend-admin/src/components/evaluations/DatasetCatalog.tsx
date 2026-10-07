'use client'

import { Database, ChevronRight, Hash } from 'lucide-react'
import type { DatasetCatalogItem } from '@/api/evaluation'

interface DatasetCatalogProps {
  items: DatasetCatalogItem[]
  selectedId?: string
  onSelect?: (item: DatasetCatalogItem) => void
}

export default function DatasetCatalog({ items, selectedId, onSelect }: DatasetCatalogProps) {
  // 探针集排序沉底，常规评测集优先展示
  const sorted = [...items].sort((a, b) => (a.kind === 'probe' ? 1 : 0) - (b.kind === 'probe' ? 1 : 0))
  return (
    <section className="rounded-xl border border-border-subtle bg-surface-base" aria-label="评测集目录">
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <div><h2 className="text-sm font-semibold text-text-primary">评测集目录</h2><p className="mt-0.5 text-[11px] text-text-muted">canonical 数据集与最新评测范围</p></div>
        <Database size={15} className="text-text-muted" />
      </div>
      {sorted.length === 0 ? <div className="px-4 py-8 text-center text-xs text-text-muted">暂无 canonical 评测集</div> : (
        <div className="divide-y divide-border-subtle">
          {sorted.map(item => (
            <button key={item.dataset_id} type="button" onClick={() => onSelect?.(item)} className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-black/[0.02] ${selectedId === item.dataset_id ? 'bg-accent/[0.04]' : ''}`}>
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent"><Database size={15} /></div>
              <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="text-xs font-medium text-text-primary">{item.module}</span>{item.kind === 'probe' && <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">探针 · 需 live</span>}<span className="font-mono text-[10px] text-text-muted">v{item.dataset_version}</span></div><div className="mt-1 flex flex-wrap gap-x-3 text-[10px] text-text-muted"><span>{item.case_count} cases</span><span>{item.kb_id || 'KB —'}</span><span>{item.fixture_set || 'fixture —'}</span></div></div>
              <div className="hidden items-center gap-1 text-[10px] text-text-muted md:flex"><Hash size={10} /> {item.content_hash.slice(0, 10)}</div><ChevronRight size={14} className="shrink-0 text-text-muted" />
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
