// Mock — Dashboard

export interface DashboardKpi {
  label: string; value: string; trend: number; trendLabel: string; icon: string; color: string
}

export interface RecentItem {
  id: string; title: string; type: string; status: string; time: string; detail?: string
}

export interface AgentHealth {
  status: 'healthy' | 'degraded' | 'down'
  ollama: boolean; postgresql: boolean; chromadb: boolean; fastapi: boolean
  uptime: string; lastError?: string
}

export interface SystemEvent {
  id: string; level: 'info' | 'warn' | 'error'; message: string; time: string
}

export const DASHBOARD_KPIS: DashboardKpi[] = [
  { label: '数据资产', value: '7', trend: 40, trendLabel: '较上周', icon: '🗄', color: '#4E79A7' },
  { label: '知识文档', value: '68', trend: 12, trendLabel: '较上周', icon: '📚', color: '#F28E2B' },
  { label: 'AI任务', value: '50', trend: 25, trendLabel: '较上周', icon: '🤖', color: '#59A14F' },
  { label: '系统可用', value: '99.7%', trend: 0.1, trendLabel: '30天', icon: '📈', color: '#B07AA1' },
]

export const RECENT_ANALYSES: RecentItem[] = [
  { id: 'a1', title: '最近30天女装销量下降原因分析', type: '分析', status: '完成', time: '10分钟前', detail: 'SQL×3 RAG×5 报告已生成' },
  { id: 'a2', title: '库存风险巡检', type: '巡检', status: '完成', time: '1小时前', detail: '发现3条预警' },
  { id: 'a3', title: '供应商质量评估', type: '评估', status: '完成', time: '2小时前', detail: '1家供应商触发质量审查' },
]

export const RECENT_UPLOADS: RecentItem[] = [
  { id: 'u1', title: 'product.csv', type: 'CSV', status: '已清洗', time: '30分钟前', detail: '12行 9字段' },
  { id: 'u2', title: '商品运营规则.md', type: 'Markdown', status: '已入库', time: '1小时前', detail: '35 chunks' },
]

export const RECENT_REPORTS: RecentItem[] = [
  { id: 'r1', title: '库存健康报告', type: 'inventory_health', status: '已生成', time: '1小时前' },
  { id: 'r2', title: '供应商质量评估报告', type: 'supplier_quality', status: '已生成', time: '2小时前' },
]

export const AGENT_HEALTH: AgentHealth = {
  status: 'healthy', ollama: true, postgresql: true, chromadb: true, fastapi: true,
  uptime: '3d 12h 45m', lastError: undefined,
}

export const SYSTEM_EVENTS: SystemEvent[] = [
  { id: 'e1', level: 'info', message: '数据采集 Pipeline 完成: products (12行)', time: '10分钟前' },
  { id: 'e2', level: 'warn', message: 'LLM 调用超时重试: qwen2.5:3b, 第2次成功', time: '30分钟前' },
  { id: 'e3', level: 'info', message: 'RAG 文档入库: 商品运营规则.md (35 chunks)', time: '1小时前' },
  { id: 'e4', level: 'info', message: 'Agent 分析完成: 库存风险巡检, 耗时8.6s', time: '1小时前' },
]
