'use client'

import type { ReactNode } from 'react'

export interface WorkbenchTab<TabId extends string> {
  id: TabId
  label: string
}

interface WorkbenchShellProps<TabId extends string> {
  title: string
  description?: string
  tabs: readonly WorkbenchTab<TabId>[]
  activeTab: TabId
  onTabChange: (tab: TabId) => void
  children: ReactNode
}

export default function WorkbenchShell<TabId extends string>({
  title,
  description,
  tabs,
  activeTab,
  onTabChange,
  children,
}: WorkbenchShellProps<TabId>) {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-black/5 bg-white px-4 py-5 sm:px-6">
        <div className="mx-auto w-full max-w-7xl">
          <h1 className="text-xl font-semibold tracking-tight text-text-primary">{title}</h1>
          {description && <p className="mt-1 text-xs leading-5 text-text-secondary">{description}</p>}
        </div>
      </header>
      <div className="shrink-0 border-b border-black/5 bg-white px-4 sm:px-6">
        <div className="mx-auto flex w-full max-w-7xl gap-1 overflow-x-auto" role="tablist" aria-label={title + '内容'}>
          {tabs.map((tab) => {
            const selected = tab.id === activeTab
            const tabClassName = selected
              ? 'border-accent text-accent'
              : 'border-transparent text-text-secondary hover:border-black/10 hover:text-text-primary'
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={'workbench-panel-' + tab.id}
                tabIndex={selected ? 0 : -1}
                onClick={() => onTabChange(tab.id)}
                className={'shrink-0 border-b-2 px-3 py-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ' + tabClassName}
              >
                {tab.label}
              </button>
            )
          })}
        </div>
      </div>
      <section
        id={'workbench-panel-' + activeTab}
        role="tabpanel"
        aria-label={tabs.find((tab) => tab.id === activeTab)?.label}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {children}
      </section>
    </div>
  )
}
