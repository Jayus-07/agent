// cs-workstation-v2 P2：商城演示域 API（frontend/src/mall，经 BFF 代理）。
import { request } from '@/api/client'

export interface MallProduct {
  product_id: string
  sku: string
  product_name: string
  brand: string | null
  sale_price: string | number
  category: string | null
  stock: number
}

export interface WalletLedgerEntry {
  direction: 'in' | 'out' | string
  amount: number
  balance_after?: number | null
  kind: string
  ref_no?: string
  memo: string
  created_at: string | null
}

export interface WalletView {
  balance: number
  currency: string
  updated_at?: string | null
  ledger: WalletLedgerEntry[]
}

export interface MallOrder {
  order_no: string
  total_amount: number | null
  status: string
  status_zh?: string
  payment_status: string | null
  created_at: string
  product_summary: string
}

export const ORDER_STATUS_ZH: Record<string, string> = {
  pending: '待付款',
  paid: '待发货',
  shipped: '已发货',
  completed: '已完成',
  cancelled: '已取消',
  refunded: '已退款',
}

export function money(value: string | number | null | undefined): string {
  const n = typeof value === 'string' ? Number.parseFloat(value) : (value ?? 0)
  return `¥${(Number.isFinite(n) ? n : 0).toFixed(2)}`
}

export async function listProducts(params: {
  category?: string
  q?: string
  limit?: number
}): Promise<MallProduct[]> {
  const search = new URLSearchParams()
  if (params.category) search.set('category', params.category)
  if (params.q) search.set('q', params.q)
  search.set('limit', String(params.limit ?? 40))
  const data = await request<{ products: MallProduct[] }>(
    `/api/mall/products?${search.toString()}`,
  )
  return data.products ?? []
}

export async function getWallet(): Promise<WalletView> {
  const data = await request<{ wallet: WalletView }>('/api/mall/wallet')
  return data.wallet
}

export async function topUp(amount: number): Promise<WalletView> {
  const data = await request<{ wallet: WalletView }>('/api/mall/wallet/topup', {
    method: 'POST',
    body: JSON.stringify({ amount, operation_key: crypto.randomUUID() }),
  })
  return data.wallet
}

export async function createOrder(
  items: Array<{ product_id: string; quantity: number }>,
): Promise<{ order_no: string; total_amount: number }> {
  const data = await request<{
    order: { order_no: string; total_amount: number }
  }>('/api/mall/orders', {
    method: 'POST',
    body: JSON.stringify({ items, operation_key: crypto.randomUUID() }),
  })
  return data.order
}

export async function payOrder(
  orderNo: string,
  expectedAmount: number,
): Promise<{ order: MallOrder; wallet: WalletView }> {
  return request(`/api/mall/orders/${orderNo}/pay`, {
    method: 'POST',
    body: JSON.stringify({ expected_amount: expectedAmount, operation_key: crypto.randomUUID() }),
  })
}

export async function cancelUnpaidOrder(
  orderNo: string,
): Promise<{ order: MallOrder }> {
  const data = await request<{ order: MallOrder }>(
    `/api/mall/orders/${orderNo}/cancel`,
    { method: 'POST' },
  )
  return data
}

export async function listMyOrders(limit = 20): Promise<MallOrder[]> {
  const data = await request<{ orders: MallOrder[] }>(
    `/api/mall/orders?limit=${limit}`,
  )
  return data.orders ?? []
}

export interface LogisticsView {
  order_no: string
  shipping_status: string
  status_display: string
  carrier: string | null
  tracking_no: string | null
  tracking_info: string
  estimated_delivery: string | null
  events: Array<{ time: string; location: string; description: string }>
}

export async function getOrderLogistics(orderNo: string): Promise<LogisticsView> {
  const data = await request<{ logistics: LogisticsView }>(
    `/api/mall/orders/${orderNo}/logistics`,
  )
  return data.logistics
}
