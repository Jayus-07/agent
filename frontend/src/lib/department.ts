/**
 * 部门选择偏好 — 聊天检索授权用（与后端 KNOWLEDGE_BASES/DEPARTMENTS 对齐）。
 *
 * 部门决定检索授权范围：带部门的请求按 employee 主体经 owner_depts 矩阵
 * 授权知识库；不带则后端按对客最严格集合处理（仅 cs_* 库）。
 * 持久化到 localStorage（键 chat_department），跨会话保留。
 */

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

const STORAGE_KEY = 'chat_department'

export function getSelectedDepartment(): string {
  if (typeof window === 'undefined') return ''
  try {
    return window.localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function setSelectedDepartment(department: string): void {
  try {
    if (department) window.localStorage.setItem(STORAGE_KEY, department)
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // localStorage 不可用（隐私模式等）：静默降级为会话内不生效
  }
}
