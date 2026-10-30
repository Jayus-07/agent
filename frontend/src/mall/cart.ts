'use client'

// cs-workstation-v2 P2：购物车状态（zustand + localStorage 持久）。
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export interface CartItem {
  productId: string
  name: string
  price: number
  quantity: number
}

interface CartState {
  items: CartItem[]
  add: (item: Omit<CartItem, 'quantity'>, quantity?: number) => void
  remove: (productId: string) => void
  setQuantity: (productId: string, quantity: number) => void
  clear: () => void
}

export const useCart = create<CartState>()(
  persist(
    (set) => ({
      items: [],
      add: (item, quantity = 1) =>
        set((state) => {
          const existing = state.items.find((i) => i.productId === item.productId)
          if (existing) {
            return {
              items: state.items.map((i) =>
                i.productId === item.productId
                  ? { ...i, quantity: Math.min(99, i.quantity + quantity) }
                  : i,
              ),
            }
          }
          return { items: [...state.items, { ...item, quantity }] }
        }),
      remove: (productId) =>
        set((state) => ({ items: state.items.filter((i) => i.productId !== productId) })),
      setQuantity: (productId, quantity) =>
        set((state) => ({
          items: state.items.map((i) =>
            i.productId === productId
              ? { ...i, quantity: Math.max(1, Math.min(99, quantity)) }
              : i,
          ),
        })),
      clear: () => set({ items: [] }),
    }),
    { name: 'cs2-mall-cart' },
  ),
)

export function cartTotal(items: CartItem[]): number {
  // 金额展示统一两位小数；合计在服务端结算时重新计价（不信任前端）
  return Math.round(items.reduce((sum, i) => sum + i.price * i.quantity, 0) * 100) / 100
}
