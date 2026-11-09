/**
 * /tools 页共享常量与小工具（自 page.tsx 拆出，纯移动）。
 *
 * 供页面主组件与各 section/抽屉组件共用；只放「多消费方」的条目，
 * 单一消费方的常量留在各自组件文件内。
 */
import type { StatusTone } from '@/components/layout/StatusChip'

export const ERROR_CLASS_LABEL: Record<string, string> = {
  timeout: '超时',
  network_error: '网络',
  permission_denied: '权限',
  validation_error: '校验',
  business_error: '业务',
  contract_error: '契约',
  provider_error: '供应商',
  unknown: '未知',
}

/** 动态工具审核/状态徽章（StatusChip 语义映射，与连接中心同口径） */
export const MCP_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: '待审核', tone: 'warn' },
  approved: { label: '已生效', tone: 'success' },
  rejected: { label: '已拒绝', tone: 'danger' },
  disabled: { label: '已停用', tone: 'neutral' },
  retired: { label: '上游已消失', tone: 'neutral' },
}

export function domainLabel(domain?: string): string {
  const value = (domain ?? '').trim()
  return value || '未分类'
}

export const fmtCount = (n?: number) => (n ?? 0).toLocaleString('zh-CN')
