'use client'

/**
 * StatusChip — V2 治理工作台统一状态徽章。
 *
 * 审核态/发布态/健康态同屏并存（待审核/漂移/已审核/运行时可用/停用…），
 * 颜色只做辅助：徽章始终带文字（设计标准：不得仅靠颜色表达状态）。
 * 语义 → tone 的映射由调用方维护，本组件不做业务判断。
 */
import type { ReactNode } from 'react'
import { clsx } from 'clsx'

export type StatusTone = 'neutral' | 'success' | 'warn' | 'danger' | 'info' | 'accent'

const TONE_CLASS: Record<StatusTone, string> = {
  neutral: 'bg-black/[0.04] text-text-secondary ring-black/10',
  success: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  warn: 'bg-amber-50 text-amber-700 ring-amber-200',
  danger: 'bg-rose-50 text-rose-700 ring-rose-200',
  info: 'bg-sky-50 text-sky-700 ring-sky-200',
  accent: 'bg-accent/10 text-accent ring-accent/30',
}

const DOT_CLASS: Record<StatusTone, string> = {
  neutral: 'bg-black/30',
  success: 'bg-emerald-500',
  warn: 'bg-amber-500',
  danger: 'bg-rose-500',
  info: 'bg-sky-500',
  accent: 'bg-accent',
}

export default function StatusChip({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: StatusTone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={clsx(
        'inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset',
        TONE_CLASS[tone],
        className,
      )}
    >
      <span aria-hidden className={clsx('h-1.5 w-1.5 rounded-full', DOT_CLASS[tone])} />
      {children}
    </span>
  )
}
