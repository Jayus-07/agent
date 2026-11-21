'use client'

// cs-workstation-v2 P2：移动端底部三入口 Tab（智能客服/商品中心/我的订单），
// 仅 <md 显示（≥md 由 SiteNav 顶部导航承担）。条目与 SiteNav 各自维护
// （移动端 label 语境不同：客服/商城/订单 vs 智能客服/商品中心/我的订单），
// 路由目标保持一致靠约定；safe-area 适配，客服页不用（保持沉浸式聊天布局，导航在顶栏）。
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Headphones, Package, ShoppingBag } from 'lucide-react'

const TAB_ITEMS = [
  { href: '/customer-service', label: '客服', Icon: Headphones },
  { href: '/mall', label: '商城', Icon: ShoppingBag },
  { href: '/my-orders', label: '订单', Icon: Package },
] as const

export default function SiteTabBar() {
  const pathname = usePathname()
  return (
    <nav
      aria-label="底部导航"
      className="fixed inset-x-0 bottom-0 z-30 flex border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {TAB_ITEMS.map(({ href, label, Icon }) => {
        const active = pathname === href
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? 'page' : undefined}
            className={`flex flex-1 flex-col items-center gap-0.5 py-2 text-[10.5px] ${
              active ? 'text-indigo-600' : 'text-slate-500'
            }`}
          >
            <Icon size={19} aria-hidden />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}
