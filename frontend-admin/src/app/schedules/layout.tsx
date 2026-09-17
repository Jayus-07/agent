import RoleGate from '@/components/auth/RoleGate'

/**
 * schedules 路由段守卫（UX 设计 P0-①，对应 v3 计划 P2-1 敏感页清单：
 * 定时任务可触发全库重建等高危动作）。
 * 仅 UI 层体验闸；接口层权限由后端 S0-5 收口兜底。
 */
export default function SchedulesLayout({ children }: { children: React.ReactNode }) {
  return (
    <RoleGate minRole="admin" pageName="定时任务">
      {children}
    </RoleGate>
  )
}
