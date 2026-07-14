// Mock — Observability
// 迁移自 lib/mock-observability.ts

export interface AgentTrace { id: string; question: string; totalElapsed: number; nodes: { name: string; status: string; elapsed: number }[]; status: string }
export interface LlmCall { id: string; model: string; node: string; promptTokens: number; completionTokens: number; elapsed: number; status: string; timestamp: string }
export interface RagMetric { totalSearches: number; avgElapsed: number; avgScore: number; rerankCount: number; tokenUsage: number; topQueries: { query: string; count: number }[] }
export interface AnalysisTask { id: string; question: string; status: string; nodes: string[]; elapsed: string; createdAt: string }
export interface AgentReport { id: string; title: string; type: string; status: string; createdAt: string; summary: string }

export const AGENT_TRACES: AgentTrace[] = [
  { id: 'tr1', question: '分析最近女装销量下降原因', totalElapsed: 8.6, status: 'success',
    nodes: [{ name: 'Planner', status: 'done', elapsed: 1.2 }, { name: 'SQL Agent', status: 'done', elapsed: 2.5 }, { name: 'RAG Agent', status: 'done', elapsed: 1.8 }, { name: 'Reporter', status: 'done', elapsed: 3.1 }] },
  { id: 'tr2', question: '查询库存不足的 SKU', totalElapsed: 3.2, status: 'success',
    nodes: [{ name: 'Planner', status: 'done', elapsed: 0.8 }, { name: 'SQL Agent', status: 'done', elapsed: 1.5 }, { name: 'Reporter', status: 'done', elapsed: 0.9 }] },
  { id: 'tr3', question: '对比各渠道销售额排名', totalElapsed: 5.8, status: 'success',
    nodes: [{ name: 'Planner', status: 'done', elapsed: 1.0 }, { name: 'SQL Agent', status: 'done', elapsed: 3.2 }, { name: 'Reporter', status: 'done', elapsed: 1.6 }] },
]

export const LLM_CALLS: LlmCall[] = [
  { id: 'llm1', model: 'qwen2.5:3b', node: 'Planner', promptTokens: 450, completionTokens: 120, elapsed: 1.2, status: 'success', timestamp: '2026-07-14 10:30' },
  { id: 'llm2', model: 'qwen2.5:3b', node: 'SQL Gen', promptTokens: 820, completionTokens: 95, elapsed: 2.1, status: 'success', timestamp: '2026-07-14 10:30' },
  { id: 'llm3', model: 'qwen2.5:3b', node: 'RAG Chain', promptTokens: 1200, completionTokens: 350, elapsed: 3.5, status: 'success', timestamp: '2026-07-14 10:30' },
  { id: 'llm4', model: 'qwen2.5:3b', node: 'Reporter', promptTokens: 680, completionTokens: 520, elapsed: 2.8, status: 'success', timestamp: '2026-07-14 10:30' },
  { id: 'llm5', model: 'deepseek-chat', node: 'SQL Gen', promptTokens: 550, completionTokens: 110, elapsed: 1.5, status: 'error', timestamp: '2026-07-14 10:25' },
]

export const RAG_METRICS: RagMetric = {
  totalSearches: 12580, avgElapsed: 0.23, avgScore: 0.72, rerankCount: 8450, tokenUsage: 2850000,
  topQueries: [{ query: 'FBA发货流程', count: 450 }, { query: '库存补货策略', count: 380 }, { query: 'Listing优化', count: 320 }],
}

export const AGENT_REPORTS: AgentReport[] = [
  { id: 'r1', title: '库存健康报告', type: 'inventory_health', status: 'done', createdAt: '2026-07-14', summary: '共检查12个SKU-仓库，发现3条预警。' },
  { id: 'r2', title: '供应商质量评估报告', type: 'supplier_quality', status: 'done', createdAt: '2026-07-13', summary: 'SUP-006质量最佳(不良率0.5%)，SUP-009需触发质量审查。' },
  { id: 'r3', title: '销售日报 - 2026-07-13', type: 'daily_sales', status: 'done', createdAt: '2026-07-13', summary: '当日营收¥4,956。Amazon渠道占比57%。' },
]
