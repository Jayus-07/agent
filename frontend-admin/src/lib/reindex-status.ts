export type ReindexTerminalState = 'success' | 'failure' | null

/**
 * 判断管理端是否可以停止轮询重索引。
 * 进度镜像按文档保存，新任务排队或执行时可能仍留着上一次的终态。
 */
export function getReindexTerminalState(input: {
  taskStatus?: string | null
  progressStage?: string | null
  progressUpdatedAt?: string | number | null
  submittedAtMs: number
}): ReindexTerminalState {
  const status = String(input.taskStatus ?? '').toUpperCase()
  if (status === 'SUCCESS') return 'success'
  if (status === 'FAILED' || status === 'CANCELLED') return 'failure'
  if (status) return null

  const updatedAtMs = Number(input.progressUpdatedAt) * 1000
  const progressIsCurrent = Number.isFinite(updatedAtMs)
    && updatedAtMs >= input.submittedAtMs - 1000
  if (!progressIsCurrent) return null

  const stage = String(input.progressStage ?? '').toLowerCase()
  if (stage === 'done' || stage === 'duplicate') return 'success'
  if (stage === 'error') return 'failure'
  return null
}
