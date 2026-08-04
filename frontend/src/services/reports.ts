// Reports service
export interface ReportItem { id: string; title: string; type: string; created_at: string; status: string; [key: string]: any }
export interface DailyReportSummary extends ReportItem {}
export interface DailyReportDetail extends ReportItem { content: string; sections: any[]; report_date: string; report_content: string; kpi_summary: any; trace_id: string; [key: string]: any }

const api = (url: string) => fetch(url).then(r => r.json()).catch(() => ({}))
const BASE = '/api/reports'

export const reportService: any = {
  list: () => api(`${BASE}?limit=20`),
  get: (id: string) => api(`${BASE}/${id}`),
  getReports: () => api(`${BASE}?limit=20`),
  getReport: (id: string) => api(`${BASE}/${id}`),
  getLatestReport: () => api(`${BASE}?limit=1`),
}
