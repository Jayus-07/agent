'use client'

// cs-workstation-v2 P2：我的订单页（状态 Tab 含已退款；去支付/未付款直接取消/
// 物流轨迹弹窗/申请退款·咨询此订单预填；资金闭环与客服确认联动）。
import { Suspense, useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useRouter } from 'next/navigation'
import SiteNav from './SiteNav'
import CartModal from './CartModal'
import {
  cancelUnpaidOrder,
  getWallet,
  listMyOrders,
  money,
  payOrder,
  ORDER_STATUS_ZH,
  type MallOrder,
  type WalletView,
} from './api'

const TABS: Array<{ key: string; label: string }> = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待付款' },
  { key: 'paid', label: '待发货' },
  { key: 'shipped', label: '已发货' },
  { key: 'completed', label: '已完成' },
  { key: 'refunded', label: '已退款' },
  { key: 'cancelled', label: '已取消' },
]

function statusBadgeCls(status: string): string {
  if (status === 'completed') return 'bg-emerald-50 text-emerald-600'
  if (status === 'refunded' || status === 'cancelled') return 'bg-slate-100 text-slate-500'
  if (status === 'pending') return 'bg-amber-50 text-amber-600'
  return 'bg-blue-50 text-blue-600'
}

function MyOrdersInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [orders, setOrders] = useState<MallOrder[]>([])
  const [wallet, setWallet] = useState<WalletView | null>(null)
  const [tab, setTab] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [cartOpen, setCartOpen] = useState(searchParams.get('cart') === '1')
  const [logisticsFor, setLogisticsFor] = useState<string | null>(null)
  const [busyOrder, setBusyOrder] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const reload = useCallback(() => {
    setLoading(true)
    Promise.all([listMyOrders(30), getWallet().catch(() => null)])
      .then(([orderList, walletView]) => {
        setOrders(orderList)
        setWallet(walletView)
        setError(null)
      })
      .catch(() => setError('订单加载失败，请稍后重试'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const notify = (msg: string) => {
    setNotice(msg)
    window.setTimeout(() => setNotice(null), 2600)
  }

  const prefillCs = (orderNo: string, kind: 'refund' | 'consult') => {
    const text = kind === 'refund'
      ? `我要为订单 ${orderNo} 申请退款`
      : `我想咨询订单 ${orderNo} 的情况`
    window.sessionStorage.setItem('cs2-prefill', text)
    router.push('/customer-service-v2')
  }

  const doPay = async (order: MallOrder) => {
    if (order.total_amount == null) return
    setBusyOrder(order.order_no)
    try {
      await payOrder(order.order_no, order.total_amount)
      notify(`订单 ${order.order_no} 支付成功，货款已从余额扣除`)
      reload()
    } catch {
      notify('支付失败：余额不足或订单状态已变化')
      reload()
    } finally {
      setBusyOrder(null)
    }
  }

  const doCancel = async (order: MallOrder) => {
    setBusyOrder(order.order_no)
    try {
      await cancelUnpaidOrder(order.order_no)
      notify(`订单 ${order.order_no} 已取消`)
      reload()
    } catch {
      notify('取消失败：已支付订单请通过客服会话申请取消')
      reload()
    } finally {
      setBusyOrder(null)
    }
  }

  const filtered = tab ? orders.filter((o) => o.status === tab) : orders

  return (
    <div className="flex h-screen flex-col bg-slate-100">
      <SiteNav />
      <div className="min-h-0 flex-1 overflow-auto p-5">
        <div className="mb-3 flex flex-wrap items-center gap-2.5">
          <h2 className="text-base text-slate-900">我的订单</h2>
          <span className="rounded-full bg-slate-200/60 px-2 py-0.5 text-[11.5px] text-slate-500">
            演示用户（模拟数据）
          </span>
          {wallet && (
            <span className="rounded-lg bg-indigo-50 px-2.5 py-1 text-xs text-indigo-600">
              余额 <b>{money(wallet.balance)}</b>
              <button
                type="button"
                className="ml-2 rounded bg-indigo-600 px-1.5 py-0.5 text-[11px] text-white hover:opacity-90"
                onClick={() => {
                  void (async () => {
                    try {
                      const { topUp } = await import('./api')
                      setWallet(await topUp(1000))
                      notify('充值成功（演示）：+¥1,000.00 已入账')
                    } catch {
                      notify('充值失败，请稍后重试')
                    }
                  })()
                }}
              >
                充值 ¥1,000
              </button>
            </span>
          )}
        </div>
        {wallet && wallet.ledger.length > 0 && (
          <div className="mb-3 rounded-xl border border-slate-200 bg-white p-3">
            <div className="mb-1.5 text-[11.5px] text-slate-500">资金流水（最近 {wallet.ledger.length} 笔）</div>
            {wallet.ledger.map((l, i) => (
              <div key={`${l.created_at}-${i}`} className="flex items-center justify-between gap-2 border-b border-dashed border-slate-100 py-1 text-xs last:border-b-0">
                <span className="text-slate-600">
                  {l.memo || (l.kind === 'pay' ? '订单支付' : l.kind === 'topup' ? '充值' : '退款入账')}
                  <span className="ml-2 text-[10.5px] text-slate-400">{l.created_at?.slice(5, 16).replace('T', ' ')}</span>
                </span>
                <b className={l.direction === 'in' ? 'text-emerald-600' : 'text-slate-700'}>
                  {l.direction === 'in' ? '+' : '−'}{money(l.amount)}
                </b>
              </div>
            ))}
          </div>
        )}
        <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="订单状态筛选">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              className={`rounded-full border px-3 py-1 text-xs ${
                tab === t.key ? 'border-indigo-500 bg-indigo-50 text-indigo-600' : 'border-slate-200 bg-white text-slate-500'
              }`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {error && <p className="py-10 text-center text-sm text-red-500">{error}</p>}
        {loading && !error && <p className="py-10 text-center text-sm text-slate-400">加载中…</p>}
        {!loading && !error && filtered.length === 0 && (
          <p className="py-10 text-center text-sm text-slate-400">当前状态暂无订单。</p>
        )}
        <div className="flex flex-col gap-2.5">
          {filtered.map((o) => (
            <div key={o.order_no} className="rounded-xl border border-slate-200 bg-white px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-2.5 border-b border-dashed border-slate-100 pb-2">
                <b className="text-[13px] text-slate-900">{o.order_no}</b>
                <span className={`rounded-full px-2 py-0.5 text-[11px] ${statusBadgeCls(o.status)}`}>
                  {ORDER_STATUS_ZH[o.status] ?? o.status}
                </span>
                <span className="ml-auto text-[11.5px] text-slate-400">
                  {o.created_at?.slice(0, 16).replace('T', ' ')}
                </span>
              </div>
              <div className="py-2 text-xs text-slate-500">{o.product_summary || '—'}</div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[15px] text-indigo-600">
                  {o.total_amount != null ? money(o.total_amount) : '—'}
                </span>
                <div className="ml-auto flex flex-wrap gap-2">
                  {o.status === 'pending' && (
                    <>
                      <button
                        type="button"
                        className="rounded-lg bg-indigo-600 px-2.5 py-1 text-[11.5px] text-white hover:opacity-90 disabled:opacity-40"
                        disabled={busyOrder === o.order_no}
                        onClick={() => void doPay(o)}
                      >
                        去支付
                      </button>
                      <button
                        type="button"
                        className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11.5px] text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                        disabled={busyOrder === o.order_no}
                        onClick={() => void doCancel(o)}
                      >
                        取消订单
                      </button>
                    </>
                  )}
                  {(o.status === 'shipped' || o.status === 'completed') && (
                    <button
                      type="button"
                      className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11.5px] text-slate-600 hover:bg-slate-50"
                      onClick={() => setLogisticsFor(o.order_no)}
                    >
                      物流轨迹
                    </button>
                  )}
                  {o.status === 'paid' && (
                    <button
                      type="button"
                      className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11.5px] text-slate-600 hover:bg-slate-50"
                      onClick={() => prefillCs(o.order_no, 'refund')}
                      title="已支付订单取消/退款走客服提案-确认"
                    >
                      申请退款/取消
                    </button>
                  )}
                  <button
                    type="button"
                    className="rounded-lg border border-slate-200 px-2.5 py-1 text-[11.5px] text-slate-600 hover:bg-slate-50"
                    onClick={() => prefillCs(o.order_no, 'consult')}
                  >
                    咨询此订单
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {logisticsFor && <LogisticsModal orderNo={logisticsFor} onClose={() => setLogisticsFor(null)} />}
      <CartModal open={cartOpen} onClose={() => setCartOpen(false)} />
      {notice && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-3.5 py-2 text-xs text-white">
          {notice}
        </div>
      )}
    </div>
  )
}

function LogisticsModal({ orderNo, onClose }: { orderNo: string; onClose: () => void }) {
  const [data, setData] = useState<{
    shipping_status: string
    status_display: string
    carrier: string | null
    tracking_no: string | null
    tracking_info: string
    events: Array<{ time: string; location: string; description: string }>
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    import('./api')
      .then((m) => m.getOrderLogistics(orderNo))
      .then((view) => {
        if (!cancelled) setData(view)
      })
      .catch(() => {
        if (!cancelled) setError('物流信息加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [orderNo])

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40" onClick={onClose}>
      <div
        className="w-[460px] max-w-[calc(100%-32px)] max-h-[calc(100%-32px)] overflow-auto rounded-xl bg-white p-4 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="物流轨迹"
      >
        <h3 className="mb-3 text-sm text-slate-900">物流轨迹 · {orderNo}</h3>
        {error && <p className="py-6 text-center text-xs text-red-500">{error}</p>}
        {!data && !error && <p className="py-6 text-center text-xs text-slate-400">加载中…</p>}
        {data && (
          <div className="text-xs">
            <div className="mb-2 rounded-lg bg-slate-50 px-2.5 py-2 text-slate-600">
              {data.status_display} · {data.tracking_info}
              {data.carrier && <span className="ml-2 text-slate-400">{data.carrier} {data.tracking_no}</span>}
            </div>
            {data.events.length === 0 ? (
              <p className="py-4 text-center text-slate-400">暂无配送轨迹（未发货）</p>
            ) : (
              data.events.map((e, i) => (
                <div key={i} className="relative py-1.5 pl-4 text-slate-600">
                  <span className="absolute left-0.5 top-2.5 h-2 w-2 rounded-full bg-indigo-500" aria-hidden />
                  <b className="mr-1.5 font-normal text-slate-900">{e.time?.slice(5, 16).replace('T', ' ')}</b>
                  {e.location} · {e.description}
                </div>
              ))
            )}
          </div>
        )}
        <div className="mt-3 flex justify-end">
          <button type="button" className="text-xs text-slate-400 hover:text-slate-600" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}

export default function MyOrdersPage() {
  return (
    <Suspense fallback={<div className="grid h-screen place-items-center text-sm text-slate-400">加载中…</div>}>
      <MyOrdersInner />
    </Suspense>
  )
}
