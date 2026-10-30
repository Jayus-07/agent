'use client'

// cs-workstation-v2 P2：用户端站点导航（智能客服/商品中心/我的订单 + 购物车徽章），
// 由客服 v2、商品中心、我的订单三页共用（对照 prototypes/user-portal.html 顶栏）。
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCart } from './cart'

const NAV_ITEMS: Array<{ href: string; label: string; mock?: boolean }> = [
  { href: '/customer-service-v2', label: '智能客服' },
  { href: '/mall', label: '商品中心', mock: true },
  { href: '/my-orders', label: '我的订单', mock: true },
]

export default function SiteNav() {
  const pathname = usePathname()
  const count = useCart((s) => s.items.reduce((n, i) => n + i.quantity, 0))

  return (
    <header className="flex h-12 flex-none items-center gap-2.5 border-b border-slate-200 bg-white px-4">
      <span className="grid h-6 w-6 place-items-center rounded-lg bg-indigo-600 text-xs text-white">演</span>
      <b className="text-sm text-slate-900">演示商城</b>
      <span className="rounded-full border border-slate-200 px-2 py-px text-[11px] text-slate-500">🧪 演示数据</span>
      <nav className="ml-3 flex gap-1" aria-label="站点导航">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={`rounded-lg px-2.5 py-1.5 text-xs ${
                active ? 'bg-indigo-50 text-indigo-600' : 'text-slate-500 hover:bg-slate-50'
              }`}
            >
              {item.label}
              {item.mock && (
                <i className="ml-1 rounded-full border border-slate-200 px-1 text-[10px] not-italic text-slate-400">
                  模拟
                </i>
              )}
            </Link>
          )
        })}
      </nav>
      <div className="ml-auto flex items-center gap-2.5">
        <Link
          href="/my-orders?cart=1"
          className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
        >
          🛒 购物车
          <b className="ml-1 not-italic text-indigo-600">{count}</b>
        </Link>
        <span className="grid h-7 w-7 place-items-center rounded-full bg-indigo-600 text-xs text-white" aria-label="演示用户">
          演
        </span>
      </div>
    </header>
  )
}
