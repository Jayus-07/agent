/** Shared worker node → emoji icon map */
export const WORKER_EMOJI: Record<string, string> = {
  sql_worker: '📊',
  rag_worker: '📚',
  report_worker: '📄',
  planner: '🧠',
}

/** Human-readable labels for worker nodes */
export const WORKER_LABELS: Record<string, string> = {
  sql_worker: 'SQL 数据查询',
  rag_worker: '知识库检索',
  report_worker: '报告生成',
  planner: '任务规划',
}
