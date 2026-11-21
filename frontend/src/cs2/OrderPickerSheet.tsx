'use client'

// cs2 M1：订单选择抽屉（淘宝「发订单给客服」式，参考 docs/tasks/cs-order-consult-mobile/TASK.md）。
// 双 Tab：我的订单（GET /api/mall/orders，服务端资源级授权）+ 常见问题（快捷话术）。
// 发送 = 结构化文本（【订单咨询】…），AI 与坐席均可见；M2 换 content_type='card' 落库。
// 客户端只传订单号引用，订单数据全部来自本人授权接口，不涉及他人订单。
import { useEffect, useMemo, useState } from 'react'
import { Package, RotateCcw, Search, X } from 'lucide-react'
import { listMyOrders, ORDER_STATUS_ZH, money, type MallOrder } from '@/mall/api'

export interface OrderPickerSheetProps {
  open: boolean
  onClose: () => void
  /** 点「发送」把订单带进会话（页面侧负责拼结构化文本并走既有发送通道） */
  onSendOrder: (order: MallOrder) => void
  /** 「常见问题」Tab 话术（复用页面 QUICK_ENTRIES，单一事实源留在页面） */
  faqEntries: ReadonlyArray<{ icon: string; title: string; text: string }>
  onSendText: (text: string) => void
  /** 深链预选订单号（商城「咨询此订单」）：命中仅高亮，需用户点发送确认 */
  preselectOrderNo?: string | null
}

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-600',
  paid: 'bg-blue-50 text-blue-600',
  shipped: 'bg-emerald-50 text-emerald-600',
  completed: 'bg-slate-100 text-slate-500',
  cancelled: 'bg-slate-100 text-slate-400',
  refunded: 'bg-rose-50 text-rose-500',
}

export default function OrderPickerSheet({
  open,
  onClose,
  onSendOrder,
  faqEntries,
  onSendText,
  preselectOrderNo,
}: OrderPickerSheetProps) {
  const [tab, setTab] = useState<'orders' | 'faq'>('orders')
  const [orders, setOrders] = useState<MallOrder[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [keyword, setKeyword] = useState('')

  // 打开时拉取本人订单；关闭不保留上次数据（下次打开重新拉，避免跨会话陈旧）
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setTab('orders')
    setKeyword('')
    setLoadError(false)
    setOrders(null)
    listMyOrders(20)
      .then((rows) => {
        if (!cancelled) setOrders(rows)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const filtered = useMemo(() => {
    if (!orders) return []
    const kw = keyword.trim().toLowerCase()
    if (!kw) return orders
    return orders.filter(
      (o) =>
        o.order_no.toLowerCase().includes(kw) ||
        (o.product_summary ?? '').toLowerCase().includes(kw),
    )
  }, [orders, keyword])

  if (!open) return null

  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} aria-hidden />
      <aside
        role="dialog"
        aria-label="订单选择"
        className="fixed inset-x-0 bottom-0 z-50 flex max-h-[78vh] flex-col rounded-t-2xl bg-white pb-[env(safe-area-inset-bottom)] shadow-2xl sm:inset-x-auto sm:left-1/2 sm:w-[460px] sm:-translate-x-1/2"
      >
        {/* 拖拽条 + 关闭 */}
        <div className="flex flex-none flex-col items-center pt-2">
          <span className="h-1 w-10 rounded-full bg-slate-200" aria-hidden />
          <button
            type="button"
            aria-label="关闭订单选择"
            className="absolute right-3 top-3 grid h-8 w-8 place-items-center rounded-full text-slate-400 hover:bg-slate-100"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </div>

        {/* 双 Tab */}
        <div className="mt-1.5 flex flex-none gap-6 px-5" role="tablist" aria-label="订单问题分类">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'orders'}
            className={`border-b-2 pb-2 text-sm ${tab === 'orders' ? 'border-indigo-600 font-medium text-slate-900' : 'border-transparent text-slate-500'}`}
            onClick={() => setTab('orders')}
          >
            我的订单
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'faq'}
            className={`border-b-2 pb-2 text-sm ${tab === 'faq' ? 'border-indigo-600 font-medium text-slate-900' : 'border-transparent text-slate-500'}`}
            onClick={() => setTab('faq')}
          >
            常见问题
          </button>
        </div>

        {tab === 'orders' ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* 价值横幅 */}
            <div className="mx-4 mt-2.5 flex-none rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
              🤝 带着订单咨询，问题解决快人一步
            </div>
            {/* 本地过滤：本人订单 ≤20 条，无需服务端搜索 */}
            <div className="mx-4 mt-2.5 flex flex-none items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3">
              <Search size={15} className="shrink-0 text-slate-400" aria-hidden />
              <input
                type="search"
                className="h-9 w-full bg-transparent text-sm text-slate-800 outline-none"
                placeholder="搜订单号 / 商品关键词"
                aria-label="搜索订单"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </div>

            {/* 订单列表 */}
            <div className="mt-2.5 min-h-0 flex-1 overflow-y-auto px-4 pb-2 [touch-action:pan-y]">
              {loadError ? (
                <div className="flex flex-col items-center gap-2 py-8 text-xs text-slate-500">
                  订单加载失败
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 rounded-full border border-slate-200 px-3 py-1.5 text-indigo-600 hover:bg-indigo-50"
                    onClick={() => {
                      setLoadError(false)
                      setOrders(null)
                      listMyOrders(20)
                        .then((rows) => setOrders(rows))
                        .catch(() => setLoadError(true))
                    }}
                  >
                    <RotateCcw size={13} /> 重试
                  </button>
                </div>
              ) : orders === null ? (
                // 骨架 3 行
                <div className="flex flex-col gap-2 pt-1" aria-label="订单加载中">
                  {[0, 1, 2].map((i) => (
                    <div key={i} className="h-[74px] animate-pulse rounded-xl bg-slate-100" />
                  ))}
                </div>
              ) : filtered.length === 0 ? (
                <div className="py-10 text-center text-xs text-slate-400">
                  {keyword ? '没有匹配的订单' : '您还没有订单'}
                </div>
              ) : (
                filtered.map((o) => {
                  const statusZh = ORDER_STATUS_ZH[o.status] ?? o.status
                  const preselected = preselectOrderNo === o.order_no
                  return (
                    <div
                      key={o.order_no}
                      className={`mb-2 rounded-xl border px-3 py-2.5 ${
                        preselected ? 'border-indigo-500 bg-indigo-50/50' : 'border-slate-200'
                      }`}
                      data-order-no={o.order_no}
                      aria-selected={preselected || undefined}
                    >
                      <div className="flex items-center gap-2">
                        <b className="min-w-0 flex-1 truncate text-[13px] text-slate-900">{o.order_no}</b>
                        <span
                          className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] ${
                            STATUS_BADGE[o.status] ?? 'bg-slate-100 text-slate-500'
                          }`}
                        >
                          {statusZh}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center gap-2">
                        <Package size={26} className="shrink-0 text-slate-300" aria-hidden />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-xs text-slate-600">{o.product_summary}</div>
                          <div className="mt-0.5 text-[10.5px] text-slate-400">
                            {o.created_at ? o.created_at.slice(0, 10) : ''}
                          </div>
                        </div>
                        <div className="flex flex-none flex-col items-end gap-1">
                          <b className="text-[13px] text-slate-900">{money(o.total_amount)}</b>
                          <button
                            type="button"
                            className="rounded-full bg-indigo-600 px-3 py-1 text-[11.5px] text-white hover:bg-indigo-700"
                            onClick={() => onSendOrder(o)}
                          >
                            发送
                          </button>
                        </div>
                      </div>
                    </div>
                  )
                })
              )}
            </div>

            {/* 兜底：没有对应订单 → 关抽屉直接文字描述（淘宝同款） */}
            <div className="flex-none px-4 pb-3 pt-1">
              <button
                type="button"
                className="w-full rounded-full border border-slate-200 py-2 text-xs text-slate-500 hover:bg-slate-50"
                onClick={onClose}
              >
                没有对应订单
              </button>
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="mt-2.5 min-h-0 flex-1 overflow-y-auto px-4 pb-2 [touch-action:pan-y]">
              {faqEntries.map((q) => (
                <button
                  key={q.title}
                  type="button"
                  className="mb-2 w-full rounded-xl border border-slate-200 px-3.5 py-2.5 text-left text-[13px] text-slate-700 hover:border-indigo-400 hover:text-indigo-600"
                  onClick={() => onSendText(q.text)}
                >
                  {q.icon} {q.title}
                </button>
              ))}
            </div>
            <div className="flex-none px-4 pb-3 pt-1">
              <button
                type="button"
                className="w-full rounded-full border border-slate-200 py-2 text-xs text-slate-500 hover:bg-slate-50"
                onClick={onClose}
              >
                没有我想问的问题
              </button>
            </div>
          </div>
        )}
      </aside>
    </>
  )
}
