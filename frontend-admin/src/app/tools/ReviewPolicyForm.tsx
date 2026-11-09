/**
 * /tools 动态工具详情抽屉的「执行策略」：审核表单（含业务域）+ 授权产品开关。
 * （自 page.tsx 拆出，纯移动。）
 */
import { useState } from 'react'
import { CheckCircle2, Loader2, ShieldCheck } from 'lucide-react'
import { AssetActionButton } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import { grantMcpTool, reviewMcpTool, type McpTool } from '@/api/mcp'
import { errMessage } from '@/api/client'

const DOMAIN_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '未分类（不设限）' },
  { value: 'general', label: 'general 通用' },
  { value: 'cs', label: 'cs 客服' },
  { value: 'selection', label: 'selection 选品' },
  { value: 'travel', label: 'travel 旅游' },
  { value: 'data', label: 'data 数据' },
  { value: 'knowledge', label: 'knowledge 知识' },
  { value: 'business', label: 'business 业务' },
  { value: 'report', label: 'report 报告' },
  { value: 'communication', label: 'communication 沟通' },
  { value: 'mcp', label: 'mcp 外部 MCP' },
]

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

export function ReviewPolicyForm({ tool, onDone }: { tool: McpTool; onDone: () => Promise<void> }) {
  const toast = useToast()
  const [description, setDescription] = useState(tool.description)
  const [operation, setOperation] = useState(tool.governance?.operation ?? 'read')
  const [risk, setRisk] = useState(tool.governance?.risk_level ?? 'R1')
  const [domain, setDomain] = useState(
    typeof tool.governance?.domain === 'string' ? tool.governance.domain : '')
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
  const [busyProduct, setBusyProduct] = useState('')
  const grants = tool.grants ?? []

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
          ...(domain ? { domain } : {}),
          planner_example: plannerExampleParsed,
        } : {},
        examples: action === 'approve'
          ? examplesText.split('\n').map((s) => s.trim()).filter(Boolean) : [],
        notes,
      })
      toast.success(action === 'approve' ? '审核通过，工具已进入运行时' : '已拒绝')
      await onDone()
    } catch (e) {
      toast.error(`审核失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const toggleGrant = async (product: 'agent' | 'customer_service') => {
    const active = grants.some((g) => g.product_key === product)
    setBusyProduct(product)
    try {
      await grantMcpTool(tool.id, product, active)
      toast.success(active ? `已撤销 ${product} 授权` : `已授权给 ${product}`)
      await onDone()
    } catch (e) {
      toast.error(`授权操作失败: ${errMessage(e)}`)
    } finally {
      setBusyProduct('')
    }
  }

  return (
    <div className="space-y-4 text-[12.5px]">
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
          <span className="text-text-muted">业务域（决策 #15：目标产品禁行域在授权时被服务端拒绝）</span>
          <select value={domain} onChange={(e) => setDomain(e.target.value)}
            className="rounded border border-border-subtle bg-surface-base px-2 py-1.5">
            {DOMAIN_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
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
        <div className="flex flex-wrap items-center gap-2 md:col-span-2">
          <AssetActionButton tone="primary" disabled={submitting} onClick={() => void submitReview('approve')}>
            {submitting ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
            审核通过并生效
          </AssetActionButton>
          <AssetActionButton disabled={submitting} onClick={() => void submitReview('reject')}>
            拒绝
          </AssetActionButton>
          <span className="text-text-muted">审核通过后还需授权给产品入口，Agent 才能调用。</span>
        </div>
      </div>

      <div className="rounded-lg border border-border-subtle bg-surface-base p-3">
        <div className="mb-1 text-[12px] font-medium text-text-primary">授权产品入口</div>
        <div className="mb-2 text-[11px] text-text-muted">
          仅已生效工具可授权；写调用出网前强一致复核授权，停用/撤权立即拦截。
        </div>
        <div className="flex gap-2">
          {(['agent', 'customer_service'] as const).map((product) => {
            const active = grants.some((g) => g.product_key === product)
            const busy = busyProduct === product
            return (
              <AssetActionButton key={product} disabled={busy}
                onClick={() => void toggleGrant(product)}>
                {busy ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
                {product === 'agent' ? '主助手' : '客服'}{active ? '（已授权）' : ''}
              </AssetActionButton>
            )
          })}
        </div>
      </div>
    </div>
  )
}
