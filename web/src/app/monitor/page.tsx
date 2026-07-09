'use client'

/**
 * MonitorPage — 全局可观测性大盘
 * 数据源: /observability/* JSON API + /metrics (Prometheus)
 */

import MonitorDashboard from '@/components/monitor/MonitorDashboard'
import ErrorBoundary from '@/components/ErrorBoundary'

export default function MonitorPage() {
  return (
    <ErrorBoundary>
      <MonitorDashboard />
    </ErrorBoundary>
  )
}
