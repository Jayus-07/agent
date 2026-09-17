'use client'

/**
 * EmptyState — 管理端统一空态（UX P2-⑧，设计文档 §4.6）
 *
 * 三层空态体系：
 * - 接口未就绪 / 建设中 → kind="under_construction"（本组件，取代零引用的
 *   PlaceholderPage 场景：原实现死白板不可导航，违反「空态必须可导航」约束）
 * - 权限不足 → ForbiddenCard（P0-① 已交付，不在本组件职责内）
 * - 数据为空 → kind="no_data"
 *
 * 硬约束（C11「不写假数据」推论）：空态不允许死白板 —— 提供 actionHref
 * 或 onAction 至少其一作为出路；两页接线示范见 /schedules /evaluations。
 */
import Link from 'next/link'
import type { ReactNode } from 'react'

export type EmptyKind = 'no_data' | 'under_construction'

export interface EmptyStateProps {
  /** 空态类别：数据为空（默认）| 功能建设中 */
  kind?: EmptyKind
  /** 实体名（如「定时任务」「评测记录」） */
  title: string
  description?: string
  /** 出路：跳转目标（可导航硬约束） */
  actionHref?: string
  /** 出路：编程式动作（与 actionHref 二选一或并用） */
  onAction?: () => void
  actionLabel?: string
}

interface EmptyCopy {
  icon: ReactNode
  hint: string
}

/** 纯函数：kind → 默认图标与提示文案（导出供测试） */
export function emptyStateCopy(kind: EmptyKind): EmptyCopy {
  if (kind === 'under_construction') {
    return { icon: '🚧', hint: '此页面正在开发中，先去别处看看' }
  }
  return { icon: '📭', hint: '当前还没有数据' }
}

export default function EmptyState({
  kind = 'no_data',
  title,
  description,
  actionHref,
  onAction,
  actionLabel,
}: EmptyStateProps) {
  const { icon, hint } = emptyStateCopy(kind)
  const ctaLabel = actionLabel ?? '返回管理端首页'

  return (
    <div className="bg-surface-base rounded-xl border border-border-subtle p-12 text-center">
      <div className="text-4xl mb-3" aria-hidden="true">{icon}</div>
      <p className="text-sm font-medium text-text-primary">
        {kind === 'under_construction' ? `「${title}」建设中` : `暂无${title}`}
      </p>
      <p className="text-xs text-text-muted mt-1.5">{description ?? hint}</p>
      {(actionHref || onAction) && (
        <div className="mt-5">
          {actionHref ? (
            <Link
              href={actionHref}
              className="inline-flex items-center px-4 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:opacity-90 transition-opacity"
            >
              {ctaLabel}
            </Link>
          ) : (
            <button
              type="button"
              onClick={onAction}
              className="inline-flex items-center px-4 py-1.5 rounded-lg text-xs font-medium bg-accent text-white hover:opacity-90 transition-opacity"
            >
              {ctaLabel}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
