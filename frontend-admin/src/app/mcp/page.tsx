'use client'

/**
 * /mcp — MCP 能力中心（B1/B2/B3/B4，ADR-0011）
 *
 * 工作流：新增连接（SSRF 校验在服务端）→ 连接测试 → 工具发现 → 审核
 * （治理配置 + 路由示例）→ 授权给产品入口 → 只读测试调用。
 * 凭据只上行一次，服务端仅回 fingerprint/last4；schema_changed 的工具
 * 必须重新审核才会回到运行时。
 */
import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, CircleSlash,
  Clock, KeyRound, Loader2, Plug, Plus, RefreshCw, Search, ShieldCheck, Trash2,
} from 'lucide-react'
import { AssetActionButton, AssetPageShell, AssetSection, AssetStatCard } from '@/components/layout/AssetPageShell'
import EmptyState from '@/components/shared/EmptyState'
import ErrorState from '@/components/shared/ErrorState'
import { useToast } from '@/components/shared/Toast'
import {
  createMcpConnection, deleteMcpConnection, disableMcpTool, discoverMcpTools,
  getConnectionTools, getMcpConnections, getMcpOverview,
  grantMcpTool, registerHttpTool, reviewMcpTool, setMcpConnectionEnabled,
  testInvokeMcpTool, testMcpConnection, updateHttpToolInvocation,
  updateMcpConnection,
  type ConnectionInput, type ConnectionTestResult, type DiscoverResult,
  type HttpToolInvocation, type McpConnection, type McpOverview,
  type McpTool, type TestInvokeResult,
} from '@/api/mcp'
import { ApiError } from '@/api/client'

const CONN_STATUS_META: Record<string, { label: string; cls: string }> = {
  never_connected: { label: '未连接', cls: 'bg-slate-100 text-slate-600' },
  connected: { label: '已连接', cls: 'bg-emerald-100 text-emerald-700' },
  error: { label: '连接异常', cls: 'bg-red-100 text-red-700' },
  disabled: { label: '已停用', cls: 'bg-amber-100 text-amber-700' },
}

const TOOL_STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '待审核', cls: 'bg-amber-100 text-amber-700' },
  approved: { label: '已生效', cls: 'bg-emerald-100 text-emerald-700' },
  rejected: { label: '已拒绝', cls: 'bg-red-100 text-red-700' },
  disabled: { label: '已停用', cls: 'bg-slate-100 text-slate-600' },
  retired: { label: '上游已消失', cls: 'bg-zinc-100 text-zinc-600' },
}

const RISK_OPTIONS = [
  { value: 'R0', label: 'R0 无风险（纯只读展示）' },
  { value: 'R1', label: 'R1 低风险（默认只读）' },
  { value: 'R2', label: 'R2 高风险（强制用户确认）' },
  { value: 'R3', label: 'R3 严重（强制确认 + 管理员审批）' },
]

const OPERATION_OPTIONS = [
  { value: 'read', label: 'read 只读' },
  { value: 'create', label: 'create 写入' },
  { value: 'update', label: 'update 更新' },
  { value: 'delete', label: 'delete 删除' },
  { value: 'send', label: 'send 发送' },
  { value: 'external_write', label: 'external_write 外部写' },
]

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return e instanceof Error ? e.message : '请求失败'
}

/** 连接编辑表单（新增/编辑复用；secret 留空 = 保持现有凭据）。
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
  method: 'GET' | 'POST'; path: string;
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
  const httpMethods = new Set(['get', 'post'])
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
        method: m.toUpperCase() as 'GET' | 'POST',
        path: rawPath,
        query,
        inputSchema,
      }
    }
  }
  return null
}

/** HTTP API 工具登记/编辑表单（http_api 连接专用）。
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
  const [method, setMethod] = useState<'GET' | 'POST'>(initialInv?.method ?? 'GET')
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
        <select value={method} onChange={(e) => setMethod(e.target.value as 'GET' | 'POST')}
          className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
          <option value="GET">GET</option>
          <option value="POST">POST</option>
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

/** 工具审核 + 授权 + 测试调用面板（行展开）。 */
function ToolPanel({
  tool, onDone,
}: {
  tool: McpTool
  onDone: () => void
}) {
  const toast = useToast()
  const [tab, setTab] = useState<'review' | 'invoke'>(tool.status === 'approved' ? 'invoke' : 'review')
  const [description, setDescription] = useState(tool.description)
  const [operation, setOperation] = useState(tool.governance?.operation ?? 'read')
  const [risk, setRisk] = useState(tool.governance?.risk_level ?? 'R1')
  // 后端 derive_tool_spec 读 governance.runtime.timeout_ms（与静态
  // capabilities.yaml 的 tool_spec.runtime 同构）；顶层 timeout_ms 仅作
  // 历史数据回显兜底，提交一律走 runtime 路径
  const [timeoutMs, setTimeoutMs] = useState(
    tool.governance?.runtime?.timeout_ms ?? tool.governance?.timeout_ms ?? 15000)
  const [examplesText, setExamplesText] = useState((tool.examples ?? []).join('\n'))
  const [plannerExample, setPlannerExample] = useState(
    JSON.stringify(tool.governance?.planner_example ?? { query: '示例问题' }, null, 0))
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [invokeArgs, setInvokeArgs] = useState('{}')
  const [invokeProduct, setInvokeProduct] = useState<'internal' | 'agent' | 'customer_service'>('internal')
  const [invokeResult, setInvokeResult] = useState<TestInvokeResult | null>(null)
  const schema = tool.snapshots?.[0]?.input_schema as
    { properties?: Record<string, { type?: string; description?: string }> } | undefined
  const properties = Object.entries(schema?.properties ?? {})

  const submitReview = async (action: 'approve' | 'reject') => {
    setSubmitting(true)
    try {
      let plannerExampleParsed: Record<string, unknown> | undefined
      try {
        plannerExampleParsed = plannerExample.trim() ? JSON.parse(plannerExample) : undefined
      } catch {
        toast.error('Planner 示例必须是合法 JSON')
        return
      }
      await reviewMcpTool(tool.id, {
        action,
        description: description.trim(),
        governance: action === 'approve' ? {
          operation, risk_level: risk, confirmation_policy:
            risk === 'R2' ? 'user_confirmation'
            : risk === 'R3' ? 'dual' : 'none',
          runtime: { timeout_ms: timeoutMs },
          planner_example: plannerExampleParsed,
        } : {},
        examples: action === 'approve'
          ? examplesText.split('\n').map((s) => s.trim()).filter(Boolean) : [],
        notes,
      })
      toast.success(action === 'approve' ? '审核通过，工具已进入运行时' : '已拒绝')
      onDone()
    } catch (e) {
      toast.error(`审核失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const runInvoke = async () => {
    setSubmitting(true)
    setInvokeResult(null)
    try {
      let args: Record<string, unknown>
      try {
        args = JSON.parse(invokeArgs || '{}')
      } catch {
        toast.error('参数必须是合法 JSON 对象')
        return
      }
      const result = await testInvokeMcpTool(tool.id, args, invokeProduct)
      setInvokeResult(result)
      if (result.tool_status === 'success') toast.success(`调用成功 (${result.elapsed_ms}ms)`)
      else toast.warning(`调用未成功: ${result.tool_status}${result.error_code ? ` / ${result.error_code}` : ''}`)
    } catch (e) {
      toast.error(`测试调用失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="border-t border-border-subtle bg-surface-elevated/40 px-4 py-3 text-[12px]">
      <div className="mb-3 flex items-center gap-2">
        {(['review', 'invoke'] as const).map((key) => (
          <button key={key}
            onClick={() => setTab(key)}
            className={`rounded px-2 py-1 ${tab === key ? 'bg-accent text-white' : 'bg-surface-base text-text-muted'}`}>
            {key === 'review' ? '审核与治理配置' : '只读测试调用'}
          </button>
        ))}
        <span className="ml-auto font-mono text-[11px] text-text-muted">
          {tool.capability} · hash {tool.content_hash.slice(0, 8)} · v{tool.last_seen_version}
        </span>
      </div>

      {tab === 'review' ? (
        <div className="grid gap-3 md:grid-cols-2">
          <label className="flex flex-col gap-1 md:col-span-2">
            <span className="text-text-muted">平台描述（进入 Planner / 路由 prompt，可改写上游声明）</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">操作类型（写操作强制幂等与零重试）</span>
            <select value={operation} onChange={(e) => setOperation(e.target.value)}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
              {OPERATION_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">风险等级（决定确认/审批策略）</span>
            <select value={risk} onChange={(e) => setRisk(e.target.value)}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
              {RISK_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">超时（毫秒）</span>
            <input type="number" value={timeoutMs} min={1000} max={120000} step={1000}
              onChange={(e) => setTimeoutMs(Number(e.target.value))}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">Planner 参数示例（JSON）</span>
            <textarea value={plannerExample} onChange={(e) => setPlannerExample(e.target.value)} rows={3}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono" />
          </label>
          <label className="flex flex-col gap-1 md:col-span-2">
            <span className="text-text-muted">路由示例（每行一条；≥2 条才进入路由候选）</span>
            <textarea value={examplesText} onChange={(e) => setExamplesText(e.target.value)} rows={3}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5"
              placeholder={'用这个工具查一下数据\n调用外部接口获取结果'} />
          </label>
          <label className="flex flex-col gap-1 md:col-span-2">
            <span className="text-text-muted">审核备注（留痕）</span>
            <input value={notes} onChange={(e) => setNotes(e.target.value)}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5" />
          </label>
          {tool.conn_transport === 'http_api' && (tool.snapshots?.length ?? 0) > 1 && (
            <div className="md:col-span-2">
              <div className="mb-1 text-text-muted">调用定义版本对比（最近 3 版；新版漂移需重审）</div>
              <div className="grid gap-2">
                {(tool.snapshots ?? []).slice(0, 3).map((snap, idx) => (
                  <div key={`${snap.discovery_version}-${idx}`}
                    className={`rounded border p-2 font-mono text-[11px] ${
                      idx === 0 ? 'border-accent/50 bg-accent/5' : 'border-border-subtle'
                    }`}>
                    <div className="mb-1 flex items-center gap-2">
                      <span className="font-medium">v{snap.discovery_version}</span>
                      {idx === 0 && (
                        <span className="rounded bg-accent/10 px-1 text-[10px] text-accent">当前</span>
                      )}
                      <span className="text-text-muted">hash {snap.content_hash.slice(0, 8)}</span>
                      <span className="text-text-muted">
                        {new Date(snap.discovered_at).toLocaleString()}
                      </span>
                    </div>
                    {snap.invocation ? (
                      <pre className="whitespace-pre-wrap break-all">
                        {JSON.stringify(snap.invocation, null, 0)}
                      </pre>
                    ) : (
                      <div className="text-text-muted">（该版本未记录调用定义）</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="flex items-center gap-2 md:col-span-2">
            <AssetActionButton tone="primary" disabled={submitting} onClick={() => submitReview('approve')}>
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
              审核通过并生效
            </AssetActionButton>
            <AssetActionButton disabled={submitting} onClick={() => submitReview('reject')}>
              拒绝
            </AssetActionButton>
            <span className="text-text-muted">审核通过后仍需在列表中授权给产品入口，Agent 才能调用。</span>
          </div>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2">
            <div className="mb-1 text-text-muted">参数 Schema（来自发现快照）</div>
            {properties.length === 0 ? <div className="text-text-muted">无参数</div> : (
              <table className="w-full">
                <thead>
                  <tr className="text-left text-[11px] text-text-muted">
                    <th className="py-1 pr-3 font-normal">参数</th>
                    <th className="py-1 pr-3 font-normal">类型</th>
                    <th className="py-1 font-normal">说明（上游声明，不可信）</th>
                  </tr>
                </thead>
                <tbody>
                  {properties.map(([pname, pdef]) => (
                    <tr key={pname} className="border-t border-border-subtle">
                      <td className="py-1 pr-3 font-mono">{pname}</td>
                      <td className="py-1 pr-3">{pdef?.type ?? '-'}</td>
                      <td className="py-1 text-text-muted">{pdef?.description ?? ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">测试参数（JSON，走完整治理链）</span>
            <textarea value={invokeArgs} onChange={(e) => setInvokeArgs(e.target.value)} rows={4}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">产品身份（验证真实授权边界）</span>
            <select value={invokeProduct} onChange={(e) => setInvokeProduct(e.target.value as typeof invokeProduct)}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
              <option value="internal">internal（运维身份，不校验产品授权）</option>
              <option value="agent">agent（主助手，校验授权）</option>
              <option value="customer_service">customer_service（客服，校验授权）</option>
            </select>
            <span className="text-text-muted">仅只读工具可测试；写操作走真实业务链路。</span>
          </label>
          <div className="md:col-span-2">
            <AssetActionButton tone="primary" disabled={submitting} onClick={runInvoke}>
              {submitting ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
              执行测试调用
            </AssetActionButton>
          </div>
          {invokeResult && (
            <div className="md:col-span-2 rounded border border-border-subtle bg-surface-base p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                  invokeResult.tool_status === 'success'
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-red-100 text-red-700'}`}>
                  {invokeResult.tool_status}
                </span>
                <span className="text-text-muted">{invokeResult.elapsed_ms}ms</span>
                {invokeResult.error_code && (
                  <span className="font-mono text-[11px] text-red-600">
                    {invokeResult.error_code}: {invokeResult.error_message}
                  </span>
                )}
              </div>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-surface-elevated p-2 text-[11px]">
                {JSON.stringify(invokeResult.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function McpPage() {
  const toast = useToast()
  const [overview, setOverview] = useState<McpOverview | null>(null)
  const [connections, setConnections] = useState<McpConnection[]>([])
  const [toolsByConn, setToolsByConn] = useState<Record<string, McpTool[]>>({})
  const [snapshotsByTool, setSnapshotsByTool] = useState<Record<number, McpTool>>({})
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<McpConnection | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [search, setSearch] = useState('')
  const [expandedConn, setExpandedConn] = useState<string | null>(null)
  const [expandedTool, setExpandedTool] = useState<number | null>(null)
  const [busyAction, setBusyAction] = useState('')
  const [testResult, setTestResult] = useState<Record<string, ConnectionTestResult>>({})
  const [discoverResult, setDiscoverResult] = useState<Record<string, DiscoverResult>>({})
  // HTTP API 工具登记/编辑：{ connId, tool } | null（tool 空 = 新登记）
  const [httpToolForm, setHttpToolForm] = useState<
    { connId: string; tool: McpTool | null } | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [ov, conns] = await Promise.all([getMcpOverview(), getMcpConnections()])
      setOverview(ov)
      setConnections(conns)
      if (expandedConn) {
        const tools = await getConnectionTools(expandedConn)
        setToolsByConn((prev) => ({ ...prev, [expandedConn]: tools }))
      }
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [expandedConn])

  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadTools = async (connId: string) => {
    try {
      const tools = await getConnectionTools(connId)
      setToolsByConn((prev) => ({ ...prev, [connId]: tools }))
    } catch (e) {
      toast.error(`工具列表加载失败: ${errMessage(e)}`)
    }
  }

  const toggleConn = async (connId: string) => {
    if (expandedConn === connId) {
      setExpandedConn(null)
      return
    }
    setExpandedConn(connId)
    setExpandedTool(null)
    await loadTools(connId)
  }

  const submitConnection = async (input: ConnectionInput & { keepSecret?: boolean }) => {
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
      }
      setShowForm(false)
      setEditing(null)
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
      await loadTools(conn.id)
      // 重新发现可能改写 Schema/状态：详情缓存整体失效，展开中的工具重拉
      await resyncToolDetails()
    } catch (e) {
      toast.error(`发现失败: ${errMessage(e)}`)
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
      await loadTools(connId)
      await reload()
    } catch (e) {
      toast.error(`登记失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const submitUpdateHttpTool = async (
    connId: string, toolId: number,
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
      await resyncToolDetails([toolId])
      if (expandedConn) await loadTools(expandedConn)
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
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
      await reload()
    } catch (e) {
      toast.error(`删除被拒绝: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const toggleGrant = async (tool: McpTool, product: 'agent' | 'customer_service') => {
    const active = (tool.grants ?? []).some((g) => g.product_key === product)
    setBusyAction(`grant:${tool.id}:${product}`)
    try {
      await grantMcpTool(tool.id, product, active)
      toast.success(active ? `已撤销 ${product} 授权` : `已授权给 ${product}`)
      if (expandedConn) await loadTools(expandedConn)
      await resyncToolDetails([tool.id])
      await reload()
    } catch (e) {
      toast.error(`授权操作失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const disableTool = async (tool: McpTool) => {
    setBusyAction(`disableTool:${tool.id}`)
    try {
      await disableMcpTool(tool.id)
      toast.success('工具已停用；写调用立即被拦截，读调用最迟 15 秒退出候选')
      if (expandedConn) await loadTools(expandedConn)
      await resyncToolDetails([tool.id])
      await reload()
    } catch (e) {
      toast.error(`停用失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  /** 变更后同步详情视图：失效缓存并按需重拉——否则展开面板继续用旧
   *  Schema/治理配置覆盖最新列表数据（审核人在旧内容上提交重审）。 */
  const resyncToolDetails = useCallback(async (toolIds?: number[]) => {
    invalidateMcpToolDetailCache(toolIds)
    setSnapshotsByTool((prev) => {
      const keys = new Set(toolIds ?? Object.keys(prev).map(Number))
      if (![...keys].some((k) => k in prev)) return prev
      const next = { ...prev }
      for (const k of keys) delete next[k]
      return next
    })
    const affected = toolIds ?? (expandedTool != null ? [expandedTool] : [])
    if (expandedTool != null && affected.includes(expandedTool)) {
      try {
        const detail = await fetchMcpToolDetailFresh(expandedTool)
        setSnapshotsByTool((prev) => ({ ...prev, [expandedTool]: detail }))
      } catch {
        // 重拉失败保持列表态，下次展开重新加载
      }
    }
  }, [expandedTool])

  const openToolDetail = async (tool: McpTool) => {
    if (expandedTool === tool.id) {
      setExpandedTool(null)
      return
    }
    if (!tool.snapshots) {
      try {
        const detail = await getMcpToolDetailCached(tool.id)
        setSnapshotsByTool((prev) => ({ ...prev, [tool.id]: detail }))
      } catch (e) {
        toast.error(`详情加载失败: ${errMessage(e)}`)
      }
    }
    setExpandedTool(tool.id)
  }

  const filtered = connections.filter((c) =>
    !search || c.name.toLowerCase().includes(search.toLowerCase())
    || c.base_url.toLowerCase().includes(search.toLowerCase()))
  const pendingCount = Object.values(toolsByConn)
    .flat().filter((t) => t.status === 'pending').length

  return (
    <AssetPageShell
      title="MCP 能力中心"
      desc="连接中心：MCP（自动发现）与 HTTP API（手工登记）统一接入 → 审核 → 授权，全程走统一治理 Runtime；停用与撤权对写调用立即拦截（出网前强一致复核），读调用最迟 15 秒收敛。"
      actions={
        <>
          <AssetActionButton onClick={() => void reload()} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            刷新
          </AssetActionButton>
          <AssetActionButton tone="primary" onClick={() => { setEditing(null); setShowForm(true) }}>
            <Plus size={14} /> 新增连接
          </AssetActionButton>
        </>
      }
    >
      {error && <ErrorState title="加载失败" message={error} onRetry={() => void reload()} className="mb-4" />}

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <AssetStatCard label="连接" value={connections.length} icon={<Plug size={16} />} />
        <AssetStatCard label="运行时生效工具" value={overview?.active_tools.length ?? 0}
          icon={<CheckCircle2 size={16} />} hint={overview?.loaded ? undefined : '快照未加载（DB 不可用）'} />
        <AssetStatCard label="待审核工具" value={pendingCount} icon={<Clock size={16} />}
          tone={pendingCount > 0 ? 'warn' : 'default'} />
        <AssetStatCard label="产品授权" value={overview?.grant_count ?? 0} icon={<KeyRound size={16} />} />
      </div>

      {overview && overview.derive_errors.length > 0 && (
        <div className="mb-4 rounded border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-800">
          <div className="mb-1 flex items-center gap-1 font-medium">
            <AlertTriangle size={14} /> {overview.derive_errors.length} 个工具派生失败（未进入运行时）
          </div>
          {overview.derive_errors.map((e) => (
            <div key={e.capability} className="font-mono">{e.capability}: {e.reason}</div>
          ))}
        </div>
      )}

      <AssetSection title="MCP 连接" icon={<Plug size={16} />}
        meta={`${filtered.length} 个 · Streamable HTTP`}>
        {showForm ? (
          <div className="border-b border-border-subtle">
            <ConnectionForm initial={editing} onSubmit={submitConnection}
              onCancel={() => { setShowForm(false); setEditing(null) }}
              submitting={submitting} />
          </div>
        ) : null}
        {filtered.length === 0 && !showForm ? (
          <EmptyState title="MCP 连接" description="尚未接入任何外部 MCP Server"
            actionLabel="新增连接" onAction={() => { setEditing(null); setShowForm(true) }} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
                  <th className="px-4 py-2 font-normal">名称</th>
                  <th className="px-2 py-2 font-normal">地址</th>
                  <th className="px-2 py-2 font-normal">认证</th>
                  <th className="px-2 py-2 font-normal">状态</th>
                  <th className="px-2 py-2 font-normal">发现版本</th>
                  <th className="px-2 py-2 font-normal">最后同步</th>
                  <th className="px-4 py-2 text-right font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((conn) => {
                  const open = expandedConn === conn.id
                  const tr = testResult[conn.id]
                  const dr = discoverResult[conn.id]
                  return (
                    <Fragment key={conn.id}>
                      <tr
                        className="cursor-pointer border-b border-border-subtle hover:bg-surface-hover/50"
                        onClick={() => void toggleConn(conn.id)}>
                        <td className="px-4 py-2">
                          <span className="inline-flex items-center gap-1 font-medium">
                            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            {conn.name}
                          </span>
                          {!conn.enabled && (
                            <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">已停用</span>
                          )}
                        </td>
                        <td className="px-2 py-2 font-mono text-[11px] text-text-muted">{conn.base_url}</td>
                        <td className="px-2 py-2">
                          {conn.auth_type === 'none' ? '无' : (
                            <span title={`指纹 ${conn.credential.fingerprint}`}>
                              Bearer ****{conn.credential.last4 || '????'}
                            </span>
                          )}
                          {conn.network_scope === 'private' && (
                            <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-700">内网</span>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${CONN_STATUS_META[conn.status]?.cls ?? ''}`}>
                            {CONN_STATUS_META[conn.status]?.label ?? conn.status}
                          </span>
                          {conn.last_error && (
                            <div className="mt-0.5 max-w-48 truncate text-[10px] text-red-600" title={conn.last_error}>
                              {conn.last_error}
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-2">v{conn.discovery_version}</td>
                        <td className="px-2 py-2 text-text-muted">
                          {conn.last_synced_at ? new Date(conn.last_synced_at).toLocaleString() : '—'}
                        </td>
                        <td className="px-4 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                          <div className="inline-flex gap-1">
                            <AssetActionButton disabled={busyAction === `test:${conn.id}`} onClick={() => void runTest(conn)}>
                              {busyAction === `test:${conn.id}` ? <Loader2 size={13} className="animate-spin" /> : <Plug size={13} />} 测试
                            </AssetActionButton>
                            {conn.transport === 'http_api' ? (
                              <AssetActionButton onClick={() => setHttpToolForm({ connId: conn.id, tool: null })}>
                                <Plus size={13} /> 登记工具
                              </AssetActionButton>
                            ) : (
                              <AssetActionButton disabled={busyAction === `discover:${conn.id}`} onClick={() => void runDiscover(conn)}>
                                {busyAction === `discover:${conn.id}` ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />} 发现
                              </AssetActionButton>
                            )}
                            <AssetActionButton disabled={busyAction === `toggle:${conn.id}`} onClick={() => void toggleEnabled(conn)}>
                              {conn.enabled ? <CircleSlash size={13} /> : <CheckCircle2 size={13} />}
                            </AssetActionButton>
                            <AssetActionButton onClick={() => { setEditing(conn); setShowForm(true) }}>编辑</AssetActionButton>
                            <AssetActionButton disabled={busyAction === `delete:${conn.id}`} onClick={() => void removeConnection(conn)}>
                              <Trash2 size={13} />
                            </AssetActionButton>
                          </div>
                        </td>
                      </tr>
                      {tr && (
                        <tr className="border-b border-border-subtle bg-surface-elevated/30">
                          <td colSpan={7} className="px-4 py-1.5 text-[11px]">
                            {tr.ok
                              ? <span className="text-emerald-700">连接测试：成功 {tr.elapsed_ms}ms · {tr.tool_count} 个工具</span>
                              : <span className="text-red-600">连接测试：失败 {tr.error_class} · {tr.error}</span>}
                          </td>
                        </tr>
                      )}
                      {dr && (
                        <tr className="border-b border-border-subtle bg-surface-elevated/30">
                          <td colSpan={7} className="px-4 py-1.5 text-[11px] text-text-muted">
                            上次发现 v{dr.discovery_version}：
                            新增 {(dr.new ?? []).map((t) => t.tool).join('、') || '无'}；
                            变化 {(dr.changed ?? []).map((t) => t.tool).join('、') || '无'}；
                            消失 {(dr.retired ?? []).map((t) => t.tool).join('、') || '无'}
                            {(dr.skipped ?? []).length > 0 && `；跳过 ${(dr.skipped ?? []).map((t) => t.tool).join('、')}`}
                          </td>
                        </tr>
                      )}
                      {open && (
                        <tr className="border-b border-border-subtle">
                          <td colSpan={7} className="p-0">
                            {httpToolForm?.connId === conn.id && (
                              <div className="border-b border-border-subtle bg-surface-elevated/40">
                                <div className="px-4 pt-3 text-[12px] font-medium">
                                  {httpToolForm.tool
                                    ? `编辑调用定义：${httpToolForm.tool.tool_name}`
                                    : `登记 HTTP API 工具：${conn.name}`}
                                </div>
                                <HttpToolForm mode={httpToolForm.tool ? 'edit' : 'register'}
                                  initial={httpToolForm.tool}
                                  submitting={submitting}
                                  onRegister={(input) =>
                                    void submitRegisterHttpTool(conn.id, input)}
                                  onUpdate={(input) =>
                                    void submitUpdateHttpTool(
                                      conn.id, httpToolForm.tool!.id, input)}
                                  onCancel={() => setHttpToolForm(null)} />
                              </div>
                            )}
                            <ToolListSection connId={conn.id}
                              tools={toolsByConn[conn.id] ?? []}
                              snapshotsByTool={snapshotsByTool}
                              expandedTool={expandedTool}
                              onToggleTool={openToolDetail}
                              onToggleGrant={toggleGrant}
                              onDisableTool={disableTool}
                              onToolMutated={resyncToolDetails}
                              onEditInvocation={(tool) =>
                                setHttpToolForm({ connId: conn.id, tool })}
                              onReload={reload}
                              busyAction={busyAction} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </AssetSection>
    </AssetPageShell>
  )
}

/** 连接展开后的工具清单（含授权开关与行展开审核/测试面板）。 */
function ToolListSection({
  connId, tools, snapshotsByTool, expandedTool, onToggleTool,
  onToggleGrant, onDisableTool, onToolMutated, onEditInvocation, onReload,
  busyAction,
}: {
  connId: string
  tools: McpTool[]
  snapshotsByTool: Record<number, McpTool>
  expandedTool: number | null
  onToggleTool: (tool: McpTool) => void
  onToggleGrant: (tool: McpTool, product: 'agent' | 'customer_service') => void
  onDisableTool: (tool: McpTool) => void
  /** 单工具变更（审核/停用/授权）后失效其详情缓存 */
  onToolMutated: (toolIds?: number[]) => Promise<void>
  /** HTTP API 工具：打开调用定义编辑表单 */
  onEditInvocation: (tool: McpTool) => void
  onReload: () => Promise<void>
  busyAction: string
}) {
  if (tools.length === 0) {
    return (
      <div className="p-4">
        <EmptyState title="工具" description="尚未发现工具；MCP 连接点击「发现」，HTTP API 连接点击「登记工具」" />
      </div>
    )
  }
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
              <th className="px-4 py-2 font-normal">上游工具</th>
              <th className="px-2 py-2 font-normal">Capability</th>
              <th className="px-2 py-2 font-normal">状态</th>
              <th className="px-2 py-2 font-normal">授权</th>
              <th className="px-4 py-2 text-right font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {tools.map((tool) => {
              // 列表数据（状态/hash/漂移标记）是最新事实源；详情快照只补
              // snapshots 等列表没有的字段，绝不让过期详情反向覆盖列表
              const merged = { ...(snapshotsByTool[tool.id] ?? {}), ...tool }
              const open = expandedTool === tool.id
              const grants = tool.grants ?? []
              return (
                <Fragment key={`${connId}-${tool.id}`}>
                  <tr
                    className="cursor-pointer border-b border-border-subtle hover:bg-surface-hover/50"
                    onClick={() => onToggleTool(tool)}>
                    <td className="px-4 py-2 font-mono">
                      <span className="inline-flex items-center gap-1">
                        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        {tool.tool_name}
                      </span>
                      {tool.schema_changed && (
                        <span className="ml-1 rounded bg-red-100 px-1 text-[10px] text-red-700"
                          title="上游 Schema 已变化，需重新审核">Schema 已变化</span>
                      )}
                    </td>
                    <td className="px-2 py-2 font-mono text-[11px]">{tool.capability}</td>
                    <td className="px-2 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${TOOL_STATUS_META[tool.status]?.cls ?? ''}`}>
                        {TOOL_STATUS_META[tool.status]?.label ?? tool.status}
                      </span>
                    </td>
                    <td className="px-2 py-2" onClick={(e) => e.stopPropagation()}>
                      <div className="flex gap-1">
                        {(['agent', 'customer_service'] as const).map((product) => {
                          const active = grants.some((g) => g.product_key === product)
                          return (
                            <button key={product} disabled={tool.status !== 'approved' && !active}
                              onClick={() => onToggleGrant(tool, product)}
                              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                active ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'
                              } ${busyAction === `grant:${tool.id}:${product}` ? 'opacity-50' : ''}`}>
                              {product === 'agent' ? '主助手' : '客服'}
                              {active ? ' ✓' : ''}
                            </button>
                          )
                        })}
                      </div>
                    </td>
                    <td className="px-4 py-2 text-right" onClick={(e) => e.stopPropagation()}>
                      <div className="inline-flex gap-1">
                        {tool.conn_transport === 'http_api' && (
                          <AssetActionButton onClick={() => onEditInvocation(tool)}>
                            编辑调用
                          </AssetActionButton>
                        )}
                        {tool.status === 'approved' && (
                          <AssetActionButton disabled={busyAction === `disableTool:${tool.id}`}
                            onClick={() => onDisableTool(tool)}>
                            停用
                          </AssetActionButton>
                        )}
                      </div>
                    </td>
                  </tr>
                  {open && (
                    <tr className="border-b border-border-subtle">
                      <td colSpan={5} className="p-0">
                        <ToolPanel tool={merged}
                          onDone={async () => {
                            await onReload()
                            await onToolMutated([tool.id])
                          }} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

let _detailCache: Record<number, McpTool> = {}
async function getMcpToolDetailCached(id: number): Promise<McpTool> {
  if (_detailCache[id]) return _detailCache[id]
  const { getMcpToolDetail } = await import('@/api/mcp')
  const detail = await getMcpToolDetail(id)
  _detailCache = { ..._detailCache, [id]: detail }
  return detail
}

/** 失效详情缓存：重新发现 / 审核 / 停用 / 授权变更后必须调用，
 *  否则审核面板会继续展示旧 Schema 与旧治理配置。 */
function invalidateMcpToolDetailCache(ids?: number[]) {
  if (!ids) {
    _detailCache = {}
    return
  }
  _detailCache = Object.fromEntries(
    Object.entries(_detailCache).filter(([k]) => !ids.includes(Number(k))))
}

async function fetchMcpToolDetailFresh(id: number): Promise<McpTool> {
  const { getMcpToolDetail } = await import('@/api/mcp')
  return getMcpToolDetail(id)
}
