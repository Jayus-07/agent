/**
 * /mcp HTTP API 工具登记/编辑表单（自 page.tsx 拆出，纯移动）。
 * http_api 连接专用，入口在连接抽屉。
 * method/path/query/headers/body_from 服务端白名单校验；
 * headers 禁 Authorization/Cookie（凭据统一注入）。
 */
import { useState } from 'react'
import { CheckCircle2, Loader2 } from 'lucide-react'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import type { HttpToolInvocation, McpToolSummary } from '@/api/mcp'

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

export function HttpToolForm({
  initial, onRegister, onUpdate, onCancel, submitting, mode,
}: {
  initial?: McpToolSummary | null
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
      // 列表行（McpToolSummary）不含 snapshots——该字段仅详情端点返回，
      // 此预填在列表数据流下历来恒为默认值；如需带出当前 Schema 应改为
      // 编辑前先拉取详情（本次保持既有行为不变）。
      { type: 'object', properties: {}, additionalProperties: false },
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
