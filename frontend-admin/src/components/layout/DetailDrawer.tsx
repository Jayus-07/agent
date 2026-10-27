'use client'

/**
 * DetailDrawer — V2 治理工作台共用的右侧详情抽屉。
 *
 * 承载 V2 原型的「详情 Tab 化」交互（连接详情/工具详情）：标题 + 动作区 +
 * 可选页级 Tab + 可滚动内容体。只管壳与开合，数据状态（加载/空/错误）由
 * 调用方在 children 内表达（遵循设计标准 §4 的状态覆盖表）。
 */
import { useEffect, type ReactNode } from 'react'
import { clsx } from 'clsx'
import { X } from 'lucide-react'

export interface DetailDrawerTab {
  id: string
  label: string
  /** 置灰壳位 Tab：不可进入，配合 disabledHint 说明原因（data-stub="demo"） */
  disabled?: boolean
  /** disabled Tab 的常驻提示（渲染在 Tab 行下方，不依赖悬停） */
  disabledHint?: string
}

interface DetailDrawerProps {
  open: boolean
  onClose: () => void
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  tabs?: readonly DetailDrawerTab[]
  activeTab?: string
  onTabChange?: (id: string) => void
  children: ReactNode
  width?: 'md' | 'lg'
}

export default function DetailDrawer({
  open,
  onClose,
  title,
  subtitle,
  actions,
  tabs,
  activeTab,
  onTabChange,
  children,
  width = 'lg',
}: DetailDrawerProps) {
  // Esc 关闭 + 打开期间锁定背景滚动（关闭/卸载时还原）
  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="关闭详情"
        className="absolute inset-0 h-full w-full cursor-default bg-black/30"
        onClick={onClose}
      />
      <aside
        className={clsx(
          'absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-xl',
          width === 'md' ? 'max-w-xl' : 'max-w-2xl',
        )}
      >
        <header className="shrink-0 border-b border-border-subtle px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-text-primary">{title}</h2>
              {subtitle && <div className="mt-0.5 text-xs text-text-muted">{subtitle}</div>}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {actions}
              <button
                type="button"
                aria-label="关闭"
                onClick={onClose}
                className="rounded-lg p-1.5 text-text-muted transition-colors hover:bg-bg-hover hover:text-text-primary"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          {tabs && tabs.length > 0 && (
            <>
              <div className="-mb-px mt-3 flex gap-1 overflow-x-auto" role="tablist">
                {tabs.map((tab) => {
                  const selected = tab.id === activeTab
                  if (tab.disabled) {
                    return (
                      <span
                        key={tab.id}
                        data-stub="demo"
                        title={tab.disabledHint}
                        aria-disabled="true"
                        className="shrink-0 cursor-not-allowed border-b-2 border-transparent px-3 py-2 text-xs font-medium text-text-muted/50"
                      >
                        {tab.label}
                      </span>
                    )
                  }
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={selected}
                      onClick={() => onTabChange?.(tab.id)}
                      className={clsx(
                        'shrink-0 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none',
                        selected
                          ? 'border-accent text-accent'
                          : 'border-transparent text-text-secondary hover:border-black/10 hover:text-text-primary',
                      )}
                    >
                      {tab.label}
                    </button>
                  )
                })}
              </div>
              {tabs.some((tab) => tab.disabled && tab.disabledHint) && (
                <div className="mt-1 space-y-0.5">
                  {tabs.filter((tab) => tab.disabled && tab.disabledHint).map((tab) => (
                    <div key={tab.id} className="text-[11px] text-text-muted">
                      「{tab.label}」{tab.disabledHint}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>
  )
}
