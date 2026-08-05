/**
 * 报告中心 service
 *
 * 后端为 backend/app/api/routes/reports.py（前缀 /reports，
 * 经 next.config.js rewrite 由 /api/reports 代理）。
 *
 * 两个此前的契约错配已修正：
 * 1. 列表接口的参数是 type/page/page_size，不是 limit —— 传 limit 会被 FastAPI 忽略
 * 2. "最新报告"有专门端点 /reports/latest（返回 {report}），
 *    此前用 /reports?limit=1 打列表接口再读 .report，恒为 undefined，
 *    导致报告中心顶部的 KPI 卡片从未显示过
 */

/** KPI 摘要（由 workflow 生成，字段可能缺失） */
export interface ReportKpiSummary {
  total_products?: number
  alert_count?: number
  sales_records?: number
  report_date?: string
}

/** 报告列表项 */
export interface DailyReportSummary {
  id: string
  report_date: string
  report_type: string
  status: string
  kpi_summary: ReportKpiSummary | null
  trace_id: string | null
  created_at: string
}

/** 报告详情：列表项 + 完整 markdown 正文 */
export interface DailyReportDetail extends DailyReportSummary {
  report_content: string
}

const BASE = '/api/reports'

/** 失败时抛错，让调用方能区分"没有报告"和"接口挂了" */
async function api<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`GET ${url} 失败 (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`)
  }
  return res.json() as Promise<T>
}

export const reportService = {
  /** 报告列表（按类型 + 分页） */
  getReports: (params: { type: string; page?: number; pageSize?: number }) => {
    const q = new URLSearchParams({
      type: params.type,
      page: String(params.page ?? 1),
      page_size: String(params.pageSize ?? 20),
    })
    return api<{ reports: DailyReportSummary[]; page: number; page_size: number }>(`${BASE}?${q}`)
  },

  /** 最新一条报告（含完整正文 + KPI 摘要） */
  getLatestReport: (type: string) =>
    api<{ report: DailyReportDetail | null }>(`${BASE}/latest?type=${encodeURIComponent(type)}`),

  /** 报告详情 */
  getReport: (id: string) => api<{ report: DailyReportDetail }>(`${BASE}/${encodeURIComponent(id)}`),
}
