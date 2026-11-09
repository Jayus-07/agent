/**
 * /tools 动态工具详情抽屉的「调试」：只读测试调用（走完整治理链）。
 * （自 page.tsx 拆出，纯移动。）invokeSchemaProperties 同时被抽屉
 * 「Schema」Tab 使用，一并导出。
 */
import { useState } from 'react'
import { Loader2, Plug } from 'lucide-react'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import { testInvokeMcpTool, type McpTool, type TestInvokeResult } from '@/api/mcp'
import { errMessage } from '@/api/client'

export type SchemaPropertyDef = { type?: string; description?: string }

export function invokeSchemaProperties(tool: McpTool): [string, SchemaPropertyDef | undefined][] {
  const schema = tool.snapshots?.[0]?.input_schema as
    { properties?: Record<string, SchemaPropertyDef> } | undefined
  return Object.entries(schema?.properties ?? {})
}

export function DebugInvokeForm({ tool }: { tool: McpTool }) {
  const toast = useToast()
  const [invokeArgs, setInvokeArgs] = useState('{}')
  const [invokeProduct, setInvokeProduct] = useState<'internal' | 'agent' | 'customer_service'>('internal')
  const [submitting, setSubmitting] = useState(false)
  const [invokeResult, setInvokeResult] = useState<TestInvokeResult | null>(null)

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
    <div className="grid gap-3 text-[12.5px]">
      <div>
        <div className="mb-1 text-text-muted">参数 Schema（来自发现快照）</div>
        {invokeSchemaProperties(tool).length === 0 ? <div className="text-text-muted">无参数</div> : (
          <table className="w-full">
            <thead>
              <tr className="text-left text-[11px] text-text-muted">
                <th className="py-1 pr-3 font-normal">参数</th>
                <th className="py-1 pr-3 font-normal">类型</th>
                <th className="py-1 font-normal">说明（上游声明，不可信）</th>
              </tr>
            </thead>
            <tbody>
              {invokeSchemaProperties(tool).map(([pname, pdef]) => (
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
      <div>
        <AssetActionButton tone="primary" disabled={submitting} onClick={() => void runInvoke()}>
          {submitting ? <Loader2 size={14} className="animate-spin" /> : <Plug size={14} />}
          执行测试调用
        </AssetActionButton>
      </div>
      {invokeResult && (
        <div className="rounded border border-border-subtle bg-surface-base p-3">
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
  )
}
