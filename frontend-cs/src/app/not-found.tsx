import Link from 'next/link'
import { Compass } from 'lucide-react'

/**
 * 全局 404 — 管理端品牌化兜底页（替换 Next 默认英文 404）。
 * 保持「AI Native 简约商务风」：居中卡片、主色按钮返回总览。
 */
export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center px-6">
      <div className="flex flex-col items-center text-center py-12">
        <div className="w-14 h-14 rounded-2xl bg-accent/8 flex items-center justify-center mb-5">
          <Compass size={28} className="text-accent" strokeWidth={1.5} />
        </div>
        <div className="flex items-baseline gap-3">
          <span className="text-4xl font-bold text-text-primary tracking-tight">404</span>
          <span className="text-sm text-text-secondary">页面不存在或已被移动</span>
        </div>
        <p className="text-xs text-text-muted mt-2 max-w-sm leading-relaxed">
          请检查左侧导航或从运营总览重新出发；若为书签链接，可能对应功能已归入子菜单。
        </p>
        <Link
          href="/"
          className="mt-6 inline-flex items-center gap-1.5 rounded-lg bg-accent px-4 py-2 text-[13px]
            font-medium text-white shadow-sm hover:bg-accent-hover active:scale-[0.99] transition-all"
        >
          返回运营总览
        </Link>
      </div>
    </div>
  )
}
