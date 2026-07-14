// Mock — Agent Trace + Timeline + Tool Calls + SQL Visualization

export interface TraceNode {
  name: string; status: 'done' | 'running' | 'pending' | 'error'
  elapsed: number; startTime?: string; endTime?: string
  input?: string; output?: string; prompt?: string
}

export interface ToolCall {
  id: string; toolName: string; toolType: 'sql' | 'rag' | 'http' | 'python' | 'browser' | 'search'
  input: string; output: string; duration: number; status: 'success' | 'error'
  timestamp: string
}

export interface SqlViz {
  query: string; explain: string; data: Record<string, any>[]
  chartType: 'line' | 'bar' | 'pie'; chartData: { labels: string[]; values: number[] }
  summary: string
}

export const MOCK_TRACE: TraceNode[] = [
  { name: 'Planner', status: 'done', elapsed: 1.2, startTime: '10:30:00.000', endTime: '10:30:01.200', prompt: '分析最近30天女装销量下降原因', output: 'Step1: SQL查询销量趋势\nStep2: RAG检索竞品策略\nStep3: Report生成分析报告' },
  { name: 'SQL Agent', status: 'done', elapsed: 2.5, startTime: '10:30:01.201', endTime: '10:30:03.700', input: 'SELECT category, SUM(sales) FROM orders WHERE date >= now()-30 GROUP BY category', output: '电子产品: ¥1893\n家居厨房: ¥874\n母婴: ¥775\n宠物用品: ¥757' },
  { name: 'SQL Execute', status: 'done', elapsed: 0.3, startTime: '10:30:03.701', endTime: '10:30:04.000', input: 'Executed on PostgreSQL @ 127.0.0.1:5432/demo', output: '4 rows returned' },
  { name: 'RAG Retrieve', status: 'done', elapsed: 0.8, startTime: '10:30:04.001', endTime: '10:30:04.800', input: '女装销量下降原因 竞品策略', output: 'Top 3 chunks:\n1. 商品运营规则.md (score 0.86)\n2. Listing优化指南.md (score 0.72)\n3. 广告投放策略.pdf (score 0.65)' },
  { name: 'Rerank', status: 'done', elapsed: 0.2, startTime: '10:30:04.801', endTime: '10:30:05.000', input: 'CrossEncoder rerank: bge-reranker-base', output: 'Re-ranked: chunk priority adjusted' },
  { name: 'Reporter', status: 'done', elapsed: 1.6, startTime: '10:30:05.001', endTime: '10:30:06.600', input: 'SQL results + RAG documents', output: '生成《女装销量分析报告》\n- 近30天销量下降20%\n- 竞品降价5-10%\n- 建议优化Listing和广告投放' },
  { name: 'Answer', status: 'done', elapsed: 0.1, startTime: '10:30:06.601', endTime: '10:30:06.700', output: '最终回答生成完毕' },
]

export const MOCK_TOOL_CALLS: ToolCall[] = [
  { id: 't1', toolName: 'sql_query', toolType: 'sql', input: 'SELECT category, SUM(sales) FROM orders WHERE date >= now()-30 GROUP BY category', output: '4 rows (电子产品/家居厨房/母婴/宠物用品)', duration: 2.5, status: 'success', timestamp: '10:30:01' },
  { id: 't2', toolName: 'rag_search', toolType: 'rag', input: '女装销量下降原因 竞品策略', output: '3 chunks (scores: 0.86, 0.72, 0.65)', duration: 0.8, status: 'success', timestamp: '10:30:04' },
  { id: 't3', toolName: 'generate_report', toolType: 'http', input: '{report_type: "daily_sales", filters: {category: "女装"}}', output: '报告生成完毕，1200 tokens', duration: 1.6, status: 'success', timestamp: '10:30:05' },
]

export const MOCK_SQL_VIZ: SqlViz = {
  query: 'SELECT category, SUM(sales) AS total_sales, COUNT(*) AS order_count FROM orders WHERE date >= now()-30 GROUP BY category ORDER BY total_sales DESC',
  explain: 'Seq Scan on orders (cost=0.00..25.50 rows=450 width=68)\n  Filter: (date >= (now() - \'30 days\'::interval))\n  Group Key: category',
  data: [
    { category: '电子产品', total_sales: 1893, order_count: 12 },
    { category: '家居厨房', total_sales: 874, order_count: 8 },
    { category: '母婴', total_sales: 775, order_count: 6 },
    { category: '宠物用品', total_sales: 757, order_count: 5 },
  ],
  chartType: 'bar' as const,
  chartData: { labels: ['电子产品', '家居厨房', '母婴', '宠物用品'], values: [1893, 874, 775, 757] },
  summary: '电子产品销售额最高(¥1,893)，占总营收的44%。母婴品类订单均价最高(¥129/单)，建议重点关注。',
}

export interface TokenInfo {
  inputTokens: number; outputTokens: number; totalTokens: number; latency: number; cost: number
}

export const MOCK_TOKEN: TokenInfo = { inputTokens: 1450, outputTokens: 520, totalTokens: 1970, latency: 6.6, cost: 0.003 }
