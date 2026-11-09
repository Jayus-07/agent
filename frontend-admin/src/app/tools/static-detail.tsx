/**
 * /tools 静态契约的只读详情与契约清单行（自 page.tsx 拆出，纯移动）。
 * registry 与 contract 两个 subTab 共用 StaticToolDetail。
 */
import { useEffect, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  getToolErrors,
  type ToolContractEntry,
  type ToolErrorRecord,
} from '@/api/governance'
import { DataSourceBadge } from './badges'
import { ERROR_CLASS_LABEL, fmtCount } from './meta'

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
export function StaticToolDetail({ tool }: { tool: ToolContractEntry }) {
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
                      <span className="text-text-muted">来源链路</span>
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

/** contract subTab 的静态契约行（含运行统计列，点击展开只读详情）。 */
export function ToolRow({ tool }: { tool: ToolContractEntry }) {
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
