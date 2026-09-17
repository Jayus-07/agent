import RoleGate from '@/components/auth/RoleGate'

/**
 * knowledge/pending 路由段守卫（UX 设计 P0-①，对应 v3 计划 P2-1 敏感页清单：
 * 知识入库待审页涉敏感操作，与 knowledge 其他子路由区分开，单独挂 layout）。
 * 仅 UI 层体验闸；接口层权限由后端 S0-5 收口兜底。
 */
export default function KnowledgePendingLayout({ children }: { children: React.ReactNode }) {
  return (
    <RoleGate minRole="admin" pageName="知识入库审核">
      {children}
    </RoleGate>
  )
}
