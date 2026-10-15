'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { isNavItemActive, isNavPathActive } from './navConfig'

interface NavItem {
  label: string
  path: string
  href?: string
  icon?: React.ReactNode
  activePaths?: string[]
  activeQuery?: { key: string; value: string; defaultValue?: string }
  section?: string
}

interface Props {
  icon: React.ReactNode
  label: string
  path?: string
  items?: NavItem[]
  collapsed: boolean
  showSections?: boolean
  alwaysOpen?: boolean
  open?: boolean
  onToggle?: () => void
  /** 紧凑模式（TaskSidebar 用）：13px 字号 + 收紧行高；控制台侧栏不受影响 */
  compact?: boolean
}

export default function NavGroup({
  icon, label, path, items, collapsed, compact, showSections, alwaysOpen, open, onToggle,
}: Props) {
  const [internalOpen, setInternalOpen] = useState(false)
  const pathname = usePathname()
  const search = useSearchParams()?.toString() ?? ''
  const hasItems = items && items.length > 0
  const isItemActive = (item: NavItem) => isNavItemActive(pathname, item, search)
  const isActive = path ? isNavPathActive(pathname, path) : items?.some(isItemActive)
  const expanded = open ?? internalOpen
  const toggle = onToggle ?? (() => setInternalOpen((value) => !value))
  const rowCls = compact ? 'px-3 py-2 text-[13px]' : 'px-3 py-2.5 text-sm'

  const renderItems = (flat = false) => (
    <div className={clsx(
      flat ? 'mt-1' : 'ml-4 mt-1 border-l border-black/[0.08] pl-2',
      compact ? 'space-y-0' : 'space-y-1',
    )}>
      {items!.map((item, index) => {
        const previous = items![index - 1]
        const sectionStarts = showSections && item.section && item.section !== previous?.section
        return (
          <div key={item.href || item.path}>
            {sectionStarts && (
              <div role="heading" aria-level={3} className="flex items-center gap-2 px-3 pb-1 pt-3 text-[11px] font-semibold text-text-secondary first:pt-1">
                <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent/60" />
                <span className="shrink-0">{item.section}</span>
                <span aria-hidden="true" className="h-px flex-1 bg-black/[0.08]" />
              </div>
            )}
            <Link
              href={item.href || item.path}
              aria-current={isItemActive(item) ? 'page' : undefined}
              className={clsx(
                'relative flex min-h-9 w-full items-center rounded-md px-3 py-2 text-left transition-colors',
                compact ? 'text-[12px]' : 'text-[13px]',
                isItemActive(item)
                  ? 'bg-accent-soft font-semibold text-accent'
                  : 'text-text-secondary hover:bg-black/5 hover:text-text-primary',
              )}
            >
              {isItemActive(item) && (
                <span aria-hidden="true" className="absolute -left-[9px] top-1.5 bottom-1.5 w-0.5 rounded-full bg-accent" />
              )}
              {item.icon && <span aria-hidden="true" className="mr-2 inline-flex h-4 w-4 shrink-0 items-center justify-center">{item.icon}</span>}
              <span className="truncate">{item.label}</span>
            </Link>
          </div>
        )
      })}
    </div>
  )

  if (collapsed) {
    return (
      <Link href={path || items?.[0]?.href || items?.[0]?.path || '/'}
        className={clsx('w-full flex justify-center rounded-lg p-2 transition-colors',
          isActive ? 'text-accent bg-accent-soft ring-1 ring-accent/10' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}
        title={label}>{icon}</Link>
    )
  }

  if (!hasItems && path) {
    return (
      <Link href={path}
        className={clsx('w-full flex items-center gap-2.5 rounded-lg transition-colors', rowCls,
          isActive ? 'text-accent bg-accent-soft font-semibold ring-1 ring-accent/10' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="truncate">{label}</span>
      </Link>
    )
  }

  if (alwaysOpen && hasItems) {
    return (
      <div className="pb-1">
        <div role="heading" aria-level={2} className="px-3 pb-1 pt-4 text-[11px] font-semibold tracking-wide text-text-muted first:pt-2">
          {label}
        </div>
        {renderItems(true)}
      </div>
    )
  }

  return (
    <div>
      <button type="button" onClick={toggle} aria-expanded={expanded}
        className={clsx('w-full flex items-center gap-2.5 rounded-lg transition-colors', rowCls,
          isActive ? 'text-accent bg-accent-soft font-semibold ring-1 ring-accent/10' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown size={14} className={clsx('shrink-0 transition-transform duration-200', expanded && 'rotate-180')} />
      </button>
      {expanded && renderItems()}
    </div>
  )
}
