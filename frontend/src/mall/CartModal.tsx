'use client'

// cs-workstation-v2 P2：购物车弹窗（移除/清空/合计/结算生成待付款订单）。
import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { cartTotal, useCart } from './cart'
import { createOrder, money } from './api'

export default function CartModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter()
  const items = useCart((s) => s.items)
  const remove = useCart((s) => s.remove)
  const clear = useCart((s) => s.clear)
  const clearStore = useCart((s) => s.clear)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!open) return null
  const total = cartTotal(items)

  const checkout = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await createOrder(items.map((i) => ({ product_id: i.productId, quantity: i.quantity })))
      clearStore()
      onClose()
      router.push('/my-orders')
    } catch {
      setError('结算失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40" onClick={onClose}>
      <div
        className="w-[460px] max-w-[calc(100%-32px)] max-h-[calc(100%-32px)] overflow-auto rounded-xl bg-white p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="购物车"
      >
        <h3 className="mb-3 text-sm text-slate-900">购物车</h3>
        {items.length === 0 ? (
          <p className="py-8 text-center text-xs text-slate-400">购物车是空的，去商品中心逛逛吧。</p>
        ) : (
          <div className="flex flex-col gap-2">
            {items.map((i) => (
              <div key={i.productId} className="flex items-center gap-2.5 rounded-lg border border-slate-100 px-2.5 py-2 text-xs">
                <div className="min-w-0 flex-1">
                  <b className="block truncate text-slate-900">{i.name}</b>
                  <span className="text-slate-500">{money(i.price)} × {i.quantity}</span>
                </div>
                <button
                  type="button"
                  className="text-[11px] text-slate-400 hover:text-red-500"
                  onClick={() => remove(i.productId)}
                >
                  移除
                </button>
              </div>
            ))}
          </div>
        )}
        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
        <div className="mt-3 flex items-center gap-2.5">
          <span className="text-xs text-slate-500">合计</span>
          <b className="text-[15px] text-indigo-600">{money(total)}</b>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
              onClick={clear}
            >
              清空
            </button>
            <button
              type="button"
              className="rounded-lg bg-indigo-600 px-2.5 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-40"
              disabled={items.length === 0 || submitting}
              onClick={() => void checkout()}
            >
              {submitting ? '结算中…' : '去结算'}
            </button>
          </div>
        </div>
        <div className="mt-3 flex justify-end">
          <button type="button" className="text-xs text-slate-400 hover:text-slate-600" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
