'use client'

/**
 * /tools — Tool 治理（V2 治理工作台：动态工具的审核/授权/调试统一入口）
 *
 * 三个 subTab（状态进 URL query）：
 * - registry 工具目录与操作：静态契约 lock（getToolInventory）+ 动态 MCP/HTTP
 *   工具（getMcpTools）统一三来源目录；动态工具行打开详情抽屉（概览/Schema/
 *   执行策略/调试/历史），审核、业务域、授权与测试调用都在抽屉内完成。
 * - quality 运行质量与失败：运行统计、强制失败测试（runToolFailureProbe，
 *   只写模拟 Trace）、失败 Tool 与七分类分布。
 * - contract 契约与变更历史：契约清单（数据源/额度/参数）+ 变更历史。
 *
 * 事实源：代码 + tool_contracts.lock.json（静态）/ ai.mcp_tools（动态，DB）。
 * 清单筛选（搜索/数据源/仅失败）与变更历史分类筛选进 URL querystring，
 * 分类过滤走服务端（changes 本地只有最近一页，前端过滤会漏更早记录）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Loader2, Plug,
  RefreshCw, Search, ShieldCheck,
} from 'lucide-react'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DetailDrawer from '@/components/layout/DetailDrawer'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import StatMetricBar from '@/components/layout/StatMetricBar'
import { AssetActionButton, AssetSection, AssetStatCard } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import Skeleton from '@/components/shared/Skeleton'
import { useToast } from '@/components/shared/Toast'
import {
  getToolContractChanges,
  getToolErrors,
  getToolInventory,
  getToolStats,
  runToolFailureProbe,
  type ToolContractChange,
  type ToolContractEntry,
  type ToolErrorRecord,
  type ToolFailureClass,
  type ToolInventory,
  type ToolStats,
} from '@/api/governance'
import {
  disableMcpTool, getMcpToolDetail, getMcpTools, grantMcpTool, reviewMcpTool,
  testInvokeMcpTool,
  type McpTool, type TestInvokeResult,
} from '@/api/mcp'
import { ApiError } from '@/api/client'

const TOOL_PAGE_TABS = [
  { id: 'registry', label: '工具目录与操作' },
  { id: 'quality', label: '运行质量与失败' },
  { id: 'contract', label: '契约与变更历史' },
] as const satisfies readonly WorkbenchTab<'registry' | 'quality' | 'contract'>[]

const TOOL_PAGE_TAB_IDS = TOOL_PAGE_TABS.map((tab) => tab.id) as readonly (
  | 'registry' | 'quality' | 'contract')[]

const ERROR_CLASS_LABEL: Record<string, string> = {
  timeout: '超时',
  network_error: '网络',
  permission_denied: '权限',
  validation_error: '校验',
  business_error: '业务',
  contract_error: '契约',
  provider_error: '供应商',
  unknown: '未知',
}

const CHANGE_KIND_LABEL: Record<string, string> = {
  tool_removed: '删除 Tool',
  tool_added: '新增 Tool',
  param_removed: '删除参数',
  param_added: '新增参数',
  param_type_changed: '参数类型变更',
  required_changed: '必填性变更',
  default_changed: '默认值变更',
  output_type_changed: '输出类型变更',
  capability_binding_changed: 'Capability 归属变更',
  description_changed: '描述变更',
}

const CLASS_META: Record<string, { label: string; cls: string }> = {
  BREAKING: { label: 'BREAKING', cls: 'bg-red-100 text-red-700' },
  DEGRADED: { label: 'DEGRADED', cls: 'bg-amber-100 text-amber-700' },
  COMPATIBLE: { label: 'COMPATIBLE', cls: 'bg-emerald-100 text-emerald-700' },
  INIT: { label: 'INIT', cls: 'bg-slate-100 text-slate-600' },
}

const DATA_SOURCE_META: Record<string, { label: string; cls: string }> = {
  mcp: { label: 'MCP', cls: 'bg-violet-100 text-violet-700' },
  rest: { label: '外部', cls: 'bg-sky-100 text-sky-700' },
}

/** 数据源徽章：MCP 带「上游·开关」tooltip；rest 带 provider；internal 不显示（避免全表噪音）。 */
function DataSourceBadge({ ds }: { ds: NonNullable<ToolContractEntry['data_source']> }) {
  if (ds.type === 'internal') return null
  const meta = DATA_SOURCE_META[ds.type]
  if (!meta) return null
  const tip =
    ds.type === 'mcp'
      ? `外部 MCP server 数据源：${ds.provider ?? ''} · 上游工具 ${ds.upstream_tool ?? '—'}（开关 ${ds.switch_env ?? '—'}，关闭时该 Tool 明确报未配置）`
      : `外部网络 API：${ds.provider ?? ''}`
  return (
    <span className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap ${meta.cls}`} title={tip}>
      {meta.label}·{ds.provider}
    </span>
  )
}

/** 动态工具来源徽章：MCP（Streamable HTTP 发现）/ HTTP API（手工登记），附连接名。 */
function DynamicSourceBadge({ tool }: { tool: McpTool }) {
  const isHttp = tool.conn_transport === 'http_api'
  return (
    <span
      className={`rounded px-1 py-0.5 text-[10px] font-medium whitespace-nowrap align-middle ${
        isHttp ? 'bg-sky-100 text-sky-700' : 'bg-violet-100 text-violet-700'
      }`}
      title={isHttp
        ? `HTTP API 工具：${tool.invocation?.method ?? ''} ${tool.invocation?.path ?? ''}`
        : 'MCP 工具（Streamable HTTP 发现）'}
    >
      {isHttp ? 'HTTP API' : 'MCP'}
      {tool.connection_name ? `·${tool.connection_name}` : ''}
    </span>
  )
}

const QUOTA_PERIOD_LABEL: Record<string, string> = {
  day: '按日',
  period: '按期',
  qps: '速率',
}

// 额度运行时三态+计数态的诚实口径：unlimited ≠ 已用 0（未配预算时不计数）
const QUOTA_STATUS_META: Record<string, { label: string; cls: string }> = {
  unlimited: { label: '未设预算（不限）', cls: 'bg-slate-100 text-slate-600' },
  ok: { label: '正常', cls: 'bg-emerald-100 text-emerald-700' },
  exhausted: { label: '已达预算（软停）', cls: 'bg-red-100 text-red-700' },
  tracked: { label: '计数中', cls: 'bg-sky-100 text-sky-700' },
  untracked: { label: '读数不可用', cls: 'bg-amber-100 text-amber-700' },
}

const FAILURE_PROBE_OPTIONS: { value: ToolFailureClass; label: string }[] = [
  { value: 'timeout', label: '超时' },
  { value: 'network_error', label: '网络不可用' },
  { value: 'permission_denied', label: '权限拒绝' },
  { value: 'validation_error', label: '参数校验' },
  { value: 'business_error', label: '业务失败' },
  { value: 'contract_error', label: '契约降级' },
  { value: 'provider_error', label: '供应商限流' },
]

/** 动态工具审核/状态徽章（StatusChip 语义映射，与连接中心同口径） */
const MCP_STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
  pending: { label: '待审核', tone: 'warn' },
  approved: { label: '已生效', tone: 'success' },
  rejected: { label: '已拒绝', tone: 'danger' },
  disabled: { label: '已停用', tone: 'neutral' },
  retired: { label: '上游已消失', tone: 'neutral' },
}

/** 业务域（决策 #15 硬隔离）：审核时登记 governance.domain，授权时服务端按禁行域拦截。 */
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

function domainLabel(domain?: string): string {
  const value = (domain ?? '').trim()
  return value || '未分类'
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

const fmtCount = (n?: number) => (n ?? 0).toLocaleString('zh-CN')

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return e instanceof Error ? e.message : '请求失败'
}

/** 契约参数表（lock args_schema）：参数 / 类型 / 必填 / 默认值。 */
function ToolArgsTable({ args }: { args: NonNullable<ToolContractEntry['args_schema']> }) {
  const params = Object.entries(args)
  if (params.length === 0) return <div className="text-[12px] text-text-muted">无参数</div>
  return (
    <table className="w-full text-[12px]">
      <thead>
        <tr className="text-left text-[11px] text-text-muted">
          <th className="py-1 pr-3 font-normal">参数</th>
          <th className="py-1 pr-3 font-normal">类型</th>
          <th className="py-1 pr-3 font-normal">必填</th>
          <th className="py-1 font-normal">默认值</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-black/[0.04]">
        {params.map(([name, spec]) => {
          const s = spec.schema ?? {}
          const type = typeof s.type === 'string'
            ? s.type
            : Array.isArray(s.anyOf)
              ? (s.anyOf as { type?: string }[]).map((v) => v.type ?? '?').join(' | ')
              : '—'
          const enumVals = Array.isArray(s.enum) ? (s.enum as unknown[]).map(String).join(' | ') : ''
          return (
            <tr key={name}>
              <td className="py-1 pr-3 font-mono text-[11px] text-text-primary">{name}</td>
              <td className="py-1 pr-3 text-text-secondary">
                {type}
                {enumVals && <span className="ml-1 text-text-muted">（{enumVals}）</span>}
              </td>
              <td className="py-1 pr-3">
                {spec.required ? <span className="text-red-600">必填</span> : <span className="text-text-muted">可选</span>}
              </td>
              <td className="py-1 font-mono text-[11px] text-text-muted">
                {spec.default !== '__unset__' ? JSON.stringify(spec.default) : '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

/** 数据源与额度卡：声明（labels.py 经 lock 派生）+ 运行时读数（quota_runtime）。 */
function DataSourceQuotaSection({ tool }: { tool: ToolContractEntry }) {
  const ds = tool.data_source
  const q = ds?.quota
  const rt = tool.quota_runtime
  if (!ds) {
    return <div className="text-[12px] text-text-muted">数据源未登记（labels.py TOOL_DATA_SOURCES）</div>
  }
  const period = q?.period ? (QUOTA_PERIOD_LABEL[q.period] ?? q.period) : null
  return (
    <div className="space-y-1.5 text-[12px] leading-relaxed">
      <div className="flex flex-wrap items-center gap-2">
        {ds.type === 'internal' ? (
          <span className="text-text-secondary">平台内部数据源（无外部额度）</span>
        ) : (
          <>
            <span className="text-text-secondary">数据源：{ds.provider}</span>
            {ds.upstream_tool && <span className="font-mono text-[11px] text-text-muted">上游工具 {ds.upstream_tool}</span>}
            {ds.switch_env && <span className="font-mono text-[11px] text-text-muted">开关 {ds.switch_env}</span>}
          </>
        )}
      </div>
      {q && (
        <div className="flex flex-wrap items-center gap-2">
          {period && <span className="text-text-secondary">额度周期：{period}</span>}
          {q.limit != null && <span className="text-text-secondary">声明额度 {fmtCount(q.limit)} 次</span>}
          {q.limit_env && <span className="font-mono text-[11px] text-text-muted">预算 env {q.limit_env}</span>}
          {/* 纯被动声明（无计数器，如高德）不显示读数状态——note 已承载口径 */}
          {rt?.status && (q.usage_provider || q.usage_counter) && (
            <span className={`rounded px-1.5 py-0.5 text-[11px] ${QUOTA_STATUS_META[rt.status]?.cls ?? 'bg-slate-100 text-slate-600'}`}>
              {QUOTA_STATUS_META[rt.status]?.label ?? rt.status}
            </span>
          )}
          {/* 已设预算才显示用量：unlimited 态 current_usage 不计数，显示 0 会误导 */}
          {rt && (rt.status === 'ok' || rt.status === 'exhausted') && rt.usage != null && rt.budget != null && (
            <span className="font-mono text-text-primary">今日 {fmtCount(rt.usage)} / {fmtCount(rt.budget)}</span>
          )}
          {rt && rt.status === 'tracked' && rt.usage != null && (
            <span className="font-mono text-text-primary">本期已用 {fmtCount(rt.usage)} 次</span>
          )}
        </div>
      )}
      {q?.note && <div className="text-[11px] text-text-muted">{q.note}</div>}
      {rt?.status === 'untracked' && (q?.usage_provider || q?.usage_counter) && (
        <div className="text-[11px] text-amber-700">用量读数暂不可用（计数源不可达或未启用；额度声明仍有效）</div>
      )}
    </div>
  )
}

/** 静态 Tool 只读详情（行展开内容）：数据源与额度 + 契约参数 + 失败错误明细。
 *  registry 与 contract 两个 subTab 共用。 */
function StaticToolDetail({ tool }: { tool: ToolContractEntry }) {
  const rt = tool.runtime
  const failed = (rt?.failures ?? 0) > 0
  const [errors, setErrors] = useState<ToolErrorRecord[] | null>(null)
  const [loadingErr, setLoadingErr] = useState(false)

  // 失败行展开时懒加载错误来源明细
  useEffect(() => {
    if (!failed || errors !== null) return
    setLoadingErr(true)
    getToolErrors(tool.name, 10)
      .then((r) => setErrors(r.errors))
      .catch(() => setErrors([]))
      .finally(() => setLoadingErr(false))
  }, [failed, errors, tool.name])

  return (
    <div className="space-y-4">
      <section>
        <div className="mb-1.5 text-[12px] font-medium text-text-secondary">数据源与额度</div>
        <DataSourceQuotaSection tool={tool} />
      </section>
      <section>
        <div className="mb-1.5 text-[12px] font-medium text-text-secondary">
          契约参数（tool_contracts.lock.json）
        </div>
        {tool.args_schema ? (
          <ToolArgsTable args={tool.args_schema} />
        ) : (
          <div className="text-[12px] text-text-muted">—</div>
        )}
      </section>
      {failed && (
        <section>
          <div className="mb-1.5 text-[12px] font-medium text-text-secondary">
            错误来源明细（最近 10 条，Skill → Capability → Tool，来自执行 Trace）
          </div>
          {loadingErr && <div className="text-[12px] text-text-muted">加载中…</div>}
          {!loadingErr && errors && errors.length === 0 && (
            <div className="text-[12px] text-text-muted">
              最近 Trace 中没有该 Tool 的失败记录（可能发生在更早时间窗，或由探针/测试流量产生）
            </div>
          )}
          {!loadingErr && errors && errors.length > 0 && (
            <ul className="space-y-1.5">
              {errors.map((e, i) => {
                const errorKey = e.error_class || e.error_code
                const errorLabel = errorKey
                  ? (ERROR_CLASS_LABEL[errorKey] ?? errorKey)
                  : '失败'
                return (
                  <li key={`${e.trace_id}-${i}`} className="rounded-md border border-red-100 bg-surface-base px-3 py-2 text-[12px] leading-relaxed">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-[11px] text-text-muted">{e.ts.replace('T', ' ').slice(0, 19)}</span>
                      <span className="text-text-muted">·</span>
                      <span className="font-medium text-text-secondary">来源链路</span>
                      <span className="text-text-muted">Skill</span>
                      <code className="text-[11px] text-text-primary">{e.skill || '未知'}</code>
                      <span className="text-text-muted">→ Capability</span>
                      <code className="text-[11px] text-text-primary">{e.capability || '未知'}</code>
                      <span className="text-text-muted">→ Tool</span>
                      <code className="text-[11px] text-text-primary">{e.tool || tool.name}</code>
                    </div>
                    <div className="mt-1 text-text-secondary">
                      <span className="font-medium">用户问题：</span>{e.question || '未记录'}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="font-medium text-red-700">错误分类：{errorLabel}</span>
                      {e.source_error_code && <code className="text-text-muted">({e.source_error_code})</code>}
                      {e.error && <span className="text-text-secondary">{e.error}</span>}
                      <span className="text-text-muted">耗时 {e.latency_ms} ms</span>
                      <a href={`/observability/traces/${e.trace_id}`} className="text-accent hover:underline">
                        查看完整 Trace
                      </a>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}

/** 清单筛选：与 DataSourceBadge 同口径（无徽章 = internal） */
const DS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: '全部数据源' },
  { value: 'mcp', label: 'MCP' },
  { value: 'rest', label: '外部 API' },
  { value: 'internal', label: '平台内部' },
]

const CLS_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: '全部分类' },
  { value: 'BREAKING', label: 'BREAKING' },
  { value: 'DEGRADED', label: 'DEGRADED' },
  { value: 'COMPATIBLE', label: 'COMPATIBLE' },
  { value: 'INIT', label: 'INIT' },
]

type ChangeCls = '' | 'BREAKING' | 'DEGRADED' | 'COMPATIBLE' | 'INIT'

/** URL 读入的分类值只接受白名单，脏值回落全部分类 */
function parseCls(value: string | null): ChangeCls {
  const v = value ?? ''
  return (CLS_FILTER_OPTIONS.some((o) => o.value === v) ? v : '') as ChangeCls
}

/** contract subTab 的静态契约行（含运行统计列，点击展开只读详情）。 */
function ToolRow({ tool }: { tool: ToolContractEntry }) {
  const rt = tool.runtime
  const failed = (rt?.failures ?? 0) > 0
  const [expanded, setExpanded] = useState(false)

  const chevronCls = failed ? 'text-red-500' : 'text-text-muted/50'

  return (
    <>
      <tr className={`${failed ? 'bg-red-50/50' : ''} cursor-pointer hover:bg-slate-50/60`} onClick={() => setExpanded(!expanded)}>
        <td className="px-3 py-2">
          <div className="flex items-center gap-1">
            {expanded ? <ChevronDown size={13} className={chevronCls} /> : <ChevronRight size={13} className={chevronCls} />}
            <span className="text-[13px] font-medium text-text-primary">
              {tool.display_name || <span className="text-text-muted/60">（未登记中文名）</span>}
            </span>
            {tool.data_source && <DataSourceBadge ds={tool.data_source} />}
          </div>
          <div className="font-mono text-[11px] text-text-muted">{tool.name}</div>
        </td>
        <td className="px-3 py-2 text-[12px] text-text-muted">{tool.module.replace(/^backend\//, '')}</td>
        <td className="px-3 py-2 text-[12px]">
          {tool.capabilities.length > 0 ? (
            <span className="font-mono text-[11px] text-text-secondary">
              {tool.capabilities.join(', ')}
            </span>
          ) : (
            <span className="text-[11px] text-text-muted/60">—（不可静态派生）</span>
          )}
        </td>
        <td className="px-3 py-2 text-[12px]">
          <span className="font-mono text-[11px] text-text-secondary" title={tool.content_hash}>
            {tool.content_hash.slice(0, 8)}
          </span>
        </td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">{fmtCount(rt?.calls)}</td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">
          {rt?.success_rate != null ? `${(rt.success_rate * 100).toFixed(1)}%` : '—'}
        </td>
        <td className="px-3 py-2 text-right font-mono text-[12px]">
          {failed ? (
            <span className="text-red-600">{fmtCount(rt?.failures)}</span>
          ) : (
            <span className="text-emerald-600">0</span>
          )}
        </td>
        <td className="px-3 py-2 text-[11px] text-text-muted">
          {rt?.error_classes && Object.keys(rt.error_classes).length > 0
            ? Object.entries(rt.error_classes)
                .sort((a, b) => b[1] - a[1])
                .map(([cls, n]) => `${ERROR_CLASS_LABEL[cls] ?? cls}:${fmtCount(n)}`)
                .join(' · ')
            : '—'}
        </td>
        {failed && (
          <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
            <a
              href={`/observability/monitoring?tab=traces&has_tool=${encodeURIComponent(tool.name)}`}
              className="text-[11px] text-accent hover:underline"
            >
              Trace 下钻 →
            </a>
          </td>
        )}
      </tr>
      {expanded && (
        <tr className={failed ? 'bg-red-50/30' : 'bg-slate-50/60'}>
          <td colSpan={failed ? 9 : 8} className="px-10 py-3">
            <StaticToolDetail tool={tool} />
          </td>
        </tr>
      )}
    </>
  )
}

/** 动态工具详情抽屉的「执行策略」：审核表单（含业务域）+ 授权产品开关。 */
function ReviewPolicyForm({ tool, onDone }: { tool: McpTool; onDone: () => Promise<void> }) {
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

/** 动态工具详情抽屉的「调试」：只读测试调用（走完整治理链）。 */
function DebugInvokeForm({ tool }: { tool: McpTool }) {
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

type SchemaPropertyDef = { type?: string; description?: string }

function invokeSchemaProperties(tool: McpTool): [string, SchemaPropertyDef | undefined][] {
  const schema = tool.snapshots?.[0]?.input_schema as
    { properties?: Record<string, SchemaPropertyDef> } | undefined
  return Object.entries(schema?.properties ?? {})
}

/** 动态工具详情抽屉的「历史」：HTTP API 工具的 invocation 版本对比。 */
function ToolHistorySection({ tool }: { tool: McpTool }) {
  if (tool.conn_transport !== 'http_api' || (tool.snapshots?.length ?? 0) <= 1) {
    return (
      <div className="text-[12px] text-text-muted">
        暂无可对比的调用定义版本；HTTP API 工具在「编辑调用」保存新版本后，这里展示最近 3 版对比。
      </div>
    )
  }
  return (
    <div className="space-y-2 text-[12px]">
      <div className="text-text-muted">调用定义版本对比（最近 3 版；新版漂移需重审）</div>
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
  )
}

// ── 动态工具详情缓存（迁移自原 /mcp 页）──────────────────────

let _detailCache: Record<number, McpTool> = {}
/** 失效详情缓存：重新发现 / 审核 / 停用 / 授权变更后必须调用，
 *  否则抽屉会继续展示旧 Schema 与旧治理配置。 */
function invalidateMcpToolDetailCache(ids?: number[]) {
  if (!ids) {
    _detailCache = {}
    return
  }
  _detailCache = Object.fromEntries(
    Object.entries(_detailCache).filter(([k]) => !ids.includes(Number(k))))
}

async function fetchMcpToolDetailFresh(id: number): Promise<McpTool> {
  invalidateMcpToolDetailCache([id])
  return getMcpToolDetail(id)
}

/** 工具详情抽屉 Tab（AI 语义为置灰壳位 Tab） */
const TOOL_DRAWER_TABS = [
  { id: 'overview', label: '概览' },
  { id: 'schema', label: 'Schema' },
  { id: 'policy', label: '执行策略' },
  { id: 'debug', label: '调试' },
  { id: 'history', label: '历史' },
  {
    id: 'ai', label: 'AI 语义', disabled: true,
    disabledHint: 'AI 语义契约建议属下一批（能力建设中）',
  },
]

/** 动态工具详情抽屉：概览 / Schema / 执行策略（审核+域+授权）/ 调试 / 历史。 */
function ToolDrawer({ toolId, onClose, onMutated }: {
  toolId: number
  onClose: () => void
  /** 工具变更（审核/停用/授权）后通知页面刷新列表与统计 */
  onMutated: () => Promise<void>
}) {
  const toast = useToast()
  const [detail, setDetail] = useState<McpTool | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('overview')
  // 提交后用 revision 重挂表单（以服务端返回的最新事实源重新初始化）
  const [revision, setRevision] = useState(0)
  const [disabling, setDisabling] = useState(false)

  const load = useCallback(async (fresh = false) => {
    setLoading(true)
    setError('')
    try {
      const data = fresh
        ? await fetchMcpToolDetailFresh(toolId)
        : await getMcpToolDetail(toolId)
      setDetail(data)
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [toolId])

  useEffect(() => {
    setTab('overview')
    void load()
  }, [load])

  const handleMutated = async (toolIds: number[]) => {
    invalidateMcpToolDetailCache(toolIds)
    await onMutated()
    await load(true)
    setRevision((r) => r + 1)
  }

  const disableTool = async (tool: McpTool) => {
    setDisabling(true)
    try {
      await disableMcpTool(tool.id)
      toast.success('工具已停用；写调用立即被拦截，读调用最迟 15 秒退出候选')
      await handleMutated([tool.id])
    } catch (e) {
      toast.error(`停用失败: ${errMessage(e)}`)
    } finally {
      setDisabling(false)
    }
  }

  return (
    <DetailDrawer
      open
      width="lg"
      onClose={onClose}
      title={detail?.capability ?? `工具 #${toolId}`}
      subtitle={detail && (
        <span className="flex flex-wrap items-center gap-1.5">
          <DynamicSourceBadge tool={detail} />
          <span className="font-mono">{detail.tool_name}</span>
          <span className="font-mono text-[11px]">
            hash {detail.content_hash.slice(0, 8)} · v{detail.last_seen_version}
          </span>
        </span>
      )}
      tabs={TOOL_DRAWER_TABS}
      activeTab={tab}
      onTabChange={setTab}
      actions={detail?.status === 'approved' ? (
        <AssetActionButton disabled={disabling} onClick={() => void disableTool(detail)}>
          {disabling ? <Loader2 size={13} className="animate-spin" /> : null}
          停用工具
        </AssetActionButton>
      ) : undefined}
    >
      {loading && !detail ? (
        <Skeleton rows={6} cols={2} />
      ) : error && !detail ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : detail ? (
        <div key={revision} className="space-y-4 text-[12.5px]">
          {tab === 'overview' && (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-text-muted">状态：</span>
                <StatusChip tone={MCP_STATUS_META[detail.status]?.tone ?? 'neutral'}>
                  {MCP_STATUS_META[detail.status]?.label ?? detail.status}
                </StatusChip>
                {detail.schema_changed && (
                  <StatusChip tone="danger">契约漂移（需重新审核）</StatusChip>
                )}
                {detail.retired_reason && (
                  <StatusChip tone="neutral">上游已消失</StatusChip>
                )}
              </div>
              <div className="grid gap-x-4 gap-y-2 md:grid-cols-2">
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">连接</span>
                  <span>{detail.connection_name ?? detail.connection_id}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">上游工具</span>
                  <span className="font-mono text-[11px]">{detail.tool_name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">业务域</span>
                  <span>{domainLabel(
                    typeof detail.governance?.domain === 'string' ? detail.governance.domain : '')}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-14 shrink-0 text-text-muted">授权产品</span>
                  <span>
                    {(detail.grants ?? []).length > 0
                      ? (detail.grants ?? []).map((g) => g.product_key).join('、')
                      : '未授权'}
                  </span>
                </div>
              </div>
              <div>
                <div className="mb-1 text-text-muted">描述</div>
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 leading-relaxed">
                  {detail.description || <span className="text-text-muted">（无描述）</span>}
                </div>
              </div>
              {detail.reviewed_at && (
                <div className="text-[11px] text-text-muted">
                  最近审核 {new Date(detail.reviewed_at).toLocaleString()}（by {detail.reviewed_by || '—'}）
                </div>
              )}
            </div>
          )}

          {tab === 'schema' && (
            <div className="space-y-3">
              {detail.schema_changed && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                  上游 Schema 已变化：当前 hash {detail.content_hash.slice(0, 8)} ≠
                  已审核 hash {detail.approved_hash.slice(0, 8)}，需重新审核后才会回到运行时。
                </div>
              )}
              <div>
                <div className="mb-1 text-text-muted">输入参数（发现快照）</div>
                {invokeSchemaProperties(detail).length === 0 ? (
                  <div className="text-text-muted">无参数</div>
                ) : (
                  <table className="w-full">
                    <thead>
                      <tr className="text-left text-[11px] text-text-muted">
                        <th className="py-1 pr-3 font-normal">参数</th>
                        <th className="py-1 pr-3 font-normal">类型</th>
                        <th className="py-1 font-normal">说明（上游声明，不可信）</th>
                      </tr>
                    </thead>
                    <tbody>
                      {invokeSchemaProperties(detail).map(([pname, pdef]) => (
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
              <div>
                <div className="mb-1 text-text-muted">快照版本（漂移对比：发现版本 / 内容指纹）</div>
                {(detail.snapshots?.length ?? 0) === 0 ? (
                  <div className="text-text-muted">暂无快照记录</div>
                ) : (
                  <ul className="space-y-1.5">
                    {(detail.snapshots ?? []).slice(0, 5).map((snap, idx) => (
                      <li key={`${snap.discovery_version}-${idx}`}
                        className={`rounded border px-2.5 py-1.5 font-mono text-[11px] ${
                          idx === 0 ? 'border-accent/50 bg-accent/5' : 'border-border-subtle'
                        }`}>
                        v{snap.discovery_version} · hash {snap.content_hash.slice(0, 8)}
                        <span className="ml-2 font-sans text-text-muted">
                          {new Date(snap.discovered_at).toLocaleString()}
                        </span>
                        {idx === 0 && (
                          <span className="ml-2 rounded bg-accent/10 px-1 font-sans text-[10px] text-accent">当前</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}

          {tab === 'policy' && (
            <ReviewPolicyForm tool={detail} onDone={() => handleMutated([detail.id])} />
          )}

          {tab === 'debug' && <DebugInvokeForm tool={detail} />}

          {tab === 'history' && <ToolHistorySection tool={detail} />}
        </div>
      ) : null}
    </DetailDrawer>
  )
}

export default function ToolsPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { tab, selectTab } = useWorkbenchTab(TOOL_PAGE_TAB_IDS, 'registry')
  const [inventory, setInventory] = useState<ToolInventory | null>(null)
  const [stats, setStats] = useState<ToolStats | null>(null)
  const [changes, setChanges] = useState<ToolContractChange[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [probeTool, setProbeTool] = useState('')
  const [probeClass, setProbeClass] = useState<ToolFailureClass>('timeout')
  const [probeLoading, setProbeLoading] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  const [probeResult, setProbeResult] = useState<{ trace_id: string; status: string } | null>(null)
  // 清单筛选（q/ds/failed）与变更历史分类（cls）进 URL，复用 TracesPanel 的 querystring 模式
  const [query, setQuery] = useState(searchParams.get('q') ?? '')
  const [dsFilter, setDsFilter] = useState(searchParams.get('ds') ?? 'all')
  const [failedOnly, setFailedOnly] = useState(searchParams.get('failed') === '1')
  const [clsFilter, setClsFilter] = useState<ChangeCls>(parseCls(searchParams.get('cls')))
  // 外部 MCP 动态工具（数据库事实源，非契约 lock；连接管理在 /mcp 连接中心）
  const [mcpTools, setMcpTools] = useState<McpTool[]>([])
  const [mcpError, setMcpError] = useState<string | null>(null)
  // 动态工具详情抽屉
  const [drawerToolId, setDrawerToolId] = useState<number | null>(null)
  // registry 目录静态行的展开（只读详情）
  const [expandedStatic, setExpandedStatic] = useState<string | null>(null)
  // clsFilter 首载由 load 覆盖，仅后续变化时重新拉取
  const clsLoadedOnce = useRef(false)

  const loadChanges = (classification: ChangeCls) => {
    // 分类筛选走服务端过滤：本地只存最近 20 条，前端过滤会漏更早的 BREAKING
    getToolContractChanges({ classification: classification || undefined })
      .then((ch) => setChanges(ch.changes))
      .catch(() => setChanges([]))
  }

  // clsFilter 的 ref 视图：load 保持稳定引用（仅挂载与探针后调用），
  // 变更历史分类切换由下方专用 effect 处理（与原实现一致，避免双拉取）
  const clsFilterRef = useRef(clsFilter)
  clsFilterRef.current = clsFilter

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getToolInventory(), getToolStats()])
      .then(([inv, st]) => {
        setInventory(inv)
        setStats(st)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
    loadChanges(clsFilterRef.current)
    getMcpTools()
      .then((tools) => setMcpTools(tools))
      .catch((e: Error) => setMcpError(e.message))
  }, [])

  useEffect(load, [load])

  // 分类切换重新拉取（首载已由 load 覆盖）
  useEffect(() => {
    if (!clsLoadedOnce.current) {
      clsLoadedOnce.current = true
      return
    }
    loadChanges(clsFilter)
  }, [clsFilter])

  // 浏览器后退/外链带参时回填筛选状态（同值 set 不触发渲染，无循环）
  useEffect(() => {
    const params = new URLSearchParams(searchParams.toString())
    const next = {
      q: params.get('q') ?? '',
      ds: params.get('ds') ?? 'all',
      failed: params.get('failed') === '1',
    }
    setQuery((c) => (c === next.q ? c : next.q))
    setDsFilter((c) => (c === next.ds ? c : next.ds))
    setFailedOnly((c) => (c === next.failed ? c : next.failed))
    setClsFilter((c) => {
      const parsed = parseCls(params.get('cls'))
      return c === parsed ? c : parsed
    })
  }, [searchParams])

  const syncFilterUrl = (patch: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString())
    for (const [k, v] of Object.entries(patch)) {
      if (v) params.set(k, v)
      else params.delete(k)
    }
    router.replace(`?${params.toString()}`, { scroll: false })
  }

  // 契约清单 × 运行统计合并（runtime 已在后端合并，这里只排序展示）
  const tools = useMemo(() => inventory?.tools ?? [], [inventory])

  const filteredTools = useMemo(() => {
    const kw = query.trim().toLowerCase()
    return tools.filter((t) => {
      if (failedOnly && !((t.runtime?.failures ?? 0) > 0)) return false
      if (dsFilter !== 'all' && (t.data_source?.type ?? 'internal') !== dsFilter) return false
      if (!kw) return true
      return [t.name, t.display_name, t.module, ...t.capabilities]
        .join('\n')
        .toLowerCase()
        .includes(kw)
    })
  }, [tools, query, dsFilter, failedOnly])

  // registry 统一目录：静态契约行 + 动态工具行共用搜索词
  const filteredMcpTools = useMemo(() => {
    const kw = query.trim().toLowerCase()
    if (!kw) return mcpTools
    return mcpTools.filter((t) =>
      [t.capability, t.tool_name, t.connection_name ?? '', t.description]
        .join('\n')
        .toLowerCase()
        .includes(kw))
  }, [mcpTools, query])

  const pendingMcpCount = mcpTools.filter((t) => t.status === 'pending').length
  const driftMcpCount = mcpTools.filter((t) => t.schema_changed).length
  const approvedMcpCount = mcpTools.filter((t) => t.status === 'approved').length

  const runProbe = async () => {
    const tool = probeTool || tools[0]?.name || ''
    if (!tool) {
      setProbeError('当前没有可用于探测的 Tool 契约')
      return
    }
    setProbeLoading(true)
    setProbeError(null)
    setProbeResult(null)
    try {
      const result = await runToolFailureProbe(tool, probeClass)
      setProbeResult({ trace_id: result.trace_id, status: result.status })
      load()
    } catch (e) {
      setProbeError(e instanceof Error ? e.message : '失败探针执行失败')
    } finally {
      setProbeLoading(false)
    }
  }

  const headerActions = (
    <>
      <Link
        href="/skills"
        className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Skill 与 Capability
      </Link>
      <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={load}>
        刷新
      </AssetActionButton>
    </>
  )

  return (
    <WorkbenchShell
      title="Tool 治理"
      description={`契约快照 tool_contracts.lock.json（${inventory?.lock_git_sha ?? '…'}）× 运行统计（进程内 Prometheus 直读）× 动态工具（MCP 发现 + HTTP API 登记）`}
      tabs={TOOL_PAGE_TABS}
      activeTab={tab}
      onTabChange={selectTab}
      actions={headerActions}
    >
      <div className="mx-auto w-full max-w-7xl space-y-4 px-4 py-5 sm:px-6">
        {tab === 'registry' && (
          <>
            <StatMetricBar items={[
              {
                label: '工具总数',
                value: tools.length + mcpTools.length,
                hint: `静态契约 ${tools.length} + 动态 ${mcpTools.length}`,
              },
              {
                label: '运行时可用',
                value: (stats?.totals.tools_seen ?? 0) + approvedMcpCount,
                hint: `静态观测 ${stats?.totals.tools_seen ?? '—'} + 动态已生效 ${approvedMcpCount}`,
              },
              {
                label: '待审核', value: pendingMcpCount,
                tone: pendingMcpCount > 0 ? 'warn' : 'default',
                hint: pendingMcpCount > 0 ? '动态工具等待审核' : undefined,
              },
              {
                label: '契约漂移', value: driftMcpCount,
                tone: driftMcpCount > 0 ? 'danger' : 'default',
                hint: driftMcpCount > 0 ? '上游 Schema 已变化，需重审' : undefined,
              },
            ]} />

            {error && <ErrorState message={error} onRetry={load} />}

            {inventory?.lock_error && (
              <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
              </div>
            )}

            {mcpError && (
              <div className="flex flex-wrap items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 动态工具目录加载失败：{mcpError}
                <AssetActionButton onClick={load}>重试</AssetActionButton>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input
                  aria-label="搜索工具"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    syncFilterUrl({ q: e.target.value.trim() || null })
                  }}
                  placeholder="搜索名称 / Capability / 连接"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary"
                />
              </div>
              <span className="text-[11px] text-text-muted">
                {filteredTools.length + filteredMcpTools.length} / {tools.length + mcpTools.length} 个
              </span>
            </div>

            <AssetSection
              title="工具目录（静态契约 + 动态工具）"
              meta={`静态 ${filteredTools.length} · 动态 ${filteredMcpTools.length}`}
              bodyClassName="p-0"
            >
              {loading && tools.length === 0 ? (
                <div className="p-4"><Skeleton rows={6} cols={5} /></div>
              ) : filteredTools.length + filteredMcpTools.length === 0 ? (
                <EmptyState
                  title={tools.length + mcpTools.length > 0 ? '匹配筛选的工具' : '工具'}
                  description="调整搜索词；动态工具需先在连接中心完成发现与登记"
                />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12.5px]">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated/60 text-left text-[11px] text-text-muted">
                        <th className="px-4 py-2 font-normal">工具 / 用途</th>
                        <th className="px-2 py-2 font-normal">来源</th>
                        <th className="px-2 py-2 font-normal">业务域</th>
                        <th className="px-2 py-2 font-normal">风险</th>
                        <th className="px-2 py-2 font-normal">审核 / 状态</th>
                        <th className="px-4 py-2 text-right font-normal">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredTools.map((t) => {
                        const expanded = expandedStatic === t.name
                        return (
                          <FragmentRow
                            key={t.name}
                            expanded={expanded}
                            onToggle={() => setExpandedStatic(expanded ? null : t.name)}
                            name={t.name}
                            displayName={t.display_name}
                            module={t.module}
                            source={
                              t.data_source ? <DataSourceBadge ds={t.data_source} /> : (
                                <span className="rounded px-1 py-0.5 text-[10px] font-medium text-text-muted" title="平台内部 Tool（契约 lock）">
                                  Python
                                </span>
                              )
                            }
                            domain="—"
                            risk="—"
                            status={<StatusChip tone="neutral">契约锁定</StatusChip>}
                            action={
                              <button type="button" className="inline-flex items-center text-[11px] text-text-muted hover:text-text-primary"
                                aria-expanded={expanded}>
                                {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                                详情
                              </button>
                            }
                          >
                            <StaticToolDetail tool={t} />
                          </FragmentRow>
                        )
                      })}
                      {filteredMcpTools.map((t) => {
                        const meta = MCP_STATUS_META[t.status] ?? { label: t.status, tone: 'neutral' as StatusTone }
                        return (
                          <tr key={`mcp-${t.id}`} className="border-b border-border-subtle hover:bg-slate-50/60">
                            <td className="px-4 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <span className="text-[13px] font-medium text-text-primary">{t.capability}</span>
                                {t.schema_changed && (
                                  <span className="rounded bg-red-100 px-1 text-[10px] text-red-700" title="上游 Schema 已变化，需重新审核">漂移</span>
                                )}
                              </div>
                              <div className="font-mono text-[11px] text-text-muted">
                                {t.tool_name}
                                {t.description && <span className="ml-2 font-sans text-text-muted">{t.description.slice(0, 60)}{t.description.length > 60 ? '…' : ''}</span>}
                              </div>
                            </td>
                            <td className="px-2 py-2"><DynamicSourceBadge tool={t} /></td>
                            <td className="px-2 py-2 text-[12px]">{domainLabel(
                              typeof t.governance?.domain === 'string' ? t.governance.domain : '')}</td>
                            <td className="px-2 py-2 text-[12px]">{t.governance?.risk_level ?? '—'}</td>
                            <td className="px-2 py-2">
                              <div className="flex flex-wrap items-center gap-1.5">
                                <StatusChip tone={meta.tone}>{meta.label}</StatusChip>
                              </div>
                            </td>
                            <td className="px-4 py-2 text-right">
                              <AssetActionButton onClick={() => setDrawerToolId(t.id)}>详情</AssetActionButton>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </AssetSection>

            <p className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <CheckCircle2 size={12} />
              静态行（Python）来自契约 lock，展开查看数据源/额度/契约参数；动态行（MCP / HTTP API）来自连接发现与登记，「详情」打开抽屉完成审核、执行策略、授权与测试调用；连接与凭据在「连接中心」管理
            </p>
          </>
        )}

        {tab === 'quality' && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <AssetStatCard label="调用总数" value={fmtCount(stats?.totals.calls)} hint="进程内 Prometheus 累计" />
              <AssetStatCard
                label="成功率"
                value={stats?.totals.success_rate != null ? `${(stats.totals.success_rate * 100).toFixed(2)}%` : '—'}
                tone={stats?.totals.success_rate != null && stats.totals.success_rate < 0.99 ? 'warn' : 'default'}
                hint={stats?.totals.success_rate == null ? '暂无调用数据' : undefined}
              />
              <AssetStatCard
                label="失败次数"
                value={fmtCount(stats?.totals.failures)}
                tone={(stats?.totals.failures ?? 0) > 0 ? 'warn' : 'default'}
                hint={stats && stats.totals.failures > 0 ? '失败归属见下方「失败 Tool」区块' : '暂无失败'}
              />
              <AssetStatCard
                label="运行时观测到的 Tool"
                value={String(stats?.totals.tools_seen ?? '—')}
                hint="只计生产流量（探针/测试不计入）"
              />
            </div>

            {error && <ErrorState message={error} onRetry={load} />}

            <AssetSection title="强制失败测试" meta="仅开发/测试环境" icon={<AlertTriangle size={15} />} bodyClassName="pt-3">
              <div className="space-y-2.5">
                <div className="text-[12px] leading-relaxed text-text-secondary">
                  只生成模拟 ToolResult、指标和 Trace，不调用真实 Tool 或外部服务；用于稳定验收不同错误分类的展示与下钻。
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <select
                    aria-label="探测 Tool"
                    value={probeTool || tools[0]?.name || ''}
                    onChange={(e) => setProbeTool(e.target.value)}
                    className="min-w-[240px] rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                  >
                    {tools.length === 0 && <option value="">暂无 Tool 契约</option>}
                    {tools.map((tool) => <option key={tool.name} value={tool.name}>{tool.display_name || tool.name}</option>)}
                  </select>
                  <select
                    aria-label="探测错误分类"
                    value={probeClass}
                    onChange={(e) => setProbeClass(e.target.value as ToolFailureClass)}
                    className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                  >
                    {FAILURE_PROBE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                  <button
                    type="button"
                    onClick={runProbe}
                    disabled={probeLoading || tools.length === 0}
                    className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-[12px] font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {probeLoading && <RefreshCw size={13} className="animate-spin" />}
                    {probeLoading ? '生成中…' : '生成失败 Trace'}
                  </button>
                </div>
                {probeError && <div className="text-[12px] text-red-700">{probeError}</div>}
                {probeResult && (
                  <div className="flex flex-wrap items-center gap-2 text-[12px] text-emerald-700">
                    <span>已生成模拟失败：{ERROR_CLASS_LABEL[probeClass]}（{probeResult.status}）</span>
                    <a href={`/observability/traces/${probeResult.trace_id}`} className="text-accent hover:underline">
                      查看 Trace：{probeResult.trace_id.slice(0, 12)} →
                    </a>
                  </div>
                )}
              </div>
            </AssetSection>

            {stats && stats.top_failed_tools.length > 0 && (
              <AssetSection title="失败 Tool" meta="含未登记契约名" icon={<AlertTriangle size={15} />} bodyClassName="pt-3">
                <ul className="space-y-1.5">
                  {stats.top_failed_tools.map((f) => {
                    const inLock = (inventory?.tools ?? []).some((t) => t.name === f.tool)
                    return (
                      <li key={f.tool} className="flex flex-wrap items-center gap-2 text-[12px]">
                        <span className="font-mono text-text-primary">{f.tool}</span>
                        {!inLock && (
                          <span
                            className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700"
                            title="该名字不在契约 lock 清单中：多为 capability 名（历史调用点记账）或测试流量，不会出现在契约清单行"
                          >
                            未登记契约名
                          </span>
                        )}
                        <span className="font-mono text-red-600">失败 {fmtCount(f.failures)}</span>
                        <span className="text-text-muted">/ 调用 {fmtCount(f.calls)}</span>
                        {Object.keys(f.error_classes ?? {}).length > 0 && (
                          <span className="text-text-muted">
                            {Object.entries(f.error_classes)
                              .sort((a, b) => b[1] - a[1])
                              .map(([cls, n]) => `${ERROR_CLASS_LABEL[cls] ?? cls}:${fmtCount(n)}`)
                              .join(' · ')}
                          </span>
                        )}
                        <a
                          href={`/observability/monitoring?tab=traces&has_tool=${encodeURIComponent(f.tool)}`}
                          className="text-[11px] text-accent hover:underline"
                        >
                          Trace 下钻 →
                        </a>
                      </li>
                    )
                  })}
                </ul>
              </AssetSection>
            )}

            {stats && stats.top_error_classes.length > 0 && (
              <AssetSection title="失败原因分布" meta="统一七分类口径" icon={<ShieldCheck size={15} />} bodyClassName="pt-3">
                <div className="flex flex-wrap gap-2">
                  {stats.top_error_classes.map((c) => (
                    <span key={c.error_class} className="rounded-full bg-slate-100 px-2.5 py-1 text-[12px] text-text-secondary">
                      {ERROR_CLASS_LABEL[c.error_class] ?? c.error_class}
                      <span className="ml-1.5 font-mono font-semibold">{c.count}</span>
                    </span>
                  ))}
                </div>
              </AssetSection>
            )}
          </>
        )}

        {tab === 'contract' && (
          <>
            {inventory?.lock_error && (
              <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[13px] text-amber-800">
                <AlertTriangle size={16} /> 契约 lock 读取失败：{inventory.lock_error}
              </div>
            )}

            {error && <ErrorState message={error} onRetry={load} />}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input
                  aria-label="搜索工具"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value)
                    syncFilterUrl({ q: e.target.value.trim() || null })
                  }}
                  placeholder="搜索名称 / 模块 / Capability"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary"
                />
              </div>
              <select
                aria-label="数据源筛选"
                value={dsFilter}
                onChange={(e) => {
                  setDsFilter(e.target.value)
                  syncFilterUrl({ ds: e.target.value !== 'all' ? e.target.value : null })
                }}
                className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
              >
                {DS_FILTER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              <label className="flex min-h-9 items-center gap-1.5 text-[12px] text-text-secondary">
                <input
                  type="checkbox"
                  checked={failedOnly}
                  onChange={(e) => {
                    setFailedOnly(e.target.checked)
                    syncFilterUrl({ failed: e.target.checked ? '1' : null })
                  }}
                />
                仅看失败
              </label>
              <span className="text-[11px] text-text-muted">
                {filteredTools.length} / {tools.length} 个
              </span>
            </div>

            <AssetSection title="Tool 契约清单" meta={`${filteredTools.length}/${tools.length} 个`} bodyClassName="p-0">
              {loading && tools.length === 0 ? (
                <div className="p-4"><Skeleton rows={6} cols={5} /></div>
              ) : (
                <>
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border-subtle bg-bg-elevated/60 text-left text-[11px] uppercase tracking-wide text-text-muted">
                        <th className="px-3 py-2">工具</th>
                        <th className="px-3 py-2">模块</th>
                        <th className="px-3 py-2">Capability 归属</th>
                        <th className="px-3 py-2">契约指纹</th>
                        <th className="px-3 py-2 text-right">调用</th>
                        <th className="px-3 py-2 text-right">成功率</th>
                        <th className="px-3 py-2 text-right">失败</th>
                        <th className="px-3 py-2">错误类</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-black/[0.04]">
                      {filteredTools.map((t) => (
                        <ToolRow key={t.name} tool={t} />
                      ))}
                    </tbody>
                  </table>
                  {filteredTools.length === 0 && !loading && (
                    <EmptyState
                      title={tools.length > 0 ? '没有匹配筛选的 Tool' : '暂无 Tool 契约数据'}
                      description={tools.length > 0 ? '调整搜索词 / 数据源 / 失败筛选' : '请先运行 gen_tool_contract_lock 生成 lock'}
                    />
                  )}
                </>
              )}
            </AssetSection>

            <p className="flex items-center gap-1.5 text-[11px] text-text-muted">
              <CheckCircle2 size={12} />
              中文名在 backend/tools/labels.py 登记、随契约 lock 派生（G2 禁手抄）；红色失败行点击可展开错误来源明细；「不可静态派生」= 该 Skill 的 Tool 分发逻辑无法从 _tool_fn 主链求值（如 SQL/Competitor）；数据源徽章：MCP=外部 MCP server 数据源（tooltip 含上游工具与开关）、外部=外部网络 API，无徽章 = 平台内部数据源
            </p>

            <AssetSection title="契约变更历史" meta="生成器检测到变更时自动落库，最新在前" bodyClassName="p-0">
              <div className="border-b border-border-subtle px-4 py-2.5">
                <select
                  aria-label="变更分类筛选"
                  value={clsFilter}
                  onChange={(e) => {
                    // select 选项本身来自白名单，as 收窄安全
                    setClsFilter(e.target.value as ChangeCls)
                    syncFilterUrl({ cls: e.target.value || null })
                  }}
                  className="rounded-md border border-border-subtle bg-surface-base px-2.5 py-1.5 text-[12px] text-text-primary"
                >
                  {CLS_FILTER_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
              {changes.length > 0 ? (
                <ul className="divide-y divide-black/[0.04]">
                  {changes.map((ch) => {
                    const meta = CLASS_META[ch.classification] ?? CLASS_META.INIT
                    return (
                      <li key={ch.id} className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 font-mono text-[11px] font-semibold ${meta.cls}`}>
                            {meta.label}
                          </span>
                          <span className="font-mono text-[11px] text-text-muted">
                            {ch.created_at.replace('T', ' ').slice(0, 19)}
                          </span>
                          <span className="font-mono text-[11px] text-text-secondary">git:{ch.git_sha}</span>
                          <span className="text-[11px] text-text-muted">{ch.tool_count} Tool</span>
                          <span className="text-[11px] text-text-muted/70">via {ch.detected_by}</span>
                        </div>
                        {ch.changed_tools.length > 0 && (
                          <ul className="mt-1.5 space-y-0.5">
                            {ch.changed_tools.map((t) => (
                              <li key={t.tool} className="text-[12px] text-text-secondary">
                                <span className="font-mono text-text-primary">{t.tool}</span>
                                {t.changes && t.changes.length > 0 && (
                                  <span className="ml-2 text-[11px] text-text-muted">
                                    {t.changes
                                      .map((c) => CHANGE_KIND_LABEL[c.kind] ?? c.kind + (c.param ? `:${c.param}` : ''))
                                      .join(' · ')}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <div className="px-4 py-6 text-center text-[12px] text-text-muted">
                  该分类下暂无变更记录（服务端按分类过滤，最近 20 条范围内无匹配）
                </div>
              )}
            </AssetSection>
          </>
        )}
      </div>

      {drawerToolId != null && (
        <ToolDrawer
          toolId={drawerToolId}
          onClose={() => setDrawerToolId(null)}
          onMutated={async () => {
            load()
          }}
        />
      )}
    </WorkbenchShell>
  )
}

/** registry 静态行：行 + 可展开只读详情（数据源/契约参数/错误明细）。 */
function FragmentRow({ expanded, onToggle, name, displayName, module, source, domain, risk, status, action, children }: {
  expanded: boolean
  onToggle: () => void
  name: string
  displayName: string
  module: string
  source: ReactNode
  domain: string
  risk: string
  status: ReactNode
  action: ReactNode
  children: ReactNode
}) {
  return (
    <>
      <tr className="cursor-pointer border-b border-border-subtle hover:bg-slate-50/60" onClick={onToggle}>
        <td className="px-4 py-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[13px] font-medium text-text-primary">
              {displayName || <span className="text-text-muted/60">（未登记中文名）</span>}
            </span>
          </div>
          <div className="font-mono text-[11px] text-text-muted" title={module}>
            {name}
            <span className="ml-2 font-sans text-text-muted/70">{module.replace(/^backend\//, '')}</span>
          </div>
        </td>
        <td className="px-2 py-2">{source}</td>
        <td className="px-2 py-2 text-[12px] text-text-muted">{domain}</td>
        <td className="px-2 py-2 text-[12px] text-text-muted">{risk}</td>
        <td className="px-2 py-2">{status}</td>
        <td className="px-4 py-2 text-right" onClick={(e) => e.stopPropagation()}>
          <span onClick={onToggle}>{action}</span>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50/60">
          <td colSpan={6} className="px-10 py-3">{children}</td>
        </tr>
      )}
    </>
  )
}
