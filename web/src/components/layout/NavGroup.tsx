'use client'

import { useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { ChevronDown } from 'lucide-react'
import { clsx } from 'clsx'

interface NavItem { label: string; path: string }

interface Props {
  icon: React.ReactNode; label: string; path?: string
  items?: NavItem[]; collapsed: boolean
}

export default function NavGroup({ icon, label, path, items, collapsed }: Props) {
  const [open, setOpen] = useState(false)
  const pathname = usePathname()
  const router = useRouter()
  const hasItems = items && items.length > 0
  const isActive = path ? pathname === path || pathname?.startsWith(path + '/') : items?.some(c => pathname?.startsWith(c.path))

  if (collapsed) {
    return (
      <button onClick={() => router.push(path || items?.[0]?.path || '/')}
        className={clsx('w-full flex justify-center p-2 rounded-lg transition-colors',
          isActive ? 'text-accent bg-accent/8' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}
        title={label}>{icon}</button>
    )
  }

  if (!hasItems && path) {
    return (
      <button onClick={() => router.push(path)}
        className={clsx('w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors',
          isActive ? 'text-accent bg-accent/8 font-medium' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="truncate">{label}</span>
      </button>
    )
  }

  return (
    <div>
      <button onClick={() => setOpen(!open)}
        className={clsx('w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-lg transition-colors',
          isActive ? 'text-accent bg-accent/8' : 'text-text-secondary hover:text-text-primary hover:bg-black/5')}>
        {icon}<span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown size={14} className={clsx('shrink-0 transition-transform duration-200', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="ml-6 mt-0.5 space-y-0.5 border-l border-black/5 pl-3">
          {items!.map(c => (
            <button key={c.path} onClick={() => router.push(c.path)}
              className={clsx('w-full px-3 py-1.5 text-[13px] rounded-md transition-colors truncate block text-left',
                pathname === c.path ? 'text-accent bg-accent/5 font-medium' : 'text-text-muted hover:text-text-secondary hover:bg-black/5')}>
              {c.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
