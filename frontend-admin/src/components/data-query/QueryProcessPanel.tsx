import type { SSEStreamEvent } from '@/lib/types'

const PHASE_LABELS: Record<string, string> = {
  understanding: '需求理解',
  table_routing: '选择数据表',
  sql_generation: '生成 SQL',
  sql_validation: '安全校验',
  tool_start: '调用查询工具',
  tool_result: '查询工具返回',
}

export function queryPhaseLabel(phase: unknown): string {
  return typeof phase === 'string' ? (PHASE_LABELS[phase] ?? phase) : '执行进度'
}

function eventMessage(event: SSEStreamEvent): string | null {
  if (event.event !== 'log' && event.event !== 'status') return null
  return event.data.message || null
}

/** 管理端 NL2SQL 的可视化过程：让用户知道系统正在理解什么、调用了什么。 */
export default function QueryProcessPanel({ events }: { events: SSEStreamEvent[] }) {
  const progress = events.filter(event => event.event === 'log' || event.event === 'status')
  if (progress.length === 0) return null

  return (
    <div className="mt-4 border border-border-subtle rounded-lg bg-surface-elevated px-3 py-2.5">
      <div className="text-xs font-medium text-text-primary mb-2">查询过程</div>
      <div className="space-y-1.5">
        {progress.map((event, index) => {
          const payload = event.event === 'log' ? event.data.payload : undefined
          const phase = payload?.phase ?? (event.event === 'status' ? event.data.node : undefined)
          const tool = payload?.tool
          return (
            <div key={`${event.event}-${event.data.ts}-${index}`} className="flex items-center gap-2 text-xs">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent shrink-0" />
              <span className="text-text-muted shrink-0">{queryPhaseLabel(phase)}</span>
              <span className="text-text-secondary truncate">{eventMessage(event) ?? '处理中…'}</span>
              {typeof tool === 'string' && (
                <span className="ml-auto shrink-0 px-1.5 py-0.5 rounded bg-accent/10 text-accent font-mono text-[10px]">{tool}</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
