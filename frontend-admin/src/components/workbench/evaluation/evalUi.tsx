'use client'

/**
 * 评测中心共享 UI 原语：按企业评测平台 V2 原型的信息架构与交互语义实现，
 * 颜色、字体与控件沿用管理端现有设计 token（accent/bg/border/text 系列）。
 * 数据一律由调用方传入真实 API 结果；本文件不含任何演示数据。
 */

import type { ReactNode } from 'react'

export const evalControl =
  'min-h-10 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm text-text-primary outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50'

type Tone = 'good' | 'bad' | 'warn' | 'blue' | 'dim'

const badgeTone: Record<Tone, string> = {
  good: 'bg-emerald-50 text-emerald-700',
  bad: 'bg-red-50 text-red-700',
  warn: 'bg-amber-50 text-amber-800',
  blue: 'bg-accent-soft text-accent',
  dim: 'bg-bg-elevated text-text-secondary',
}

export function EvalBadge({ text, tone = 'dim' }: { text: string; tone?: Tone }) {
  return (
    <span className={'inline-flex items-center rounded-md px-2 py-1 text-[11px] font-semibold ' + badgeTone[tone]}>
      {text}
    </span>
  )
}

export function EvalTag({ children }: { children: ReactNode }) {
  return <span className="rounded bg-bg-elevated px-1.5 py-1 text-[11px] text-text-secondary">{children}</span>
}

export function EvalPageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-tight text-text-primary">{title}</h1>
        <p className="mt-1.5 max-w-3xl text-sm leading-6 text-text-secondary">{subtitle}</p>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

export function EvalKpiCard({
  label,
  value,
  suffix,
  detail,
  tone,
}: {
  label: string
  value: string
  suffix?: string
  detail: string
  tone?: Tone
}) {
  const valueTone =
    tone === 'good' ? 'text-emerald-700' : tone === 'bad' ? 'text-red-700' : tone === 'warn' ? 'text-amber-700' : 'text-text-primary'
  return (
    <article className="min-w-0 rounded-xl border border-border-subtle bg-surface-base p-5 shadow-[var(--shadow-sm)]">
      <p className="text-xs text-text-muted">{label}</p>
      <p className={'mt-2.5 text-3xl font-bold tabular-nums tracking-tight ' + valueTone}>
        {value}
        {suffix && <span className="ml-1 text-sm font-medium text-text-secondary">{suffix}</span>}
      </p>
      <p className="mt-2 text-xs leading-5 text-text-muted">{detail}</p>
    </article>
  )
}

export function EvalCard({
  title,
  subtitle,
  actions,
  children,
  className = '',
  bodyClassName = 'p-5',
}: {
  title?: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={'min-w-0 overflow-hidden rounded-xl border border-border-subtle bg-surface-base shadow-[var(--shadow-sm)] ' + className}>
      {title && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle px-5 py-3.5">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-text-primary">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  )
}

export function EvalInfoBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border-subtle bg-bg-elevated p-3.5 text-xs leading-6 text-text-secondary">
      {children}
    </div>
  )
}

export function EvalWarnBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3.5 text-xs leading-6 text-amber-900">
      {children}
    </div>
  )
}

export function EvalEmpty({ children }: { children: ReactNode }) {
  return <p className="px-4 py-9 text-center text-sm text-text-muted">{children}</p>
}

export function EvalMetricRow({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-subtle py-3 last:border-b-0">
      <span className="min-w-0 text-sm text-text-secondary">{label}</span>
      <span className="shrink-0 text-sm font-semibold text-text-primary">{children}</span>
    </div>
  )
}

export function EvalDiffBar({ value, weak = false }: { value: number; weak?: boolean }) {
  const width = Math.max(0, Math.min(100, value))
  return (
    <div className="h-2 overflow-hidden rounded-full bg-bg-elevated">
      <span
        className={'block h-full rounded-full ' + (weak ? 'bg-accent/40' : 'bg-accent')}
        style={{ width: width + '%' }}
      />
    </div>
  )
}

export function EvalButton({
  children,
  onClick,
  variant = 'default',
  size = 'md',
  disabled,
  type = 'button',
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'soft' | 'danger'
  size?: 'md' | 'sm'
  disabled?: boolean
  type?: 'button' | 'submit'
  className?: string
}) {
  const variantClass =
    variant === 'primary'
      ? 'border-accent bg-accent text-white hover:bg-accent-hover'
      : variant === 'soft'
        ? 'border-accent/30 bg-accent-soft text-accent hover:border-accent'
        : variant === 'danger'
          ? 'border-border-subtle bg-surface-base text-red-700 hover:bg-bg-hover'
          : 'border-border-subtle bg-surface-base text-text-primary hover:bg-bg-hover'
  const sizeClass = size === 'sm' ? 'min-h-8 px-2.5 py-1 text-xs' : 'min-h-10 px-3.5 py-2 text-sm'
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={
        'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border font-semibold shadow-[var(--shadow-sm)] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50 ' +
        variantClass + ' ' + sizeClass + ' ' + className
      }
    >
      {children}
    </button>
  )
}

export function EvalField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-semibold text-text-secondary">{label}</span>
      {children}
    </label>
  )
}

export function EvalToolbar({ children }: { children: ReactNode }) {
  return <div className="mb-4 flex flex-wrap items-center gap-2.5">{children}</div>
}
