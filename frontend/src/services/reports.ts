// Service — Reports API

const BASE = '/api'

export interface DailyReportSummary {
  id: string
  report_date: string
  report_type: string
  status: string
  kpi_summary: {
    total_products?: number
    alert_count?: number
    sales_records?: number
    report_date?: string
  }
  trace_id: string
  created_at: string
}

export interface DailyReportDetail extends DailyReportSummary {
  report_content: string
}

export interface ReportListResult {
  reports: DailyReportSummary[]
  total: number
  page: number
  page_size: number
}

export const reportService = {
  async getReports(params?: {
    type?: string; page?: number; page_size?: number
  }): Promise<ReportListResult> {
    const qs = new URLSearchParams()
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== '') qs.set(k, String(v))
      })
    }
    const res = await fetch(`${BASE}/reports?${qs}`)
    return res.json()
  },

  async getLatestReport(type: string = 'daily_report'): Promise<{ report: DailyReportDetail | null }> {
    const res = await fetch(`${BASE}/reports/latest?type=${type}`)
    return res.json()
  },

  async getReport(id: string): Promise<{ report: DailyReportDetail }> {
    const res = await fetch(`${BASE}/reports/${id}`)
    if (!res.ok) throw new Error(`Report ${id} not found`)
    return res.json()
  },
}
