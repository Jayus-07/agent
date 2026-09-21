/** 探测结果详情（分级步骤 + 「去修」动作区；B4 拆分迁出）。 */
import { Wrench } from 'lucide-react'
import type { ReactNode } from 'react'
import type { ProbeResponse } from '@/api/modelConfig'
import { gradeLabel, probeFailureReason, probeStepSummary } from '@/types/modelConfig'
import type { FixHint } from './fixHints'
import { formatElapsed, probeStatusLabel, resultElapsedMs } from './format'

export default function ProbeResultDetails({
  result,
  fixHints,
  onFix,
}: {
  result: ProbeResponse
  /** `grade → 可执行的修复动作`（由 `fixHintsFor` 产出）。不传则不显示「去修」。 */
  fixHints?: Record<string, FixHint[]>
  onFix?: (hint: FixHint) => void
}) {
  const failure = probeFailureReason(result)
  const elapsedMs = resultElapsedMs(result)
  return (
    <div
      data-testid="probe-result-details"
      className={`mt-2 rounded-lg border px-2.5 py-2 text-[10px] ${result.ok ? 'border-emerald-200 bg-emerald-50/70 text-emerald-800' : 'border-red-200 bg-red-50/80 text-red-800'}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-medium">{result.ok ? '探测通过' : failure}</div>
        {elapsedMs > 0 && <span className="shrink-0 font-mono opacity-70">总耗时 {formatElapsed(elapsedMs)}</span>}
      </div>
      <details open={!result.ok} className="mt-1.5">
        <summary className="cursor-pointer select-none text-[10px] font-medium">查看探测详情</summary>
        <ol className="mt-1.5 space-y-1.5">
          {result.steps.map((step) => {
            const hints = step.status === 'fail' ? fixHints?.[step.grade] ?? [] : []
            return (
              <li key={step.grade} className="border-l border-current/20 pl-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{step.grade} · {gradeLabel(step.grade)} · {probeStatusLabel(step.status)}</span>
                  {step.elapsedMs != null && <span className="opacity-70">{step.elapsedMs}ms</span>}
                </div>
                <div className="mt-0.5 break-words" data-testid={`probe-step-summary-${step.grade}`}>{probeStepSummary(step)}</div>
                {hints.length > 0 && onFix && (
                  <div data-testid={`probe-fix-${step.grade}`} className="mt-1 flex flex-wrap items-center gap-1.5">
                    <span className="flex items-center gap-1 opacity-80"><Wrench size={10} />可以直接修：</span>
                    {hints.map((hint) => (
                      <AdvisorButton key={`${hint.kind}:${hint.label}`} disabled={false} onClick={() => onFix(hint)}>
                        {hint.label}
                      </AdvisorButton>
                    ))}
                  </div>
                )}
                {/* 英文原文 / 上游报文只在这里出现，并明确标注用途 —— 用户看不懂的东西
                    不该和结论混在一行里。 */}
                {step.raw && (
                  <details className="mt-0.5 opacity-75">
                    <summary className="cursor-pointer select-none">技术细节（上游原文，排障用）</summary>
                    <div className="mt-0.5 break-words font-mono">{step.raw}</div>
                  </details>
                )}
              </li>
            )
          })}
        </ol>
        {result.suggestion && <div className="mt-1.5 break-words border-t border-current/10 pt-1.5">建议：{result.suggestion}</div>}
      </details>
    </div>
  )
}

export function AdvisorButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-amber-300 bg-white px-2 py-1 text-left font-mono text-[10px] text-amber-900 hover:bg-amber-100 disabled:opacity-50"
    >
      {children}
    </button>
  )
}
