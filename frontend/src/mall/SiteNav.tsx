'use client'

// cs-workstation-v2 P2：用户端站点导航（智能客服/商品中心/我的订单 + 购物车徽章），
// 由客服 v2、商品中心、我的订单三页共用（对照 prototypes/user-portal.html 顶栏）。
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useCart } from './cart'

const NAV_ITEMS: Array<{ href: string; label: string; mock?: boolean }> = [
  { href: '/customer-service', label: '智能客服' },
  { href: '/mall', label: '商品中心', mock: true },
  { href: '/my-orders', label: '我的订单', mock: true },
]

export default function SiteNav({
  // 客服页传入：头像变为「个人中心」抽屉入口并显示客户昵称首字；
  // 商城/订单页不传，维持静态演示头像
  avatarOnClick,
  avatarChar,
}: {
  avatarOnClick?: () => void
  avatarChar?: string
}) {
  const pathname = usePathname()
  const count = useCart((s) => s.items.reduce((n, i) => n + i.quantity, 0))

  return (
    <header className="flex h-12 flex-none min-w-0 items-center gap-2.5 border-b border-slate-200 bg-white px-4">
      <Link href="/" aria-label="回到门户" className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-indigo-600 text-xs text-white">演</Link>
      <b className="shrink-0 text-sm text-slate-900">演示商城</b>
      <span className="hidden rounded-full border border-slate-200 px-2 py-px text-[11px] text-slate-500 sm:inline-flex">🧪 演示数据</span>
      <nav className="ml-3 flex min-w-0 gap-1 overflow-x-auto" aria-label="站点导航">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={`shrink-0 rounded-lg px-2.5 py-1.5 text-xs ${
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
      <div className="ml-auto flex shrink-0 items-center gap-2.5">
        <Link
          href="/my-orders?cart=1"
          className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
        >
          🛒 购物车
          <b className="ml-1 not-italic text-indigo-600">{count}</b>
        </Link>
        {avatarOnClick ? (
          <button
            type="button"
            onClick={avatarOnClick}
            aria-label="打开个人中心"
            title="打开个人中心"
            className="grid h-7 w-7 place-items-center rounded-full bg-indigo-600 text-xs text-white hover:opacity-90"
          >
            {avatarChar || '演'}
          </button>
        ) : (
          <span className="grid h-7 w-7 place-items-center rounded-full bg-indigo-600 text-xs text-white" aria-label="演示用户">
            演
          </span>
        )}
      </div>
    </header>
  )
}
