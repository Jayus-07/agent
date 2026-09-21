import { redirect } from 'next/navigation'

/**
 * 客服端根路由 → 坐席工作台首页 /cs
 *
 * 2026-09-21 三端拆分：客服端 frontend-cs（:3300）只承载智能客服域，
 * 根路径直达工作台，不再有运营总览之类平台治理入口。
 */
export default function CSRootPage() {
  redirect('/cs')
}
