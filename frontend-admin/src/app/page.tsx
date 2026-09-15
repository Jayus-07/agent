import { redirect } from 'next/navigation'

/**
 * 管理端根页面：直接跳转链路追踪首页（管理端无业务驾驶舱，那属于用户端）
 */
export default function AdminHome() {
  redirect('/observability')
}
