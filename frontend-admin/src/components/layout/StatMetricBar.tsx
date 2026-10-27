'use client'

/**
 * StatMetricBar — V2 治理工作台的紧凑指标条。
 *
 * V2 三大工作台顶部的一行式指标（连接总数/健康/异常/漂移/工具数…），
 * 与 AssetStatCard（独立卡片）互补：指标多、密度高时用本组件。
 */
import type { ReactNode } from 'react'
import { clsx } from 'clsx'
import { ASSET_SURFACE } from './AssetPageShell'

export interface StatMetricItem {
  label: string
  value: ReactNode
  tone?: 'default' | 'warn' | 'danger' | 'accent'
  hint?: string
}

const VALUE_TONE: Record<NonNullable<StatMetricItem['tone']>, string> = {
  default: 'text-text-primary',
  warn: 'text-amber-600',
  danger: 'text-rose-600',
  accent: 'text-accent',
}

export default function StatMetricBar({
  items,
  className,
}: {
  items: readonly StatMetricItem[]
  className?: string
}) {
  if (items.length === 0) return null
  return (
    <div className={clsx(ASSET_SURFACE, 'flex flex-wrap overflow-hidden', className)}>
      {items.map((item, index) => (
        <div
          key={item.label}
          className={clsx(
            'min-w-[7rem] flex-1 px-4 py-3',
            index > 0 && 'border-l border-border-subtle',
          )}
        >
          <div className="text-[11px] text-text-muted">{item.label}</div>
          <div className={clsx('mt-0.5 text-lg font-semibold tabular-nums', VALUE_TONE[item.tone ?? 'default'])}>
            {item.value}
          </div>
          {item.hint && <div className="mt-0.5 text-[11px] text-text-muted">{item.hint}</div>}
        </div>
      ))}
    </div>
  )
}
