import RoleGate from '@/components/auth/RoleGate'

/**
 * observability 路由段守卫（UX 设计 P0-①，对应 v3 计划 P2-1 敏感页清单）
 * 覆盖本段全部子路由（/observability、traces、tokens、alerts、gateway）。
 * 仅 UI 层体验闸；接口层权限由后端 require_admin_user / S0-5 收口兜底。
 */
export default function ObservabilityLayout({ children }: { children: React.ReactNode }) {
  return (
    <RoleGate minRole="admin" pageName="可观测性">
      {children}
    </RoleGate>
  )
}
