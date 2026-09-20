'use client'

// 从 tokens/page.tsx 抽出：recharts 较重，由页面用 next/dynamic 懒加载
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Legend, LineChart, Line,
} from 'recharts'
import type { TokenUsageDaily } from '@/api/observability'

export type TokenChartRow = TokenUsageDaily & { label: string }
export type MetricMode = 'tokens' | 'cost'

function formatNum(n: number | undefined | null): string {
  return (n ?? 0).toLocaleString('zh-CN')
}

function formatCost(n: number | undefined | null): string {
  const usd = n ?? 0
  return `$${usd.toFixed(2)}`
}

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

export default function TokenCharts({ data, metric }: { data: TokenChartRow[]; metric: MetricMode }) {
  if (metric === 'tokens') {
    return (
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#94a3b8" />
            <YAxis tickFormatter={compact} tick={{ fontSize: 11 }} stroke="#94a3b8" />
            <Tooltip
              formatter={((v: unknown, name: unknown) => [formatNum(Number(v)), String(name)]) as never}
              labelFormatter={((l: unknown) => `日期 ${String(l)}`) as never}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="prompt_tokens" name="输入 Token" stackId="t" fill="#4D6BFE" radius={[0, 0, 0, 0]} />
            <Bar dataKey="completion_tokens" name="输出 Token" stackId="t" fill="#34d399" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    )
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#94a3b8" />
          <YAxis
            tickFormatter={(v: number) => `$${compact(v)}`}
            tick={{ fontSize: 11 }} stroke="#94a3b8"
          />
          <Tooltip
            formatter={((v: unknown) => [formatCost(Number(v)), '成本']) as never}
            labelFormatter={((l: unknown) => `日期 ${String(l)}`) as never}
          />
          <Line type="monotone" dataKey="cost_usd" stroke="#4D6BFE" strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
