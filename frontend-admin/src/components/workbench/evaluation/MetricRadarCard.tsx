'use client'

import React, { useMemo } from 'react'
import {
  RAG_CORE_METRICS,
  RAGAS_OPTIONAL_METRICS,
  resolveMetricDef,
  formatScorePercent,
  getScoreLevel,
  getScoreTone,
  type MetricDefinition,
} from './ragMetrics'

export interface MetricRadarCardProps {
  /** 各项指标原始得分 map，如 { context_recall: 0.88, ragas_faithfulness: 0.94 } */
  scores: Record<string, number | null | undefined>
  /** 是否展示 Ragas 进阶可选指标 */
  showRagasOptional?: boolean
  /** 标题 */
  title?: string
  /** 描述或子标题 */
  subtitle?: string
}

function polarToCartesian(centerX: number, centerY: number, radius: number, angleInDegrees: number) {
  const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180.0
  return {
    x: centerX + radius * Math.cos(angleInRadians),
    y: centerY + radius * Math.sin(angleInRadians),
  }
}

export default function MetricRadarCard({
  scores,
  showRagasOptional = true,
  title = 'RAG 五维核心质量雷达',
  subtitle = '综合评估检索质量（召回/精度）与生成质量（忠实度/相关性/真实性）',
}: MetricRadarCardProps) {
  const coreScores = useMemo(() => {
    return RAG_CORE_METRICS.map(def => {
      // 优先读直接 key，再尝试带 ragas_ 前缀的 key
      const val = scores[def.key] ?? scores[`ragas_${def.key}`] ?? null
      return {
        def,
        score: typeof val === 'number' ? Math.max(0, Math.min(1, val)) : null,
      }
    })
  }, [scores])

  const optionalScores = useMemo(() => {
    return RAGAS_OPTIONAL_METRICS.map(def => {
      const val = scores[def.key] ?? scores[`ragas_${def.key}`] ?? null
      return {
        def,
        score: typeof val === 'number' ? Math.max(0, Math.min(1, val)) : null,
      }
    }).filter(item => item.score !== null)
  }, [scores])

  // 雷达图计算（5 轴）
  const size = 260
  const center = size / 2
  const maxRadius = 90
  const angleStep = 360 / coreScores.length

  const radarPolygonPoints = useMemo(() => {
    return coreScores
      .map((item, idx) => {
        const val = item.score ?? 0
        const r = maxRadius * val
        const { x, y } = polarToCartesian(center, center, r, idx * angleStep)
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }, [coreScores, center, maxRadius, angleStep])

  return (
    <div className="rounded-xl border border-border-subtle bg-surface-base p-5 shadow-xs">
      <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-text-primary">{title}</h3>
          <p className="text-xs text-text-muted">{subtitle}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
            核心指标
          </span>
          {showRagasOptional && optionalScores.length > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-indigo-500/10 px-2.5 py-0.5 text-[11px] font-medium text-indigo-600 dark:text-indigo-400">
              Ragas 联合测评已激活
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        {/* SVG 雷达图 */}
        <div className="flex flex-col items-center justify-center lg:col-span-5">
          <svg
            width={size}
            height={size}
            viewBox={`0 0 ${size} ${size}`}
            className="overflow-visible"
            role="img"
            aria-label="RAG五维质量雷达图"
          >
            {/* 刻度背景同心圆网格 */}
            {[0.25, 0.5, 0.75, 1.0].map(ratio => {
              const r = maxRadius * ratio
              return (
                <circle
                  key={ratio}
                  cx={center}
                  cy={center}
                  r={r}
                  fill="none"
                  stroke="currentColor"
                  strokeDasharray={ratio < 1.0 ? '2,2' : undefined}
                  className="text-border-subtle"
                  strokeWidth="1"
                />
              )
            })}

            {/* 轴线 */}
            {coreScores.map((_, idx) => {
              const { x, y } = polarToCartesian(center, center, maxRadius, idx * angleStep)
              return (
                <line
                  key={idx}
                  x1={center}
                  y1={center}
                  x2={x}
                  y2={y}
                  stroke="currentColor"
                  className="text-border-subtle"
                  strokeWidth="1"
                />
              )
            })}

            {/* 得分填充多边形 */}
            <polygon
              points={radarPolygonPoints}
              fill="rgba(16, 185, 129, 0.2)"
              stroke="#10b981"
              strokeWidth="2"
              className="transition-all duration-300"
            />

            {/* 轴顶点与标签 */}
            {coreScores.map((item, idx) => {
              const { x, y } = polarToCartesian(center, center, maxRadius, idx * angleStep)
              const labelPos = polarToCartesian(center, center, maxRadius + 22, idx * angleStep)
              const scorePos = polarToCartesian(
                center,
                center,
                maxRadius * (item.score ?? 0),
                idx * angleStep,
              )

              return (
                <g key={item.def.key}>
                  {/* 数据点 */}
                  <circle
                    cx={scorePos.x}
                    cy={scorePos.y}
                    r="4"
                    fill="#10b981"
                    stroke="#ffffff"
                    strokeWidth="1.5"
                  />
                  {/* 文本标签 */}
                  <text
                    x={labelPos.x}
                    y={labelPos.y}
                    textAnchor="middle"
                    dominantBaseline="central"
                    className="fill-text-secondary text-[11px] font-medium"
                  >
                    {item.def.label}
                  </text>
                </g>
              )
            })}
          </svg>
          <div className="mt-2 text-center text-[11px] text-text-muted">
            外环线为 100% 满分线 · 虚线为 50% 临界线
          </div>
        </div>

        {/* 核心指标明细进度卡片 */}
        <div className="flex flex-col justify-center space-y-3 lg:col-span-7">
          <div className="space-y-2.5">
            {coreScores.map(({ def, score }) => {
              const level = getScoreLevel(score, def.defaultThreshold)
              const tone = getScoreTone(level)
              const pct = score !== null ? Math.round(score * 100) : 0

              let barColor = 'bg-emerald-500'
              if (tone === 'warn') barColor = 'bg-amber-500'
              if (tone === 'bad') barColor = 'bg-rose-500'
              if (tone === 'muted') barColor = 'bg-slate-300'

              return (
                <div key={def.key} className="rounded-lg border border-border-subtle bg-bg-elevated p-2.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-text-primary">
                      {def.label}
                      <span className="ml-1 text-[10px] text-text-muted">({def.enLabel})</span>
                    </span>
                    <span className="font-mono text-xs font-semibold text-text-primary">
                      {formatScorePercent(score)}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-border-subtle/50">
                    <div
                      className={`h-full ${barColor} rounded-full transition-all duration-500`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <p className="mt-1 text-[10px] leading-tight text-text-muted">{def.description}</p>
                </div>
              )
            })}
          </div>

          {/* 联合 Ragas 可选评测结果 */}
          {showRagasOptional && optionalScores.length > 0 && (
            <div className="mt-4 border-t border-border-subtle pt-3">
              <h4 className="mb-2 text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                ⚡ 联合 Ragas 进阶可选指标得分
              </h4>
              <div className="grid gap-2 sm:grid-cols-2">
                {optionalScores.map(({ def, score }) => (
                  <div
                    key={def.key}
                    className="flex items-center justify-between rounded-md border border-indigo-500/20 bg-indigo-50/30 px-2.5 py-1.5 text-xs dark:bg-indigo-950/20"
                  >
                    <div>
                      <p className="font-medium text-text-primary">{def.label}</p>
                      <p className="text-[10px] text-text-muted">{def.enLabel}</p>
                    </div>
                    <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400">
                      {formatScorePercent(score)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
