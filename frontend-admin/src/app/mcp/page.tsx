'use client'

/**
 * /mcp — 连接中心（V2 治理工作台：连接为一级资产，ADR-0011）
 *
 * 工作流：新建连接（SSRF 校验在服务端）→ 测试健康 → 工具发现/登记。
 * 动态工具的审核、治理配置、授权与测试调用已迁往 /tools（Tool 治理），
 * 本页只管「连接」资产本身：卡片目录 + 详情抽屉（概览/已发现工具/配置/
 * 运行健康/版本历史）+ 凭据管理。凭据只上行一次，服务端仅回 fingerprint/last4。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle, CheckCircle2, CircleSlash, Download, KeyRound, Loader2,
  Plug, Plus, RefreshCw, Search, Trash2,
} from 'lucide-react'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DetailDrawer from '@/components/layout/DetailDrawer'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import StatMetricBar from '@/components/layout/StatMetricBar'
import { ASSET_SURFACE, AssetActionButton, AssetSection } from '@/components/layout/AssetPageShell'
import EmptyState from '@/components/shared/EmptyState'
import ErrorState from '@/components/shared/ErrorState'
import Skeleton from '@/components/shared/Skeleton'
import { useToast } from '@/components/shared/Toast'
import {
  createMcpConnection, deleteMcpConnection, discoverMcpTools, exportMcpConfiguration,
  getMcpConnections, getMcpHealthOverview, getMcpOverview, getMcpTools, registerHttpTool,
  setMcpConnectionEnabled, testMcpConnection, updateHttpToolInvocation,
  updateMcpConnection,
  type ConnectionInput, type ConnectionTestResult, type DiscoverResult,
  type HttpToolInvocation, type McpConnection, type McpConnectionHealth,
  type McpOverview,
  type McpTool,
} from '@/api/mcp'
import { ApiError } from '@/api/client'

const MCP_PAGE_TABS = [
  { id: 'all', label: '全部连接' },
  { id: 'mcp', label: 'MCP 服务' },
  { id: 'http', label: 'HTTP API' },
  { id: 'credentials', label: '凭据管理' },
] as const satisfies readonly WorkbenchTab<'all' | 'mcp' | 'http' | 'credentials'>[]

const MCP_PAGE_TAB_IDS = MCP_PAGE_TABS.map((tab) => tab.id) as readonly (
  | 'all' | 'mcp' | 'http' | 'credentials')[]

/** 连接抽屉：概览 / 已发现工具 / 配置 / 运行健康（壳位）/ 版本历史（壳位） */
const CONN_DRAWER_TABS = [
  { id: 'overview', label: '概览' },
  { id: 'tools', label: '已发现工具' },
  { id: 'config', label: '配置' },
  { id: 'health', label: '运行健康' },
  { id: 'versions', label: '版本历史' },
]

const CONN_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  never_connected: { label: '未连接', tone: 'neutral' },
  connected: { label: '已连接', tone: 'success' },
  error: { label: '连接异常', tone: 'danger' },
  disabled: { label: '已停用', tone: 'warn' },
}

const TOOL_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: '待审核', tone: 'warn' },
  approved: { label: '已生效', tone: 'success' },
  rejected: { label: '已拒绝', tone: 'danger' },
  disabled: { label: '已停用', tone: 'neutral' },
  retired: { label: '上游已消失', tone: 'neutral' },
}

const TRANSPORT_META: Record<string, { label: string; badgeCls: string }> = {
  streamable_http: { label: 'MCP · Streamable HTTP', badgeCls: 'bg-violet-100 text-violet-700' },
  http_api: { label: 'HTTP API', badgeCls: 'bg-sky-100 text-sky-700' },
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return e instanceof Error ? e.message : '请求失败'
}

function TransportBadge({ transport }: { transport: string }) {
  const meta = TRANSPORT_META[transport]
  if (!meta) return <span className="rounded bg-slate-100 px-1 py-0.5 text-[10px] font-medium text-slate-600">{transport}</span>
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap ${meta.badgeCls}`}>
      {meta.label}
    </span>
  )
}

/** 连接编辑表单（抽屉配置 Tab 与顶部「新建连接」复用；secret 留空 = 保持现有凭据）。
 *  transport 仅创建时可选择，编辑时保持原类型（改类型等同重建连接）。 */
function ConnectionForm({
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

/** 轻量 OpenAPI 3.0 导入：取第一个 path+method 预填表单（仅前端解析，
 *  服务端 validate_invocation 仍是唯一校验事实源）。
 *  仅支持 2xx/默认响应中声明的 JSON Schema；参数含 query/path 两来源。 */
function parseOpenApiOperation(specText: string): {
  toolName: string; description: string;
  method: 'GET' | 'POST' | 'PUT'; path: string;
  query: Record<string, string>; inputSchema: Record<string, unknown>;
} | null {
  let spec: {
    paths?: Record<string, Record<string, {
      operationId?: string; summary?: string; description?: string;
      parameters?: { name: string; in: string; required?: boolean;
        schema?: Record<string, unknown> }[]
    }>>
  }
  try {
    spec = JSON.parse(specText)
  } catch {
    return null
  }
  const httpMethods = new Set(['get', 'post', 'put'])
  for (const [rawPath, ops] of Object.entries(spec.paths ?? {})) {
    for (const [m, op] of Object.entries(ops)) {
      if (!httpMethods.has(m) || !op) continue
      const toolName = op.operationId
        ?? `${m}_${rawPath.replaceAll(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '')}`
      const inputSchema: Record<string, unknown> = {
        type: 'object', properties: {}, additionalProperties: false,
      }
      const properties: Record<string, unknown> = {}
      const required: string[] = []
      const query: Record<string, string> = {}
      for (const p of op.parameters ?? []) {
        if (p.in === 'path' || p.in === 'query') {
          properties[p.name] = p.schema
            ? { ...p.schema, description: p.schema.description ?? '' }
            : { type: 'string' }
          if (p.in === 'path' || p.required) required.push(p.name)
          if (p.in === 'query') query[p.name] = `{${p.name}}`
        }
      }
      inputSchema.properties = properties
      if (required.length) inputSchema.required = required
      return {
        toolName: toolName.slice(0, 64),
        description: (op.summary ?? op.description ?? '').slice(0, 500),
        method: m.toUpperCase() as 'GET' | 'POST' | 'PUT',
        path: rawPath,
        query,
        inputSchema,
      }
    }
  }
  return null
}

/** HTTP API 工具登记/编辑表单（http_api 连接专用，入口在连接抽屉）。
 *  method/path/query/headers/body_from 服务端白名单校验；
 *  headers 禁 Authorization/Cookie（凭据统一注入）。 */
function HttpToolForm({
  initial, onRegister, onUpdate, onCancel, submitting, mode,
}: {
  initial?: McpTool | null
  onRegister: (input: { tool_name: string; description: string;
    invocation: HttpToolInvocation;
    input_schema: Record<string, unknown> }) => void
  onUpdate: (input: { description: string;
    invocation: HttpToolInvocation;
    input_schema: Record<string, unknown> }) => void
  onCancel: () => void
  submitting: boolean
  mode: 'register' | 'edit'
}) {
  const toast = useToast()
  const initialInv = initial?.invocation ?? undefined
  const [toolName, setToolName] = useState(initial?.tool_name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [method, setMethod] = useState<'GET' | 'POST' | 'PUT'>(
    initialInv?.method ?? 'GET')
  const [path, setPath] = useState(initialInv?.path ?? '/')
  const [queryText, setQueryText] = useState(
    Object.entries(initialInv?.query ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'))
  const [headersText, setHeadersText] = useState(
    Object.entries(initialInv?.headers ?? {}).map(([k, v]) => `${k}=${v}`).join('\n'))
  const [bodyFrom, setBodyFrom] = useState(initialInv?.body_from ?? '')
  const [schemaText, setSchemaText] = useState(
    JSON.stringify(
      initial?.snapshots?.[0]?.input_schema
      ?? { type: 'object', properties: {}, additionalProperties: false },
      null, 0))
  const [openApiText, setOpenApiText] = useState('')
  const [showOpenApi, setShowOpenApi] = useState(false)

  const importOpenApi = () => {
    const parsed = parseOpenApiOperation(openApiText)
    if (!parsed) {
      toast.error('OpenAPI 解析失败：需合法 JSON 且 paths 下至少一个 GET/POST 操作')
      return
    }
    setToolName(parsed.toolName)
    setDescription(parsed.description)
    setMethod(parsed.method)
    setPath(parsed.path)
    setQueryText(Object.entries(parsed.query)
      .map(([k, v]) => `${k}=${v}`).join('\n'))
    setSchemaText(JSON.stringify(parsed.inputSchema, null, 0))
    setShowOpenApi(false)
    setOpenApiText('')
    toast.success(`已导入 ${parsed.method} ${parsed.path}（服务端仍会完整校验）`)
  }

  const parseKv = (text: string): Record<string, string> | null => {
    const out: Record<string, string> = {}
    for (const line of text.split('\n').map((s) => s.trim()).filter(Boolean)) {
      const idx = line.indexOf('=')
      if (idx <= 0) return null
      out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
    }
    return out
  }

  const submit = () => {
    const query = parseKv(queryText)
    const headers = parseKv(headersText)
    if (query === null || headers === null) {
      toast.error('Query/Headers 每行必须是 key=value 形式')
      return
    }
    let inputSchema: Record<string, unknown>
    try {
      inputSchema = JSON.parse(schemaText || '{}')
    } catch {
      toast.error('参数 Schema 必须是合法 JSON')
      return
    }
    const invocation: HttpToolInvocation = {
      method, path: path.trim(),
      ...(Object.keys(query).length ? { query } : {}),
      ...(Object.keys(headers).length ? { headers } : {}),
      ...(bodyFrom.trim() ? { body_from: bodyFrom.trim() } : {}),
    }
    if (mode === 'register') {
      if (!toolName.trim()) { toast.error('工具名不能为空'); return }
      onRegister({
        tool_name: toolName.trim(), description: description.trim(),
        invocation, input_schema: inputSchema,
      })
    } else {
      onUpdate({ description: description.trim(), invocation, input_schema: inputSchema })
    }
  }

  return (
    <div className="grid gap-3 p-4 text-[13px] md:grid-cols-2">
      {mode === 'register' && (
        <div className="md:col-span-2">
          <AssetActionButton onClick={() => setShowOpenApi((v) => !v)}>
            {showOpenApi ? '收起 OpenAPI 导入' : '从 OpenAPI 导入（可选）'}
          </AssetActionButton>
          {showOpenApi && (
            <div className="mt-2 flex flex-col gap-2">
              <textarea value={openApiText}
                onChange={(e) => setOpenApiText(e.target.value)} rows={5}
                className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono text-[11px]"
                placeholder='粘贴 OpenAPI 3.0 JSON（取第一个 GET/POST 操作预填；仅前端预填，服务端仍是唯一校验事实源）' />
              <AssetActionButton tone="primary" disabled={!openApiText.trim()}
                onClick={importOpenApi}>
                解析并预填表单
              </AssetActionButton>
            </div>
          )}
        </div>
      )}
      {mode === 'register' && (
        <label className="flex flex-col gap-1">
          <span className="text-text-muted">上游工具名（全局唯一）</span>
          <input value={toolName} onChange={(e) => setToolName(e.target.value)}
            className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
            placeholder="如 get_order" />
        </label>
      )}
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">平台描述（审核时可改写）</span>
        <input value={description} onChange={(e) => setDescription(e.target.value)}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5"
          placeholder="这个工具做什么" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">HTTP 方法</span>
        <select value={method} onChange={(e) => setMethod(e.target.value as 'GET' | 'POST' | 'PUT')}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">路径模板（相对 Base，{'{参数}'} 引用 Schema 属性）</span>
        <input value={path} onChange={(e) => setPath(e.target.value)}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
          placeholder="/orders/{order_id}" />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">Query 模板（每行 key={'{param}'}；空串=不发送）</span>
        <textarea value={queryText} onChange={(e) => setQueryText(e.target.value)} rows={2}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
          placeholder={'verbose={verbose}'} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-text-muted">Headers 模板（每行 key=value；禁 Authorization/Cookie）</span>
        <textarea value={headersText} onChange={(e) => setHeadersText(e.target.value)} rows={2}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
          placeholder={'X-Channel={channel}'} />
      </label>
      {method === 'POST' && (
        <label className="flex flex-col gap-1">
          <span className="text-text-muted">JSON Body 参数名（留空 = 未引用参数整体作为 body）</span>
          <input value={bodyFrom} onChange={(e) => setBodyFrom(e.target.value)}
            className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
            placeholder="text" />
        </label>
      )}
      <label className="flex flex-col gap-1 md:col-span-2">
        <span className="text-text-muted">参数 JSON Schema（additionalProperties 固定 false）</span>
        <textarea value={schemaText} onChange={(e) => setSchemaText(e.target.value)} rows={4}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono" />
      </label>
      <div className="flex items-center gap-2 md:col-span-2">
        <AssetActionButton tone="primary" disabled={submitting} onClick={submit}>
          {submitting ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
          {mode === 'register' ? '登记为待审核工具' : '保存为新版本（approved 将漂移）'}
        </AssetActionButton>
        <AssetActionButton onClick={onCancel}>取消</AssetActionButton>
      </div>
    </div>
  )
}

/** 连接卡片：状态 / 类型 / 工具数 / 最近检查 + 查看详情。 */
function ConnectionCard({ conn, toolCount, onOpen }: {
  conn: McpConnection
  toolCount: number
  onOpen: (conn: McpConnection) => void
}) {
  const meta = CONN_STATUS_META[conn.status] ?? { label: conn.status, tone: 'neutral' as StatusTone }
  return (
    <div className={ASSET_SURFACE + ' flex flex-col gap-2 p-4'}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold text-text-primary">{conn.name}</span>
            <TransportBadge transport={conn.transport} />
          </div>
          <div className="mt-0.5 truncate font-mono text-[11px] text-text-muted" title={conn.base_url}>
            {conn.base_url}
          </div>
        </div>
        <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
      </div>
      {conn.last_error && (
        <div className="truncate text-[11px] text-red-600" title={conn.last_error}>{conn.last_error}</div>
      )}
      <div className="mt-auto flex items-end justify-between gap-2 pt-1">
        <div className="text-[11px] leading-relaxed text-text-muted">
          <div>发现工具 <span className="font-mono text-text-secondary">{toolCount}</span> 个 · v{conn.discovery_version}</div>
          <div>最近检查 {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : '—'}</div>
          <div>
            最近探活 {conn.last_checked_at ? new Date(conn.last_checked_at).toLocaleString() : '—'}
            {(conn.probe_fail_count ?? 0) > 0 && (
              <span className="ml-1 text-amber-600">连续失败 {conn.probe_fail_count}</span>
            )}
          </div>
        </div>
        <AssetActionButton onClick={() => onOpen(conn)}>查看详情</AssetActionButton>
      </div>
    </div>
  )
}

/**
 * 连接抽屉「运行健康」真实数据区（Batch 5-3，决策 #16）：
 * 自动巡检状态/退避节奏 + 健康事件时间线 + 立即探活（与管理端 /test 同口径）。
 * 故障模拟四按钮保持置灰壳位——那是演练/评测能力，不属健康监测本体。
 */
function ConnectionHealthSection({ conn }: { conn: McpConnection }) {
  const toast = useToast()
  const [health, setHealth] = useState<McpConnectionHealth | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [probing, setProbing] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    getMcpHealthOverview()
      .then((r) => {
        setHealth(r.connections.find((c) => c.id === conn.id) ?? null)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [conn.id])

  useEffect(() => { load() }, [load])

  const onProbe = async () => {
    setProbing(true)
    try {
      const result = await testMcpConnection(conn.id)
      if (result.ok) {
        toast.success(`探活成功（${result.elapsed_ms}ms）`)
      } else {
        toast.error(`探活失败：${result.error ?? '连接不可达'}`)
      }
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '探活请求失败')
    } finally {
      setProbing(false)
    }
  }

  if (loading) {
    return <div className="rounded-lg border border-border-subtle bg-white p-6 text-center text-[12px] text-text-muted">加载健康数据中…</div>
  }
  if (error) {
    return <ErrorState message={error} onRetry={load} />
  }
  if (!health) {
    return (
      <div className="rounded-lg border border-border-subtle bg-white p-5 text-[12px] text-text-secondary">
        该连接当前未启用——启用后自动纳入健康巡检（非副作用探活 + 断线退避重连）。
      </div>
    )
  }

  const statusMeta: { label: string; tone: StatusTone } = health.status === 'connected'
    ? { label: '健康', tone: 'success' }
    : health.status === 'error' ? { label: '异常', tone: 'danger' }
      : { label: health.status, tone: 'neutral' }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-surface-base px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[12px] font-medium text-text-primary">自动巡检</span>
            <StatusChip tone={statusMeta.tone}>{statusMeta.label}</StatusChip>
            {health.probe_fail_count > 0 && (
              <StatusChip tone="warn">连续失败 {health.probe_fail_count} 次</StatusChip>
            )}
          </div>
          <div className="mt-0.5 text-[11px] leading-relaxed text-text-muted">
            非副作用探活（{health.transport === 'http_api' ? 'HEAD 可达性' : 'MCP 握手 + tools/list'}）
            · 基础周期 {health.probe_interval_seconds}s，失败按指数退避（上限 ×16），探活成功自动恢复
            {health.next_probe_in_seconds > 0 && ` · 下次探活约 ${Math.ceil(health.next_probe_in_seconds / 60)} 分钟后`}
          </div>
        </div>
        <AssetActionButton onClick={onProbe} disabled={probing} data-testid="health-probe-btn">
          {probing ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          立即探活
        </AssetActionButton>
      </div>

      {health.last_error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50/60 px-3 py-2 text-[12px] text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span className="break-all" title={health.last_error}>{health.last_error}</span>
        </div>
      )}

      <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2.5 text-[11px] text-text-muted">
        影响工具：发现 {health.tool_count} 个，其中审核生效 {health.approved_count} 个——
        连接异常时动态工具调用会以明确错误失败，恢复后无需重新审核。
        <Link href="/tools" className="ml-1 text-accent hover:underline">前往 Tool 治理</Link>
      </div>

      <div className="rounded-lg border border-border-subtle">
        <div className="border-b border-border-subtle bg-bg-elevated/60 px-3 py-2 text-[12px] font-medium text-text-primary">
          健康事件（最近 {health.recent_events.length} 条）
        </div>
        {health.recent_events.length === 0 ? (
          <div className="px-3 py-4 text-center text-[12px] text-text-muted">
            暂无事件——后台巡检周期内将产生第一条记录
          </div>
        ) : (
          <ul className="divide-y divide-black/[0.04]">
            {health.recent_events.map((event, index) => (
              <li key={`${event.created_at}-${index}`} className="flex items-start gap-2 px-3 py-2 text-[12px]">
                {event.ok
                  ? <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-600" />
                  : <AlertTriangle size={14} className="mt-0.5 shrink-0 text-red-500" />}
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={event.ok ? 'text-emerald-700' : 'text-red-600'}>
                      {event.ok ? '正常' : '异常'}
                    </span>
                    <span className="text-[11px] text-text-muted">
                      {new Date(event.created_at).toLocaleString()} · {event.elapsed_ms}ms ·{' '}
                      {event.source === 'monitor' ? '自动巡检' : '手动探活'}
                    </span>
                  </div>
                  {!event.ok && event.error && (
                    <div className="mt-0.5 break-all text-[11px] text-text-muted" title={event.error}>
                      {event.error}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* 故障模拟：演练/评测能力，保持置灰壳位（非健康监测本体） */}
      <div className="grid grid-cols-2 gap-2">
        {['模拟断线', '恢复网络', '模拟 Schema 漂移', '检查并重连'].map((label) => (
          <button key={label} type="button" disabled data-stub="demo"
            title="故障模拟能力建设中，暂不可操作"
            className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[12px] text-text-muted disabled:cursor-not-allowed disabled:opacity-50">
            {label}
          </button>
        ))}
      </div>
      <p className="text-[11px] text-text-muted">
        巡检为只读探活：不会自动发现工具、不会改动审核状态与启用开关；连接异常期间调用以明确错误失败。
      </p>
    </div>
  )
}

/** 连接抽屉「版本历史」置灰壳位：配置版本时间线属下一批，展示真实当前版本锚点。 */
function VersionHistoryStub({ conn }: { conn: McpConnection }) {
  return (
    <div data-stub="demo" className="space-y-3">
      <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-6 text-center text-[12px] text-text-muted">
        配置版本时间线属下一批：当前仅显示最新发现版本，历史版本追溯建设中。
      </div>
      <div className="rounded-lg border border-border-subtle px-3 py-2.5 text-[12px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-text-primary">当前发现版本 v{conn.discovery_version}</span>
          <span className="text-text-muted">
            最后同步 {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : '—'}
          </span>
        </div>
        {conn.updated_at && (
          <div className="mt-0.5 text-[11px] text-text-muted">
            连接配置更新于 {new Date(conn.updated_at).toLocaleString()}（by {conn.updated_by || '—'}）
          </div>
        )}
      </div>
    </div>
  )
}

/** 凭据管理（只读，真实数据派生）：连接名 / 认证方式 / 凭据 / 连接状态。 */
function CredentialTable({ connections, onGoBind }: {
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

export default function McpPage() {
  const toast = useToast()
  const { tab, selectTab } = useWorkbenchTab(MCP_PAGE_TAB_IDS, 'all')
  const [overview, setOverview] = useState<McpOverview | null>(null)
  const [connections, setConnections] = useState<McpConnection[]>([])
  const [mcpTools, setMcpTools] = useState<McpTool[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [testResult, setTestResult] = useState<Record<string, ConnectionTestResult>>({})
  const [discoverResult, setDiscoverResult] = useState<Record<string, DiscoverResult>>({})
  // 连接详情抽屉：connId 为空 = 关闭；抽屉数据始终取 connections 最新事实源
  const [drawerConnId, setDrawerConnId] = useState<string | null>(null)
  const [drawerTab, setDrawerTab] = useState('overview')
  // HTTP API 工具登记/编辑：{ connId, tool } | null（tool 空 = 新登记）
  const [httpToolForm, setHttpToolForm] = useState<
    { connId: string; tool: McpTool | null } | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [ov, conns, tools] = await Promise.all([
        getMcpOverview(), getMcpConnections(), getMcpTools(),
      ])
      setOverview(ov)
      setConnections(conns)
      setMcpTools(tools)
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  /** 新建连接：表单渲染在连接卡片分支，凭据 Tab 下先切回「全部连接」保证可见。 */
  const handleNewConnection = () => {
    setShowForm(true)
    if (tab === 'credentials') selectTab('all')
  }

  const openDrawer = (conn: McpConnection, tabId = 'overview') => {
    setDrawerConnId(conn.id)
    setDrawerTab(tabId)
  }

  // ── 写操作（沿用原确认与提示文案）────────────────────────

  const submitConnection = async (
    input: ConnectionInput & { keepSecret?: boolean },
    mode: 'create' | 'edit',
  ) => {
    const editing = mode === 'edit'
      ? connections.find((c) => c.id === drawerConnId) ?? null
      : null
    setSubmitting(true)
    try {
      if (editing) {
        await updateMcpConnection(editing.id, {
          name: input.name, base_url: input.base_url, auth_type: input.auth_type,
          network_scope: input.network_scope,
          secret: input.keepSecret ? undefined : input.secret ?? '',
        })
        toast.success('连接已更新')
      } else {
        await createMcpConnection(input)
        toast.success('连接已创建；请先测试连接再发现工具')
        setShowForm(false)
      }
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const runTest = async (conn: McpConnection) => {
    setBusyAction(`test:${conn.id}`)
    try {
      const result = await testMcpConnection(conn.id)
      setTestResult((prev) => ({ ...prev, [conn.id]: result }))
      if (result.ok) toast.success(`连接成功 (${result.elapsed_ms}ms，${result.tool_count} 个工具)`)
      else toast.error(`连接失败 (${result.error_class})`)
      await reload()
    } catch (e) {
      toast.error(`测试失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const runDiscover = async (conn: McpConnection) => {
    setBusyAction(`discover:${conn.id}`)
    try {
      const result = await discoverMcpTools(conn.id)
      setDiscoverResult((prev) => ({ ...prev, [conn.id]: result }))
      if (result.ok) {
        toast.success(
          `发现完成 v${result.discovery_version}: 新增 ${result.new?.length ?? 0} / ` +
          `变化 ${result.changed?.length ?? 0} / 消失 ${result.retired?.length ?? 0}`)
      } else {
        toast.error(`发现失败 (${result.error_class})`)
      }
      await reload()
    } catch (e) {
      toast.error(`发现失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const toggleEnabled = async (conn: McpConnection) => {
    setBusyAction(`toggle:${conn.id}`)
    try {
      await setMcpConnectionEnabled(conn.id, !conn.enabled)
      toast.success(conn.enabled
        ? '连接已停用；写调用立即被拦截，读调用最迟 15 秒退出候选'
        : '连接已启用')
      await reload()
    } catch (e) {
      toast.error(`操作失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const removeConnection = async (conn: McpConnection) => {
    if (!window.confirm(`删除连接「${conn.name}」？仍存在工具记录或有效授权时会被拒绝。`)) return
    setBusyAction(`delete:${conn.id}`)
    try {
      await deleteMcpConnection(conn.id)
      toast.success('连接已删除')
      if (drawerConnId === conn.id) setDrawerConnId(null)
      await reload()
    } catch (e) {
      toast.error(`删除被拒绝: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const submitRegisterHttpTool = async (
    connId: string,
    input: { tool_name: string; description: string;
      invocation: HttpToolInvocation; input_schema: Record<string, unknown> },
  ) => {
    setSubmitting(true)
    try {
      const r = await registerHttpTool(connId, input)
      toast.success(`已登记 ${r.capability}（待审核）`)
      setHttpToolForm(null)
      await reload()
    } catch (e) {
      toast.error(`登记失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const submitUpdateHttpTool = async (
    toolId: number,
    input: { description: string; invocation: HttpToolInvocation;
      input_schema: Record<string, unknown> },
  ) => {
    setSubmitting(true)
    try {
      const r = await updateHttpToolInvocation(toolId, input)
      toast.success(r.schema_changed
        ? `已保存 v${r.version}；approved 工具已漂移，需重新审核`
        : `已保存 v${r.version}（待审核）`)
      setHttpToolForm(null)
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  /** 配置导出：GET /admin/mcp/export → 触发浏览器下载 JSON（凭据不导出）。 */
  const handleExport = async () => {
    setExporting(true)
    try {
      const data = await exportMcpConfiguration()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `mcp-export-${new Date().toISOString().slice(0, 10)}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      toast.success('配置已导出（凭据密文与指纹不包含在导出内容中）')
    } catch (e) {
      toast.error(`导出失败: ${errMessage(e)}`)
    } finally {
      setExporting(false)
    }
  }

  // ── 派生数据 ─────────────────────────────────────────────

  const toolCountByConn = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const t of mcpTools) counts[t.connection_id] = (counts[t.connection_id] ?? 0) + 1
    return counts
  }, [mcpTools])

  const healthyCount = connections.filter((c) => c.status === 'connected').length
  const errorCount = connections.filter((c) => c.status === 'error').length
  const driftCount = mcpTools.filter((t) => t.schema_changed).length

  const scopeFiltered = useMemo(() => {
    if (tab === 'mcp') return connections.filter((c) => c.transport === 'streamable_http')
    if (tab === 'http') return connections.filter((c) => c.transport === 'http_api')
    return connections
  }, [connections, tab])

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase()
    if (!kw) return scopeFiltered
    return scopeFiltered.filter((c) =>
      c.name.toLowerCase().includes(kw) || c.base_url.toLowerCase().includes(kw))
  }, [scopeFiltered, search])

  const drawerConn = drawerConnId
    ? connections.find((c) => c.id === drawerConnId) ?? null
    : null
  const drawerTools = drawerConn
    ? mcpTools.filter((t) => t.connection_id === drawerConn.id)
    : []

  const showCardLoading = loading && connections.length === 0

  return (
    <WorkbenchShell
      title="连接中心"
      description="MCP（Streamable HTTP 自动发现）与 HTTP API（手工登记）统一接入；动态工具的审核、治理与授权在 Tool 治理进行。"
      tabs={MCP_PAGE_TABS}
      activeTab={tab}
      onTabChange={selectTab}
      actions={
        <>
          <AssetActionButton onClick={() => void reload()} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            刷新
          </AssetActionButton>
          <AssetActionButton onClick={() => void handleExport()} disabled={exporting}>
            {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            配置导出
          </AssetActionButton>
          <AssetActionButton tone="primary" onClick={handleNewConnection}>
            <Plus size={14} /> 新建连接
          </AssetActionButton>
        </>
      }
    >
      <div className="mx-auto w-full max-w-7xl space-y-4 px-4 py-5 sm:px-6">
        {error && <ErrorState title="加载失败" message={error} onRetry={() => void reload()} />}

        <StatMetricBar items={[
          { label: '连接总数', value: connections.length },
          { label: '健康连接', value: healthyCount, tone: healthyCount > 0 ? 'accent' : 'default' },
          { label: '连接异常', value: errorCount, tone: errorCount > 0 ? 'danger' : 'default' },
          { label: 'Schema 漂移', value: driftCount, tone: driftCount > 0 ? 'warn' : 'default' },
          {
            label: '发现工具数', value: mcpTools.length,
            hint: overview ? `运行时生效 ${overview.active_tools.length}` : undefined,
          },
        ]} />

        {overview && overview.derive_errors.length > 0 && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-800">
            <div className="mb-1 flex items-center gap-1 font-medium">
              <AlertTriangle size={14} /> {overview.derive_errors.length} 个工具派生失败（未进入运行时）
            </div>
            {overview.derive_errors.map((e) => (
              <div key={e.capability} className="font-mono">{e.capability}: {e.reason}</div>
            ))}
          </div>
        )}

        {tab === 'credentials' ? (
          <AssetSection title="凭据管理" icon={<KeyRound size={16} />}
            meta="只读视图：凭据密文不出服务端，仅显示配置状态与尾号；绑定/更换在连接详情的配置 Tab">
            {showCardLoading ? <Skeleton rows={4} cols={5} /> : (
              <CredentialTable connections={connections}
                onGoBind={(conn) => openDrawer(conn, 'config')} />
            )}
          </AssetSection>
        ) : (
          <>
            {showForm && (
              <AssetSection title="新建连接" icon={<Plus size={16} />}
                meta="SSRF 校验在服务端；创建后先测试连接再发现工具">
                <ConnectionForm initial={null} submitting={submitting}
                  onSubmit={(input) => void submitConnection(input, 'create')}
                  onCancel={() => setShowForm(false)} />
              </AssetSection>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input aria-label="搜索连接"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索连接名称 / 地址"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary" />
              </div>
              <span className="text-[11px] text-text-muted">{filtered.length} 个连接</span>
            </div>

            {showCardLoading ? (
              <Skeleton rows={3} cols={3} />
            ) : filtered.length === 0 ? (
              connections.length === 0 && !search ? (
                <EmptyState title="连接" description="尚未接入任何外部 MCP Server 或 HTTP API"
                  actionLabel="新建连接" onAction={() => setShowForm(true)} />
              ) : (
                <EmptyState title="连接" description="没有匹配筛选的连接；调整搜索词或切换页级 Tab" />
              )
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filtered.map((conn) => (
                  <ConnectionCard key={conn.id} conn={conn}
                    toolCount={toolCountByConn[conn.id] ?? 0}
                    onOpen={(c) => openDrawer(c)} />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {drawerConn && (
        <DetailDrawer
          open
          width="lg"
          onClose={() => { setDrawerConnId(null); setHttpToolForm(null) }}
          title={drawerConn.name}
          subtitle={
            <span className="flex flex-wrap items-center gap-1.5">
              <TransportBadge transport={drawerConn.transport} />
              <span className="font-mono">{drawerConn.base_url}</span>
            </span>
          }
          tabs={CONN_DRAWER_TABS}
          activeTab={drawerTab}
          onTabChange={setDrawerTab}
          actions={
            <>
              <AssetActionButton disabled={busyAction === `test:${drawerConn.id}`}
                onClick={() => void runTest(drawerConn)}>
                {busyAction === `test:${drawerConn.id}`
                  ? <Loader2 size={13} className="animate-spin" /> : <Plug size={13} />}
                测试健康
              </AssetActionButton>
              {drawerConn.transport === 'http_api' ? (
                <AssetActionButton onClick={() => setHttpToolForm({ connId: drawerConn.id, tool: null })}>
                  <Plus size={13} /> 登记工具
                </AssetActionButton>
              ) : (
                <AssetActionButton disabled={busyAction === `discover:${drawerConn.id}`}
                  onClick={() => void runDiscover(drawerConn)}>
                  {busyAction === `discover:${drawerConn.id}`
                    ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  重新发现
                </AssetActionButton>
              )}
              <AssetActionButton disabled={busyAction === `toggle:${drawerConn.id}`}
                onClick={() => void toggleEnabled(drawerConn)}>
                {busyAction === `toggle:${drawerConn.id}`
                  ? <Loader2 size={13} className="animate-spin" />
                  : drawerConn.enabled ? <CircleSlash size={13} /> : <CheckCircle2 size={13} />}
                {drawerConn.enabled ? '停用连接' : '启用连接'}
              </AssetActionButton>
            </>
          }
        >
          {drawerTab === 'overview' && (
            <div className="space-y-3 text-[12.5px]">
              <div className="grid gap-x-4 gap-y-2 md:grid-cols-2">
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">状态</span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusChip tone={CONN_STATUS_META[drawerConn.status]?.tone ?? 'neutral'}>
                      {CONN_STATUS_META[drawerConn.status]?.label ?? drawerConn.status}
                    </StatusChip>
                    {!drawerConn.enabled && (
                      <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-500">已停用</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">地址</span>
                  <span className="truncate font-mono text-[11px]" title={drawerConn.base_url}>{drawerConn.base_url}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">凭据</span>
                  <span>
                    {drawerConn.auth_type === 'none' ? '无认证' : (
                      <span title={`指纹 ${drawerConn.credential.fingerprint}`}>
                        Bearer ****{drawerConn.credential.last4 || '????'}
                      </span>
                    )}
                    {drawerConn.network_scope === 'private' && (
                      <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-700">内网</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">传输方式</span>
                  <TransportBadge transport={drawerConn.transport} />
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">发现版本</span>
                  <span>v{drawerConn.discovery_version}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">最近检查</span>
                  <span className="text-text-muted">
                    {drawerConn.last_synced_at ? new Date(drawerConn.last_synced_at).toLocaleString() : '—'}
                  </span>
                </div>
              </div>
              {drawerConn.last_error && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                  <span className="font-medium">最近错误：</span>{drawerConn.last_error}
                </div>
              )}
              {testResult[drawerConn.id] && (
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[11px]">
                  {testResult[drawerConn.id].ok
                    ? <span className="text-emerald-700">连接测试：成功 {testResult[drawerConn.id].elapsed_ms}ms · {testResult[drawerConn.id].tool_count} 个工具</span>
                    : <span className="text-red-600">连接测试：失败 {testResult[drawerConn.id].error_class} · {testResult[drawerConn.id].error}</span>}
                </div>
              )}
              {discoverResult[drawerConn.id] && (
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[11px] text-text-muted">
                  上次发现 v{discoverResult[drawerConn.id].discovery_version}：
                  新增 {(discoverResult[drawerConn.id].new ?? []).map((t) => t.tool).join('、') || '无'}；
                  变化 {(discoverResult[drawerConn.id].changed ?? []).map((t) => t.tool).join('、') || '无'}；
                  消失 {(discoverResult[drawerConn.id].retired ?? []).map((t) => t.tool).join('、') || '无'}
                  {(discoverResult[drawerConn.id].skipped ?? []).length > 0 &&
                    `；跳过 ${(discoverResult[drawerConn.id].skipped ?? []).map((t) => t.tool).join('、')}`}
                </div>
              )}
              <div className="text-[11px] text-text-muted">
                编辑连接信息请切换到「配置」Tab；动态工具的审核与授权在 Tool 治理进行。
              </div>
            </div>
          )}

          {drawerTab === 'tools' && (
            <div className="space-y-3">
              {httpToolForm?.connId === drawerConn.id && (
                <div className="rounded-lg border border-border-subtle bg-surface-elevated/40">
                  <div className="px-4 pt-3 text-[12px] font-medium">
                    {httpToolForm.tool
                      ? `编辑调用定义：${httpToolForm.tool.tool_name}`
                      : `登记 HTTP API 工具：${drawerConn.name}`}
                  </div>
                  <HttpToolForm mode={httpToolForm.tool ? 'edit' : 'register'}
                    initial={httpToolForm.tool}
                    submitting={submitting}
                    onRegister={(input) => void submitRegisterHttpTool(drawerConn.id, input)}
                    onUpdate={(input) =>
                      void submitUpdateHttpTool(httpToolForm.tool!.id, input)}
                    onCancel={() => setHttpToolForm(null)} />
                </div>
              )}
              {drawerTools.length === 0 && !httpToolForm ? (
                <EmptyState title="已发现工具"
                  description={drawerConn.transport === 'http_api'
                    ? '尚未登记工具；点击右上角「登记工具」手工登记'
                    : '尚未发现工具；点击右上角「重新发现」从上游拉取'} />
              ) : (
                <ul className="divide-y divide-border-subtle">
                  {drawerTools.map((tool) => {
                    const status = TOOL_STATUS_META[tool.status] ?? { label: tool.status, tone: 'neutral' as StatusTone }
                    const isHttp = tool.conn_transport === 'http_api'
                    return (
                      <li key={tool.id} className="py-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[12px] text-text-primary">{tool.tool_name}</span>
                          <span className="font-mono text-[11px] text-text-muted">{tool.capability}</span>
                          <StatusChip tone={status.tone}>{status.label}</StatusChip>
                          {tool.schema_changed && (
                            <span className="rounded bg-red-100 px-1 text-[10px] text-red-700"
                              title="上游 Schema 已变化，需重新审核">Schema 漂移</span>
                          )}
                          <span className="text-[11px] text-text-muted">
                            风险 {tool.governance?.risk_level ?? '—'}
                          </span>
                          {isHttp && (
                            <AssetActionButton
                              onClick={() => setHttpToolForm({ connId: drawerConn.id, tool })}>
                              编辑调用
                            </AssetActionButton>
                          )}
                          <Link
                            href={`/tools?q=${encodeURIComponent(tool.capability)}`}
                            className="ml-auto text-[11px] font-medium text-accent hover:underline"
                            title="在 Tool 治理中查看并审核该工具（预填搜索）">
                            前往 Tool 治理 →
                          </Link>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
              <div className="text-[11px] text-text-muted">
                工具的审核、执行策略与授权在「Tool 治理」统一操作；此处仅查看发现结果。
              </div>
            </div>
          )}

          {drawerTab === 'config' && (
            <div className="space-y-4">
              <ConnectionForm initial={drawerConn} submitting={submitting}
                onSubmit={(input) => void submitConnection(input, 'edit')}
                onCancel={() => setDrawerTab('overview')} />
              <div className="rounded-lg border border-red-200 bg-red-50/50 p-3">
                <div className="mb-2 text-[12px] font-medium text-red-700">危险操作</div>
                <div className="mb-2 text-[11px] text-text-muted">
                  删除连接会一并校验工具记录与有效授权；仍被引用时服务端会拒绝。
                </div>
                <button type="button" disabled={busyAction === `delete:${drawerConn.id}`}
                  onClick={() => void removeConnection(drawerConn)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-[12px] font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
                  {busyAction === `delete:${drawerConn.id}`
                    ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  删除连接
                </button>
              </div>
            </div>
          )}

          {drawerTab === 'health' && <ConnectionHealthSection conn={drawerConn} />}

          {drawerTab === 'versions' && <VersionHistoryStub conn={drawerConn} />}
        </DetailDrawer>
      )}
    </WorkbenchShell>
  )
}
