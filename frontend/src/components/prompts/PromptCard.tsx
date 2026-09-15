import { useRouter } from 'next/navigation'
import type { PromptListItem } from '@/api/prompts'
import type { PromptGroup } from '@/config/promptGroups'
import StatusBadge from './StatusBadge'

interface PromptCardProps {
  prompt: PromptListItem
  group: PromptGroup
}

export default function PromptCard({ prompt, group }: PromptCardProps) {
  const router = useRouter()
  const GroupIcon = group.icon

  return (
    <div
      onClick={() => router.push(`/prompts/${encodeURIComponent(prompt.key)}`)}
      className="bg-surface-elevated rounded-xl border border-border-subtle p-4 hover:shadow-card hover:border-accent/30 cursor-pointer transition-all group"
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-accent/10 text-accent">
            <GroupIcon size={14} />
          </div>
          <div>
            <p className="text-sm font-medium text-text-primary group-hover:text-accent transition-colors">
              {prompt.name}
            </p>
            <p className="text-[10px] font-mono text-text-muted mt-0.5">{prompt.key}</p>
          </div>
        </div>
        <StatusBadge status={prompt.latest_version_status} />
      </div>

      <div className="flex items-center gap-4 text-[11px] text-text-muted">
        <span>
          版本 <span className="font-mono text-text-secondary">v{prompt.latest_version_number ?? prompt.active_version ?? '-'}</span>
        </span>
        {prompt.latest_version_updated_at && (
          <span>{new Date(prompt.latest_version_updated_at).toLocaleDateString('zh-CN')}</span>
        )}
        <span>{prompt.variable_count} 变量</span>
      </div>
    </div>
  )
}
