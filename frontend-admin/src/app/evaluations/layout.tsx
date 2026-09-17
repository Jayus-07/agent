import RoleGate from '@/components/auth/RoleGate'

/**
 * evaluations 路由段守卫（UX 设计 P0-①，对应 v3 计划 P2-1 敏感页清单）。
 * 仅 UI 层体验闸；接口层权限由后端 S0-5 敏感接口收口兜底。
 */
export default function EvaluationsLayout({ children }: { children: React.ReactNode }) {
  return (
    <RoleGate minRole="admin" pageName="评估中心">
      {children}
    </RoleGate>
  )
}
