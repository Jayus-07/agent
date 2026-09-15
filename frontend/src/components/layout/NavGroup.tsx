'use client'

import { useState } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'

interface NavItem { label: string; path: string }

interface Props {
  icon: React.ReactNode; label: string; path?: string
  items?: NavItem[]; collapsed: boolean
  /** 紧凑模式（TaskSidebar 用）：13px 字号 + 收紧行高；控制台侧栏不受影响 */
  compact?: boolean
}

export default function NavGroup({ icon, label, path, items, collapsed, compact }: Props) {
  const [open, setOpen] = useState(false)
  const pathname = usePathname()
  const hasItems = items && items.length > 0
  const isActive = path ? pathname === path || pathname?.startsWith(path + '/') : items?.some(c => pathname?.startsWith(c.path))
  const rowCls = compact ? 'px-3 py-2 text-[13px]' : 'px-3 py-2 text-sm'

  if (collapsed) {
    return (
      <Link href={path || items?.[0]?.path || '/'}
        className={clsx('w-full flex justify-center p-2 rounded-lg transition-colors',
          isActive ? 'text-accent bg-accent/8' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}
        title={label}>{icon}</Link>
    )
  }

  if (!hasItems && path) {
    return (
      <Link href={path}
        className={clsx('w-full flex items-center gap-2.5 rounded-lg transition-colors', rowCls,
          isActive ? 'text-accent bg-accent/8 font-medium' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="truncate">{label}</span>
      </Link>
    )
  }

  return (
    <div>
      <button onClick={() => setOpen(!open)}
        className={clsx('w-full flex items-center gap-2.5 rounded-lg transition-colors', rowCls,
          isActive ? 'text-accent bg-accent/8' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown size={14} className={clsx('shrink-0 transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="ml-6 mt-0.5 space-y-0.5 border-l border-black/5 pl-3">
          {items!.map(c => (
            <Link key={c.path} href={c.path}
              className={clsx('w-full px-3 py-1.5 text-[13px] rounded-md transition-colors truncate block text-left',
                pathname === c.path ? 'text-accent bg-accent/5 font-medium' : 'text-text-muted hover:text-text-secondary hover:bg-black/5')}>
              {c.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
