'use client'

import { useState } from 'react'
import { FileText, ClipboardList, FolderKanban, BarChart3, User, File } from 'lucide-react'
import type { Source } from '@/lib/types'
import { formatSourcePages } from '@/lib/ragAnswer'
import SourcePreviewDrawer from './SourcePreviewDrawer'

const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  manual: ClipboardList,
  policy: ClipboardList,
  project: FolderKanban,
  report: BarChart3,
  resume: User,
  general: FileText,
}

const colorMap: Record<string, string> = {
  manual: 'bg-amber-100 text-amber-700',
  policy: 'bg-red-100 text-red-700',
  project: 'bg-blue-100 text-blue-700',
  report: 'bg-green-100 text-green-700',
  resume: 'bg-purple-100 text-purple-700',
  general: 'bg-gray-100 text-gray-600',
}

export default function SourceCard({ sources }: { sources: Source[] }) {
  const [preview, setPreview] = useState<Source | null>(null)
  if (!sources || sources.length === 0) return null

  return (
    <div className="mt-2.5 rounded-lg border border-border-subtle bg-black/[0.02] px-3 py-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-text-muted">
        <FileText className="h-3.5 w-3.5" />
        参考来源 ({sources.length} 份文档)
      </div>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s, i) => {
          const Icon = iconMap[s.doc_type] || File
          const colorClass = colorMap[s.doc_type] || colorMap.general
          // 原文定位（P0）：页码文案 + 章节/部门进悬浮提示
          const pagesLabel = formatSourcePages(s.pages)
          const titleParts = [
            s.type_label || s.doc_type,
            s.department ? `部门: ${s.department}` : '',
            s.section ? `章节: ${s.section}` : '',
            pagesLabel,
            s.score != null ? `相关度: ${s.score}` : '',
          ].filter(Boolean)
          // 原文预览（P1）：有 doc_id 且有页码（PDF）→ chip 可点开快照抽屉
          const previewable = Boolean(s.doc_id && s.pages && s.pages.length > 0)
          return (
            <button
              type="button"
              key={i}
              disabled={!previewable}
              onClick={() => setPreview(s)}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-left ${colorClass} ${
                previewable ? 'cursor-pointer hover:ring-2 hover:ring-black/10' : 'cursor-default'
              }`}
              title={previewable ? `${titleParts.join(' — ')}（点击查看原文）` : titleParts.join(' — ')}
            >
              <Icon className="h-3 w-3" />
              <span className="max-w-[200px] truncate">{s.filename}</span>
              {pagesLabel && (
                <span className="rounded bg-white/70 px-1 text-[10px]">{pagesLabel}</span>
              )}
              {s.department && (
                <span className="rounded bg-white/70 px-1 text-[10px]">{s.department}</span>
              )}
              {s.score != null && (
                <span className="ml-0.5 rounded bg-white/70 px-1 text-[10px] font-mono">
                  {s.score}
                </span>
              )}
            </button>
          )
        })}
      </div>
      {preview && preview.doc_id && (
        <SourcePreviewDrawer
          docId={preview.doc_id}
          filename={preview.filename}
          pages={preview.pages}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}
