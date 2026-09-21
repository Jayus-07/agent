/** 展示格式化与徽标配色（纯函数，从 ProvidersTab.tsx B4 拆分迁出）。 */
import type { ProbeMode, ProbeResponse } from '@/api/modelConfig'
import type { ModelKind } from '@/types/modelConfig'
import type { ProbeStatus } from './draft'

export function modelKindShortLabel(kind: ModelKind): string {
  if (kind === 'embedding') return '向量'
  if (kind === 'rerank') return '重排'
  if (kind === 'vision') return '视觉'
  if (kind === 'speech') return '语音'
  return '文本'
}

export function modelKindBadgeClass(kind: ModelKind): string {
  if (kind === 'embedding') return 'bg-indigo-50 text-indigo-700'
  if (kind === 'rerank') return 'bg-amber-50 text-amber-700'
  if (kind === 'vision') return 'bg-sky-50 text-sky-700'
  if (kind === 'speech') return 'bg-rose-50 text-rose-700'
  return 'bg-slate-100 text-text-muted'
}

export function probeStatusLabel(status: ProbeStatus): string {
  if (status === 'pass') return '通过'
  if (status === 'fail_degraded') return '降级跳过'
  if (status === 'skip') return '跳过'
  return '失败'
}

export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatLiveElapsed(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} 秒`
}

export function probeModeLabel(mode: ProbeMode): string {
  return mode === 'full' ? '完整测试' : '快速测试'
}

export function resultElapsedMs(result: ProbeResponse): number {
  return result.steps.reduce((total, step) => total + (step.elapsedMs ?? 0), 0)
}
