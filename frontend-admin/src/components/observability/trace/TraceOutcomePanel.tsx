import type { TraceRecord } from '@/types/trace'
import { countRetryEvents, getBusinessOutcome, getRunStatus, getRunStatusLabel, getRunStatusTone, safeDiagnosticCode, safeRuntimeLabel } from '@/lib/observability/runtimeStatus'

interface Props {
  trace: TraceRecord
}

function spanStatusLabel(status: string): string {
  const known: Record<string, string> = {
    success: '成功',
    error: '错误',
    timeout: '超时',
    cancelled: '已取消',
    rejected: '已拒绝',
    partial: '部分成功',
    running: '运行中',
    skipped: '已跳过',
    leaked: '未正常收口',
  }
  return known[status] ?? '状态未知'
}

export default function TraceOutcomePanel({ trace }: Props) {
  const businessOutcome = getBusinessOutcome(trace)
  const runStatus = getRunStatus(trace)
  const toolSpans = trace.spans.filter((span) => span.type === 'tool_call')
  const retries = countRetryEvents(trace)
  const rawErrorCode = trace.error?.code
  const errorCode = safeDiagnosticCode(rawErrorCode)
  const hasErrorCode = typeof rawErrorCode === 'string' && rawErrorCode.trim().length > 0
  const graphDegraded = trace.graph?.degradation_triggered

  return (
    <section className="rounded-md border border-border-subtle bg-white">
      <div className="border-b border-border-subtle px-4 py-3">
        <h2 className="font-semibold text-text-primary">运行结果与状态口径</h2>
        <p className="mt-1 text-xs text-text-secondary">业务结果、Run 聚合终态、HTTP 响应和 Tool 执行状态分别读取，不用 HTTP 200 推断业务成功。</p>
      </div>
      <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded border border-border-subtle p-3">
          <span className="block text-xs text-text-secondary">业务结果</span>
          <strong className="mt-1 block text-sm text-text-primary">
            {businessOutcome === 'failed' ? '失败' : businessOutcome === 'degraded' ? '降级' : businessOutcome === 'partial' ? '部分成功' : '未采集'}
          </strong>
          <span className="mt-1 block text-xs text-text-muted">来源：metadata.business_outcome</span>
        </div>
        <div className="rounded border border-border-subtle p-3">
          <span className="block text-xs text-text-secondary">Run 终态</span>
          <span className={`mt-1 inline-flex rounded border px-2 py-0.5 text-xs ${getRunStatusTone(runStatus)}`}>{getRunStatusLabel(runStatus)}</span>
          <span className="mt-1 block text-xs text-text-muted">来源：Trace DTO status；显式业务失败/降级优先显示</span>
        </div>
        <div className="rounded border border-amber-200 bg-amber-50 p-3">
          <span className="block text-xs text-amber-900">HTTP 响应</span>
          <strong className="mt-1 block text-sm text-amber-950">未提供 · BLOCKED</strong>
          <span className="mt-1 block text-xs text-amber-900">现有 Span DTO 未包含 HTTP status code</span>
        </div>
        <div className="rounded border border-border-subtle p-3">
          <span className="block text-xs text-text-secondary">重试 / 错误码</span>
          <strong className="mt-1 block text-sm text-text-primary">{retries === null ? '重试次数未记录' : `重试 ${retries} 次`}</strong>
          <span className="mt-1 block break-all font-mono text-xs text-text-secondary">{errorCode || (hasErrorCode ? '错误码文本已隐藏' : '错误码未记录')}</span>
        </div>
      </div>

      <div className="border-t border-border-subtle px-4 py-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-text-primary">Tool 执行状态</h3>
          <span className="text-xs text-text-secondary">{toolSpans.length} 个已记录 Tool Span</span>
        </div>
        {toolSpans.length === 0 ? (
          <p className="mt-2 text-sm text-text-secondary">本次 Trace 未记录 Tool Span。</p>
        ) : (
          <ul className="mt-2 divide-y divide-border-subtle">
            {toolSpans.map((span) => (
              <li key={span.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="min-w-0 break-all text-text-primary">{safeRuntimeLabel(span.name, 'Tool 执行步骤')}</span>
                <span className="rounded border border-border-subtle bg-bg-elevated px-2 py-0.5 text-xs text-text-secondary">{spanStatusLabel(span.status)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-border-subtle px-4 py-3 text-sm">
        <span><span className="text-text-secondary">图降级标记：</span><strong className="text-text-primary">{graphDegraded === true ? '已触发' : graphDegraded === false ? '未触发' : businessOutcome === 'degraded' ? '已降级' : '未提供'}</strong></span>
        <span><span className="text-text-secondary">服务端图拓扑：</span><strong className="text-text-primary">{trace.graph ? '已提供' : '未提供'}</strong></span>
      </div>
    </section>
  )
}
