/**
 * /mcp 凭据管理表（只读，真实数据派生，自 page.tsx 拆出，纯移动）：
 * 连接名 / 认证方式 / 凭据 / 连接状态。
 */
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import EmptyState from '@/components/shared/EmptyState'
import type { McpConnection } from '@/api/mcp'
import { CONN_STATUS_META } from './badges'

export function CredentialTable({ connections, onGoBind }: {
  connections: McpConnection[]
  onGoBind: (conn: McpConnection) => void
}) {
  if (connections.length === 0) {
    return <EmptyState title="凭据" description="尚未接入任何连接；先在「全部连接」新建" />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
            <th className="px-4 py-2 font-normal">连接名</th>
            <th className="px-2 py-2 font-normal">认证方式</th>
            <th className="px-2 py-2 font-normal">凭据</th>
            <th className="px-2 py-2 font-normal">连接状态</th>
            <th className="px-4 py-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {connections.map((conn) => {
            const status = CONN_STATUS_META[conn.status] ?? { label: conn.status, tone: 'neutral' as StatusTone }
            return (
              <tr key={conn.id} className="border-b border-border-subtle">
                <td className="px-4 py-2 font-medium text-text-primary">{conn.name}</td>
                <td className="px-2 py-2">
                  {conn.auth_type === 'none' ? '无认证' : '静态 Bearer Token'}
                </td>
                <td className="px-2 py-2">
                  {conn.credential.configured
                    ? <span title={`指纹 ${conn.credential.fingerprint}`}>已配置 ****{conn.credential.last4 || '????'}</span>
                    : <span className="text-text-muted">未配置</span>}
                </td>
                <td className="px-2 py-2"><StatusChip tone={status.tone}>{status.label}</StatusChip></td>
                <td className="px-4 py-2 text-right">
                  <AssetActionButton onClick={() => onGoBind(conn)}>前往绑定</AssetActionButton>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
