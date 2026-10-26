'use client'

import type { ReactNode } from 'react'

/** 原型风格共享原语：白卡片 + 细边框 + 中性按钮，对应原型视觉。 */

export const ctl = 'min-h-8 rounded-md border border-border-subtle bg-surface-base px-2.5 text-xs text-text-primary disabled:opacity-50'
export const ctlPrimary = 'min-h-8 rounded-md border border-transparent bg-accent px-3 text-xs font-medium text-white disabled:opacity-50'
export const ctlGhost = 'min-h-8 rounded-md px-2.5 text-xs text-text-secondary hover:text-text-primary disabled:opacity-50'

export function Badge({ text, tone }: { text: string; tone: 'good' | 'bad' | 'warn' | 'blue' | 'gray' | 'accent' }) {
  const cls = {
    good: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300',
    bad: 'bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-300',
    warn: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300',
    blue: 'bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300',
    accent: 'bg-accent-soft text-accent',
    gray: 'bg-bg-elevated text-text-secondary',
  }[tone]
  return <span className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] leading-4 ${cls}`}>{text}</span>
}

export function StatusBadge({ status }: { status: string }) {
  if (status === 'published' || status === 'completed' || status === 'pass') return <Badge text={status === 'pass' ? '通过' : status === 'completed' ? '完成' : '已发布'} tone="good" />
  if (status === 'draft') return <Badge text="草稿" tone="warn" />
  if (status === 'failed' || status === 'fail') return <Badge text={status === 'fail' ? '未通过' : '失败'} tone="bad" />
  if (status === 'cancelled') return <Badge text="已取消" tone="warn" />
  if (status === 'blocked') return <Badge text="已阻断" tone="bad" />
  if (['queued', 'preparing', 'running', 'aggregating'].includes(status)) return <Badge text={status === 'running' ? '运行中' : status === 'queued' ? '排队中' : status === 'preparing' ? '准备快照' : '汇总结果'} tone="blue" />
  if (status === 'error') return <Badge text="错误" tone="warn" />
  if (status === 'skip') return <Badge text="跳过" tone="gray" />
  if (status === 'deprecated') return <Badge text="已弃用" tone="gray" />
  return <Badge text={status} tone="gray" />
}

export const MODE_LABELS: Record<string, string> = {
  offline: '离线自研（不调用模型）',
  semantic: '在线自研指标',
  ragas: 'RAGAS',
  'self+ragas': '在线自研 + RAGAS（默认）',
}

export function modeShort(mode?: string | null): string {
  return { offline: '离线自研', semantic: '在线自研', ragas: 'RAGAS', 'self+ragas': '自研 + RAGAS' }[mode || ''] || '—'
}

export function Card({ title, subtitle, actions, children, className = '' }: { title?: string; subtitle?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-border-subtle bg-surface-base ${className}`}>
    {(title || actions) && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle px-4 py-3">
      <div>
        {title && <h3 className="text-sm font-semibold text-text-primary">{title}</h3>}
        {subtitle && <p className="mt-0.5 text-xs text-text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>}
    <div className="p-4">{children}</div>
  </section>
}

export function Th({ children, end }: { children: ReactNode; end?: boolean }) {
  return <th className={`whitespace-nowrap px-3 py-2 text-xs font-medium text-text-muted ${end ? 'text-right' : 'text-left'}`}>{children}</th>
}

export function Td({ children, end, mono, className = '' }: { children: ReactNode; end?: boolean; mono?: boolean; className?: string }) {
  return <td className={`px-3 py-2.5 align-middle text-xs text-text-secondary ${end ? 'text-right tabular-nums' : ''} ${mono ? 'font-mono text-[11px]' : ''} ${className}`}>{children}</td>
}

export function TableWrap({ children, minWidth = 720 }: { children: ReactNode; minWidth?: number }) {
  return <div className="overflow-x-auto rounded-md border border-border-subtle">
    <table className="w-full text-left" style={{ minWidth }}>{children}</table>
  </div>
}

export function Empty({ text }: { text: string }) {
  return <p className="py-8 text-center text-xs text-text-muted">{text}</p>
}

export function timeShort(value?: string | null): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function fmtCost(amount?: string | number | null, currency?: string | null): string {
  if (amount === null || amount === undefined) return '未归因'
  return `${amount} ${currency || ''}`.trim()
}
