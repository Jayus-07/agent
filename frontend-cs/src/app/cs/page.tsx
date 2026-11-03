import { redirect } from 'next/navigation'

/**
 * /cs 坐席端入口索引页 → 工作台首页。
 *
 * 2026-11-03 修复：三端拆分后根路由 / redirect('/cs')，但 /cs 下只有
 * 子页面目录、没有索引 page——直接访问 :3300/cs（含登录成功后 goNext
 * 的默认跳转）落地 404。补一个索引页把 /cs 归一到工作台。
 */
export default function CsIndexPage() {
  redirect('/cs/workspace')
}
