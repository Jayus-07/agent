'use client'

// cs-workstation-v2 P2：商品中心页（分类 chips + 搜索 + 商品卡 + 详情弹窗 +
// 加购/立即购买/咨询客服预填）。
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import SiteNav from './SiteNav'
import CartModal from './CartModal'
import { useCart } from './cart'
import { listProducts, money, type MallProduct } from './api'

export default function MallPage() {
  const router = useRouter()
  const add = useCart((s) => s.add)
  const [products, setProducts] = useState<MallProduct[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [category, setCategory] = useState<string>('')
  const [q, setQ] = useState('')
  const [detail, setDetail] = useState<MallProduct | null>(null)
  const [cartOpen, setCartOpen] = useState(false)
  const [toast, setToast] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listProducts({ category: category || undefined, q: q || undefined, limit: 56 })
      .then((items) => {
        if (!cancelled) {
          setProducts(items)
          setError(null)
        }
      })
      .catch(() => {
        if (!cancelled) setError('商品加载失败，请稍后重试')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [category, q])

  const categories = useMemo(
    () => Array.from(new Set(products.map((p) => p.category).filter(Boolean))) as string[],
    [products],
  )

  const notify = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(''), 2000)
  }

  return (
    <div className="flex h-screen flex-col bg-slate-100">
      <SiteNav />
      <div className="min-h-0 flex-1 overflow-auto p-5">
        <div className="mb-3 flex flex-wrap items-center gap-2.5">
          <h2 className="text-base text-slate-900">商品中心</h2>
          <span className="rounded-full bg-slate-200/60 px-2 py-0.5 text-[11.5px] text-slate-500">
            数据源：agent_business.product.products
          </span>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-2.5">
          <input
            className="h-8 min-w-52 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 text-xs text-slate-700 outline-none focus:border-indigo-500"
            placeholder="搜索商品名称/品牌/SKU…"
            aria-label="搜索商品"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button
            type="button"
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
            onClick={() => setCartOpen(true)}
          >
            🛒 购物车
          </button>
        </div>
        <div className="mb-3 flex flex-wrap items-center gap-1.5" role="group" aria-label="分类筛选">
          <Chip active={category === ''} onClick={() => setCategory('')}>全部</Chip>
          {categories.map((c) => (
            <Chip key={c} active={category === c} onClick={() => setCategory(c)}>{c}</Chip>
          ))}
        </div>

        {error && <p className="py-10 text-center text-sm text-red-500">{error}</p>}
        {loading && !error && <p className="py-10 text-center text-sm text-slate-400">加载中…</p>}
        {!loading && !error && (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
            {products.map((p) => (
              <div key={p.product_id} className="flex flex-col rounded-xl border border-slate-200 bg-white">
                <button
                  type="button"
                  className="grid h-24 place-items-center rounded-t-xl bg-gradient-to-br from-indigo-100 to-indigo-50 text-3xl"
                  onClick={() => setDetail(p)}
                  aria-label={`查看 ${p.product_name} 详情`}
                >
                  📦
                </button>
                <div className="flex flex-1 flex-col gap-1 p-2.5">
                  <b className="text-[13px] leading-snug text-slate-900">{p.product_name}</b>
                  <div className="flex flex-wrap gap-1.5 text-[11px] text-slate-500">
                    <span>{p.brand || '—'}</span>
                    <span>SKU {p.sku}</span>
                    <span className={p.stock > 0 ? '' : 'text-red-500'}>
                      {p.stock > 0 ? `库存 ${p.stock}` : '缺货'}
                    </span>
                  </div>
                  <div className="mt-auto flex items-center justify-between gap-1.5 pt-1">
                    <span className="text-[15px] text-indigo-600">{money(p.sale_price)}</span>
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        className="rounded-lg border border-slate-200 px-2 py-1 text-[11.5px] text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                        disabled={p.stock <= 0}
                        onClick={() => {
                          add({ productId: p.product_id, name: p.product_name, price: Number(p.sale_price) })
                          notify('已加入购物车')
                        }}
                      >
                        加购
                      </button>
                      <button
                        type="button"
                        className="rounded-lg bg-indigo-600 px-2 py-1 text-[11.5px] text-white hover:opacity-90 disabled:opacity-40"
                        disabled={p.stock <= 0}
                        onClick={() => {
                          add({ productId: p.product_id, name: p.product_name, price: Number(p.sale_price) })
                          setCartOpen(true)
                        }}
                      >
                        立即购买
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 详情弹窗 */}
      {detail && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/40" onClick={() => setDetail(null)}>
          <div
            className="w-[420px] max-w-[calc(100%-32px)] rounded-xl bg-white p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-label="商品详情"
          >
            <h3 className="mb-3 text-sm text-slate-900">商品详情</h3>
            <div className="flex flex-col gap-1.5 text-xs text-slate-600">
              <b className="text-sm text-slate-900">{detail.product_name}</b>
              <Kv k="品牌" v={detail.brand || '—'} />
              <Kv k="SKU" v={detail.sku} />
              <Kv k="分类" v={detail.category || '—'} />
              <Kv k="价格" v={money(detail.sale_price)} />
              <Kv k="库存" v={String(detail.stock)} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                onClick={() => setDetail(null)}
              >
                关闭
              </button>
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                disabled={detail.stock <= 0}
                onClick={() => {
                  add({ productId: detail.product_id, name: detail.product_name, price: Number(detail.sale_price) })
                  setDetail(null)
                  notify('已加入购物车')
                }}
              >
                加入购物车
              </button>
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
                onClick={() => {
                  window.sessionStorage.setItem('cs2-prefill', `我想咨询一下「${detail.product_name}」（SKU ${detail.sku}）`)
                  router.push('/customer-service-v2')
                }}
              >
                咨询客服
              </button>
              <button
                type="button"
                className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-xs text-white hover:opacity-90 disabled:opacity-40"
                disabled={detail.stock <= 0}
                onClick={() => {
                  add({ productId: detail.product_id, name: detail.product_name, price: Number(detail.sale_price) })
                  setDetail(null)
                  setCartOpen(true)
                }}
              >
                立即购买
              </button>
            </div>
          </div>
        </div>
      )}

      <CartModal open={cartOpen} onClose={() => setCartOpen(false)} />
      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-slate-900 px-3.5 py-2 text-xs text-white">
          {toast}
        </div>
      )}
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      className={`rounded-full border px-3 py-1 text-xs ${
        active ? 'border-indigo-500 bg-indigo-50 text-indigo-600' : 'border-slate-200 bg-slate-50 text-slate-500'
      }`}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2.5 border-b border-dashed border-slate-100 py-1">
      <span className="text-slate-400">{k}</span>
      <span className="text-right text-slate-800">{v}</span>
    </div>
  )
}
