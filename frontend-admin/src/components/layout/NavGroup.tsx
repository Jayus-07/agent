'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'
import { isNavPathActive } from './navConfig'

interface NavItem { label: string; path: string; activePaths?: string[]; section?: string }

interface Props {
  icon: React.ReactNode; label: string; path?: string
  items?: NavItem[]; collapsed: boolean
  open?: boolean
  onToggle?: () => void
  /** 紧凑模式（TaskSidebar 用）：13px 字号 + 收紧行高；控制台侧栏不受影响 */
  compact?: boolean
}

export default function NavGroup({ icon, label, path, items, collapsed, compact, open, onToggle }: Props) {
  const [internalOpen, setInternalOpen] = useState(false)
  const pathname = usePathname()
  const hasItems = items && items.length > 0
  const isActive = path ? isNavPathActive(pathname, path) : items?.some(c => isNavPathActive(pathname, c.path, c.activePaths))
  const expanded = open ?? internalOpen
  const toggle = onToggle ?? (() => setInternalOpen((value) => !value))
  const rowCls = compact ? 'px-3 py-2 text-[13px]' : 'px-3 py-2.5 text-sm'

  if (collapsed) {
    return (
      <Link href={path || items?.[0]?.path || '/'}
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

  return (
    <div>
      <button type="button" onClick={toggle} aria-expanded={expanded}
        className={clsx('w-full flex items-center gap-2.5 rounded-lg transition-colors', rowCls,
          isActive ? 'text-accent bg-accent-soft font-semibold ring-1 ring-accent/10' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown size={14} className={clsx('shrink-0 transition-transform duration-200', expanded && 'rotate-180')} />
      </button>
      {expanded && (
        <div className={clsx(
          'ml-4 mt-1 space-y-0.5 border-l border-black/[0.08] pl-2',
          compact ? 'space-y-0' : 'space-y-1',
        )}>
          {items!.map((c) => {
            return (
              <div key={c.path}>
                <Link
                  href={c.path}
                  aria-current={isNavPathActive(pathname, c.path, c.activePaths) ? 'page' : undefined}
                  className={clsx(
                    'relative flex min-h-9 w-full items-center rounded-md px-3 py-2 text-left transition-colors',
                    compact ? 'text-[12px]' : 'text-[13px]',
                    isNavPathActive(pathname, c.path, c.activePaths)
                      ? 'bg-accent-soft font-semibold text-accent'
                      : 'text-text-secondary hover:bg-black/5 hover:text-text-primary',
                  )}
                >
                  {isNavPathActive(pathname, c.path, c.activePaths) && (
                    <span aria-hidden="true" className="absolute -left-[9px] top-1.5 bottom-1.5 w-0.5 rounded-full bg-accent" />
                  )}
                  <span className="truncate">{c.label}</span>
                </Link>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
