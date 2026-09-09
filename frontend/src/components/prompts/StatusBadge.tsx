import { STATUS_META, type PromptStatus } from '@/services/prompts'

export default function StatusBadge({ status }: { status: PromptStatus | string | null }) {
  if (!status || !(status in STATUS_META)) {
    return <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-50 text-gray-400">-</span>
  }
  const meta = STATUS_META[status as PromptStatus]
  return (
    <span className={`inline-flex items-center text-[10px] px-2 py-0.5 rounded-full font-medium ${meta.bg} ${meta.color}`}>
      {meta.label}
    </span>
  )
}
