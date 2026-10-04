import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { clsx } from 'clsx'
import PageHeader from './PageHeader'

/** AI 资产页共用的内容宽度、边界和层级 token。 */
export const ASSET_SURFACE = 'rounded-xl border border-border-subtle bg-surface-base shadow-card'
export const ASSET_STATE = `${ASSET_SURFACE} p-10 text-center`

interface AssetPageShellProps {
  title: string
  desc?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}

export function AssetPageShell({ title, desc, actions, children, className }: AssetPageShellProps) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div
        data-testid="asset-page-shell"
        className={clsx('mx-auto w-full max-w-7xl px-6 py-7', className)}
      >
        <div className="mb-6 flex items-start justify-between gap-4">
          <PageHeader title={title} desc={desc} className="mb-0" />
          {actions && <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </div>
  )
}

interface AssetActionButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon?: ReactNode
  tone?: 'secondary' | 'primary'
}

export function AssetActionButton({ icon, tone = 'secondary', className, children, ...props }: AssetActionButtonProps) {
  return (
    <button
      type="button"
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50',
        tone === 'primary'
          ? 'bg-accent text-white hover:bg-accent-hover'
          : 'border border-border-subtle bg-surface-base text-text-secondary hover:border-accent/30 hover:bg-bg-hover hover:text-text-primary',
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </button>
  )
}

interface AssetSectionProps {
  title: string
  meta?: ReactNode
  icon?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}

export function AssetSection({ title, meta, icon, children, className, bodyClassName }: AssetSectionProps) {
  return (
    <section className={clsx(ASSET_SURFACE, 'overflow-hidden', className)}>
      <div className="flex items-center justify-between gap-3 border-b border-border-subtle bg-bg-elevated/60 px-4 py-3">
        <h2 className="flex items-center gap-2 text-[13px] font-semibold text-text-primary">
          {icon}
          {title}
        </h2>
        {meta && <span className="text-[11px] text-text-muted">{meta}</span>}
      </div>
      <div className={bodyClassName ?? 'p-4'}>{children}</div>
    </section>
  )
}

export function AssetStatCard({ label, value, hint, icon, tone = 'default' }: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  tone?: 'default' | 'warn'
}) {
  return (
    <div className={clsx(ASSET_SURFACE, 'p-4')}>
      <div className="flex items-center gap-2 text-[11px] text-text-muted">
        {icon}
        {label}
      </div>
      <div className={clsx('mt-1 text-xl font-semibold', tone === 'warn' ? 'text-amber-600' : 'text-text-primary')}>
        {value}
      </div>
      {hint && <div className="mt-1 text-[11px] leading-relaxed text-text-muted">{hint}</div>}
    </div>
  )
}

export function AssetState({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx(ASSET_STATE, 'text-[13px] text-text-muted', className)}>{children}</div>
}
