'use client'

/**
 * /customer-service — 独立用户智能客服整页（P8 切换）。
 * cs-workstation-v2 重建版：会话流 + 右栏客户 360/最近订单，替代旧
 * CSDrawer 抽屉页（components/cs/* 已删除，禁止再引用）。
 */
import CustomerServicePage from '@/cs2/CustomerServicePage'

export default function CustomerServiceRoute() {
  return <CustomerServicePage />
}
