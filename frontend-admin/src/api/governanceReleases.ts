/**
 * 治理配置发布/回滚/导入 API（Batch 5-2 前端）
 *
 * 后端：backend/app/api/routes/admin_governance_releases.py（/admin/mcp 前缀）。
 * 与系统发布 Gate（api/governance.ts 的 getReleaseLatest/getReleases，
 * ai.release_records）严格区分——本模块治理的是连接声明 + 能力包 + 授权
 * 关系的版本化（V2 发布记录页第一档）。
 */
import { mutationRequest, request } from '@/api/client'

export type GovernanceReleaseStatus = 'draft' | 'published' | 'rolled_back'

export interface GovernanceRelease {
  id: string
  version: number
  status: GovernanceReleaseStatus
  description: string
  connection_count: number
  bundle_count: number
  created_by: string
  published_by: string
  published_at: string | null
  created_at: string
}

export interface GovernanceReleaseDiffField {
  field: string
  release: unknown
  current: unknown
}

export interface GovernanceReleaseDiffChange {
  kind: 'connection' | 'bundle'
  name: string
  change: 'modified' | 'added' | 'removed'
  fields: GovernanceReleaseDiffField[]
}

export interface GovernanceReleaseDiff {
  changes: GovernanceReleaseDiffChange[]
  counts: { modified: number; added: number; removed: number }
}

export interface GovernanceImportResult {
  created_connections: string[]
  created_bundles: string[]
  skipped: { name: string; reason: string }[]
  rejected: { name: string; reason: string }[]
  redacted: { name: string; removed: string[] }[]
}

export async function listGovernanceReleases(): Promise<{ releases: GovernanceRelease[] }> {
  return request<{ releases: GovernanceRelease[] }>('/api/admin/mcp/releases')
}

export async function createGovernanceRelease(description: string): Promise<GovernanceRelease> {
  return mutationRequest<GovernanceRelease>('/api/admin/mcp/releases', {
    operation: 'governance_release_create',
    body: { description },
  })
}

export async function getGovernanceReleaseDiff(releaseId: string): Promise<GovernanceReleaseDiff> {
  return request<GovernanceReleaseDiff>(`/api/admin/mcp/releases/${releaseId}/diff`)
}

export async function publishGovernanceRelease(releaseId: string): Promise<GovernanceRelease> {
  return mutationRequest<GovernanceRelease>(
    `/api/admin/mcp/releases/${releaseId}/publish`,
    { operation: 'governance_release_publish' },
  )
}

export interface GovernanceRollbackResult {
  rolled_back_to: number
  new_release_id: string
  new_version: number
  restored_connections: number
  restored_bundles: number
}

export async function rollbackGovernanceRelease(
  releaseId: string,
): Promise<GovernanceRollbackResult> {
  return mutationRequest<GovernanceRollbackResult>(
    `/api/admin/mcp/releases/${releaseId}/rollback`,
    { operation: 'governance_release_rollback' },
  )
}

export async function importGovernanceConfiguration(
  payload: unknown,
): Promise<GovernanceImportResult> {
  return mutationRequest<GovernanceImportResult>('/api/admin/mcp/import', {
    operation: 'mcp_configuration_import',
    body: { payload },
  })
}
