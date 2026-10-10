'use client'

/**
 * /customer-service — 独立的用户智能客服工作区。
 * 左侧复用客服会话流程，右侧读取当前登录用户的档案和近期订单。
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { X } from 'lucide-react'
import CSDrawer from '@/components/cs/CSDrawer'
import { getMyCustomerContext, type MyCustomerContext } from '@/api/cs'
import { ApiError } from '@/api/client'

const ORDER_STATUS_LABELS: Record<string, string> = {
  pending: '待付款',
  paid: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunded: '已退款',
}

function formatDate(value?: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })
}

function formatAmount(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return '金额未知'
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    maximumFractionDigits: 2,
  }).format(value)
}

function levelLabel(value?: string | null): string {
  if (!value) return '未登记'
  const labels: Record<string, string> = {
    gold: '黄金会员',
    silver: '白银会员',
    bronze: '青铜会员',
    vip: 'VIP',
  }
  return labels[value.toLowerCase()] || value
}

function CustomerContextPanel({
  context,
  loading,
  error,
  onRetry,
  onClose,
}: {
  context: MyCustomerContext | null
  loading: boolean
  error: string | null
  onRetry: () => void
  onClose?: () => void
}) {
  const customer = context?.customer

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto p-4 sm:p-5">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#1d2822]">客户资料</h2>
          <p className="mt-1 text-xs leading-5 text-[#738078]">助手可参考当前账号的订单信息回答问题</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {(context?.demo_mode || context?.mock_data) && (
            <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700">
              模拟数据
            </span>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭客户资料"
              className="flex h-8 w-8 items-center justify-center rounded-lg text-[#67746c] hover:bg-black/5 lg:hidden"
            >
              <X size={17} />
            </button>
          )}
        </div>
      </header>

      <section className="mt-5 rounded-2xl border border-[#e3e9e4] bg-white p-4">
        <div className="flex items-center gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#e8f3eb] text-base font-semibold text-[#287448]">
            {customer?.display_name?.trim().slice(0, 1) || '客'}
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold text-[#25332a]">
              {loading ? '读取中…' : customer?.display_name || '当前用户'}
            </h3>
            <p className="mt-1 text-xs text-[#77837b]">
              {customer?.level ? levelLabel(customer.level) : '已登录用户'}
            </p>
          </div>
        </div>
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[#edf0ed] pt-3 text-xs">
          <div>
            <dt className="text-[#87928b]">会员等级</dt>
            <dd className="mt-1 font-medium text-[#354239]">{levelLabel(customer?.level)}</dd>
          </div>
          {customer?.gender && (
            <div>
              <dt className="text-[#87928b]">性别</dt>
              <dd className="mt-1 font-medium text-[#354239]">{customer.gender}</dd>
            </div>
          )}
          {customer?.register_time && (
            <div className="col-span-2">
              <dt className="text-[#87928b]">注册时间</dt>
              <dd className="mt-1 font-medium text-[#354239]">{formatDate(customer.register_time)}</dd>
            </div>
          )}
        </dl>
      </section>

      {context?.profile_unavailable && (
        <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800">
          账户档案暂时无法读取，订单信息仍按当前登录账号查询。
        </p>
      )}

      <section className="mt-6 min-h-0 flex-1">
        <div className="flex items-center justify-between border-b border-[#e2e8e3] pb-3">
          <h3 className="text-sm font-semibold text-[#25332a]">最近订单</h3>
          <span className="text-xs text-[#87928b]">
            {context ? `${context.orders.length} 笔` : ''}
          </span>
        </div>

        {loading ? (
          <p className="py-6 text-center text-xs text-[#87928b]">正在读取订单…</p>
        ) : error ? (
          <div className="py-6 text-center">
            <p className="text-xs text-[#68756d]">暂时无法读取客户资料和订单</p>
            <p className="mx-auto mt-2 max-w-xs break-words text-[10px] leading-4 text-[#9a625a]">{error}</p>
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 rounded-lg border border-[#d9e3db] bg-white px-3 py-1.5 text-xs font-medium text-[#287448] hover:bg-[#f6faf6]"
            >
              重新加载
            </button>
          </div>
        ) : context?.orders.length ? (
          <ul className="divide-y divide-[#e9eeea]">
            {context.orders.map((order) => (
              <li key={order.id} className="py-4">
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-xs font-medium text-[#354239]">{order.order_no}</span>
                  <span className="shrink-0 rounded-full bg-[#eef5ef] px-2 py-1 text-[10px] font-medium text-[#287448]">
                    {ORDER_STATUS_LABELS[order.status] || order.status}
                  </span>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-[#68756d]">
                  {order.product_summary || '暂无商品明细'}
                </p>
                <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-[#87928b]">
                  <span>{formatDate(order.created_at)}</span>
                  <span className="font-medium text-[#354239]">{formatAmount(order.total_amount)}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="py-6 text-center text-xs leading-5 text-[#87928b]">
            当前账号还没有订单记录
          </p>
        )}
      </section>

      {(context?.demo_mode || context?.mock_data) && (
        <p className="mt-4 rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800">
          当前环境使用模拟业务数据，订单信息仅用于体验客服查询和售后流程。
        </p>
      )}
    </div>
  )
}

export default function CustomerServicePage() {
  const router = useRouter()
  const [context, setContext] = useState<MyCustomerContext | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mobileContextOpen, setMobileContextOpen] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    getMyCustomerContext()
      .then((result) => {
        if (active) setContext(result)
      })
      .catch((err: unknown) => {
        if (!active) return
        if (err instanceof ApiError) {
          setError(`${err.message}（HTTP ${err.status}）`)
        } else {
          setError(err instanceof Error ? err.message : '请求失败，请检查网络后重试')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [reloadKey])

  const retry = () => setReloadKey((value) => value + 1)
  const panelProps = { context, loading, error, onRetry: retry }

  return (
    <main className="min-h-[100dvh] overflow-hidden bg-[#f3f6f4]">
      <div className="mx-auto flex h-[100dvh] w-full max-w-[1440px]">
        <section className="min-w-0 flex-1">
          <CSDrawer
            open
            standalone
            onClose={() => router.push('/')}
            onOpenCustomerContext={() => setMobileContextOpen(true)}
          />
        </section>
        <aside className="hidden h-[100dvh] w-[340px] shrink-0 border-l border-[#e1e8e2] bg-[#f7faf7] lg:block">
          <CustomerContextPanel {...panelProps} />
        </aside>
      </div>

      {mobileContextOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-end bg-black/35 lg:hidden"
          role="presentation"
          onClick={() => setMobileContextOpen(false)}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-label="客户资料和订单"
            className="h-[84dvh] max-h-[720px] w-full rounded-t-3xl border-t border-[#e1e8e2] bg-[#f7faf7] shadow-2xl"
            onClick={(event) => event.stopPropagation()}
          >
            <CustomerContextPanel
              {...panelProps}
              onClose={() => setMobileContextOpen(false)}
            />
          </section>
        </div>
      )}
    </main>
  )
}
