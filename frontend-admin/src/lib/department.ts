/**
 * 部门身份（只读） — 授权收口（2026-09-23）后的唯一口径。
 *
 * 部门是「账号的组织属性」，由管理员维护、登录/刷新时随 JWT 下发；
 * 管理端聊天框同样不可自选部门（旧选择器对授权零作用，已移除）。
 * DEPARTMENTS 仅作 code → 中文名映射保留。
 */
import { getCachedUser } from '@/lib/auth'

export interface DepartmentOption {
  id: string
  label: string
}

/** 与 backend/config/knowledge_base.py 的 DEPARTMENTS + DEPT_LABELS 对齐 */
export const DEPARTMENTS: DepartmentOption[] = [
  { id: 'general', label: '通用' },
  { id: 'customer', label: '客服部' },
  { id: 'order_dept', label: '订单部' },
  { id: 'product_dept', label: '商品部' },
  { id: 'warehouse', label: '仓储部' },
  { id: 'supply_chain', label: '供应链部' },
  { id: 'finance', label: '财务部' },
  { id: 'hr', label: '人事部' },
  { id: 'admin', label: '行政部' },
]

const LABELS = new Map(DEPARTMENTS.map((d) => [d.id, d.label]))

export function departmentLabel(code: string): string {
  return LABELS.get(code) || code
}

/** 当前账号部门（登录响应 userInfo.dept）；未分配返回 null */
export function getCurrentDepartment(): DepartmentOption | null {
  if (typeof window === 'undefined') return null
  const raw = (getCachedUser() as { dept?: unknown } | null)?.dept
  const code = typeof raw === 'string' ? raw.trim() : ''
  if (!code) return null
  return { id: code, label: departmentLabel(code) }
}
