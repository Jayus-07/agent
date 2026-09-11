'use client'

// 从 selection/page.tsx 抽出：recharts 较重，由页面用 next/dynamic 懒加载
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from 'recharts'
import type { TrendsData } from '@/services/selection'

export default function TrendPanels({ trends }: { trends: TrendsData }) {
  return (
    <>
      <div className="bg-surface-base border border-border-subtle rounded-xl p-4">
        <div className="text-sm font-medium text-text-primary mb-3">价格分位趋势（p25 / p50 / p75）</div>
        {trends.price_quantiles.length === 0 ? (
          <div className="text-xs text-text-muted py-8 text-center">暂无价格数据</div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={trends.price_quantiles}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Area type="monotone" dataKey="p25" stroke="#94a3b8" fill="#e2e8f0" />
              <Area type="monotone" dataKey="p50" stroke="#3b82f6" fill="#bfdbfe" />
              <Area type="monotone" dataKey="p75" stroke="#6366f1" fill="#c7d2fe" />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="bg-surface-base border border-border-subtle rounded-xl p-4">
        <div className="text-sm font-medium text-text-primary mb-3">热卖卖点词频</div>
        {trends.highlight_freq.length === 0 ? (
          <div className="text-xs text-text-muted py-8 text-center">暂无卖点数据</div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={trends.highlight_freq.slice(0, 10)} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="keyword" width={80} tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </>
  )
}
