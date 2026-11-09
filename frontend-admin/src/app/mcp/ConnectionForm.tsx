/**
 * /mcp 连接编辑表单（自 page.tsx 拆出，纯移动）。
 * 抽屉配置 Tab 与顶部「新建连接」复用；secret 留空 = 保持现有凭据。
 * transport 仅创建时可选择，编辑时保持原类型（改类型等同重建连接）。
 */
import { useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import type { ConnectionInput, McpConnection } from '@/api/mcp'

export function ConnectionForm({
  initial, onSubmit, onCancel, submitting,
}: {
  initial?: McpConnection | null
  onSubmit: (input: ConnectionInput & { keepSecret?: boolean }) => void
  onCancel: () => void
  submitting: boolean
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [transport, setTransport] = useState<'streamable_http' | 'http_api'>(
    initial?.transport === 'http_api' ? 'http_api' : 'streamable_http')
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? '')
  const [authType, setAuthType] = useState<'none' | 'static_bearer'>(
    initial?.auth_type ?? 'none')
  const [secret, setSecret] = useState('')
  const [networkScope, setNetworkScope] = useState<'public' | 'private'>(
    initial?.network_scope ?? 'public')
  const placeholder = initial?.credential.configured
    ? `已配置 ****${initial.credential.last4}（指纹 ${initial.credential.fingerprint}），留空保持不变`
    : 'Bearer Token（静态认证）'

  return (
    <div className="grid gap-3 p-4 text-[13px] md:grid-cols-2">
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">连接名称（唯一）</span>
        <input value={name} onChange={(e) => setName(e.target.value)}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5"
          placeholder="如 zhihu-open-api" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">连接类型</span>
        <select value={transport} disabled={!!initial}
          onChange={(e) => setTransport(e.target.value as 'streamable_http' | 'http_api')}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 disabled:opacity-60">
          <option value="streamable_http">MCP（Streamable HTTP；自动发现工具）</option>
          <option value="http_api">HTTP API（REST；手工登记工具）</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">
          {transport === 'http_api' ? 'API Base 地址' : 'Streamable HTTP 地址'}
        </span>
        <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
          placeholder={transport === 'http_api'
            ? 'https://api.example.com（不含路径）'
            : 'https://example.com/mcp'} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">认证方式</span>
        <select value={authType} onChange={(e) => setAuthType(e.target.value as 'none' | 'static_bearer')}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
          <option value="none">无认证</option>
          <option value="static_bearer">静态 Bearer Token</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">网络范围（SSRF）</span>
        <select value={networkScope} onChange={(e) => setNetworkScope(e.target.value as 'public' | 'private')}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
          <option value="public">公网（默认；拒绝内网/环回/云元数据）</option>
          <option value="private">内网（放行私网地址，需明确授权）</option>
        </select>
      </label>
      {authType === 'static_bearer' && (
        <label className="flex flex-col gap-1 md:col-span-2">
          <span className="text-text-muted">凭据（密文存储，服务端不回显）</span>
          <input type="password" autoComplete="new-password" value={secret}
            onChange={(e) => setSecret(e.target.value)}
            className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
            placeholder={placeholder} />
        </label>
      )}
      <div className="flex items-center gap-2 md:col-span-2">
        <AssetActionButton tone="primary" disabled={submitting || !name.trim() || !baseUrl.trim()}
          onClick={() => onSubmit({
            name: name.trim(), base_url: baseUrl.trim(),
            transport: initial ? undefined : transport,
            auth_type: authType,
            secret: secret || undefined, network_scope: networkScope,
            keepSecret: !secret,
          })}>
          {submitting ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
          {initial ? '保存修改' : '创建连接'}
        </AssetActionButton>
        <AssetActionButton onClick={onCancel}>取消</AssetActionButton>
      </div>
    </div>
  )
}
