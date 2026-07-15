'use client'

import { FileText, ClipboardList, FolderKanban, BarChart3, User, File } from 'lucide-react'
import type { Source } from '@/lib/types'

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
  if (!sources || sources.length === 0) return null

  return (
    <div className="mb-3 rounded-lg border border-[#3f3f3f] bg-[#1a1a1a] px-3 py-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-[#8e8e8e]">
        <FileText className="h-3.5 w-3.5" />
        参考来源 ({sources.length} 份文档)
      </div>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s, i) => {
          const Icon = iconMap[s.doc_type] || File
          const colorClass = colorMap[s.doc_type] || colorMap.general
          return (
            <div
              key={i}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs ${colorClass}`}
              title={`${s.type_label || s.doc_type}${s.score != null ? ` — 相关度: ${s.score}` : ''}`}
            >
              <Icon className="h-3 w-3" />
              <span className="max-w-[200px] truncate">{s.filename}</span>
              {s.score != null && (
                <span className="ml-0.5 rounded bg-white/50 px-1 text-[10px] font-mono">
                  {s.score}
                </span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
