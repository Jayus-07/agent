'use client'

/**
 * /consistency — 资产一致性中心（治理 M6，2026-09-30）
 *
 * 数据源：GET /api/consistency/report（七段对账矩阵，全部实时派生）。
 * 「运行 Check」= 强制重新请求实时计算。本页只读不写，数字与代码同源
 * （G2：禁止人工维护数字）。
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, RefreshCw, ShieldCheck, XCircle } from 'lucide-react'
import PageHeader from '@/components/layout/PageHeader'
import ErrorState from '@/components/shared/ErrorState'
import { getConsistencyReport, type ConsistencyReport } from '@/api/governance'

const SECTION_LABEL: Record<string, string> = {
  agents: 'Agent 节点',
  skills: 'Skill',
  capabilities: 'Capability',
  tools: 'Tool',
  workflows: 'Workflow',
  mcp: 'MCP',
  tool_contract_lock: '契约 lock',
}

const COUNT_LABEL: Record<string, string> = {
  skill_nodes: 'Skill 图节点',
  skills: 'Skill',
  domain_graphs: '域图',
  declared_skills: 'manifest 声明 Skill',
  capabilities: 'Capability',
  routed: '可路由',
  internal: '内部（routed:false）',
  loaded: '已加载',
  declared: '已声明',
  duplicates: '重复定义',
  not_loaded: '未加载',
  phantom: '幽灵注册',
  workflows: 'Workflow',
  servers: 'Server',
  tools: 'Tool',
  tool_count: 'Tool 数',
  breaking: 'BREAKING',
  degraded: 'DEGRADED',
  compatible: 'COMPATIBLE',
}

function SectionCard({
  name, status, counts, issues,
}: {
  name: string
  status: 'PASS' | 'FAIL'
  counts: Record<string, number>
  issues: string[]
}) {
  const ok = status === 'PASS'
  return (
    <div className={`rounded-lg border p-4 ${ok ? 'border-black/5 bg-white' : 'border-red-200 bg-red-50/40'}`}>
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-text-primary">
          {SECTION_LABEL[name] ?? name}
        </span>
        {ok ? (
          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-emerald-600">
            <CheckCircle2 size={14} /> PASS
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-red-600">
            <XCircle size={14} /> FAIL
          </span>
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {Object.entries(counts).map(([k, v]) => (
          <span key={k} className="text-[12px] text-text-secondary">
            {COUNT_LABEL[k] ?? k}
            <span className="ml-1 font-mono font-semibold">{v}</span>
          </span>
        ))}
      </div>
      {issues.length > 0 && (
        <ul className="mt-2 space-y-1">
          {issues.map((issue, i) => (
            <li key={i} className="flex items-start gap-1.5 text-[12px] text-red-700">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span className="font-mono">{issue}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function ConsistencyPage() {
  const [report, setReport] = useState<ConsistencyReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = () => {
    setLoading(true)
    setError(null)
    getConsistencyReport()
      .then(setReport)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="mb-6 flex items-start justify-between">
        <PageHeader
          title="资产一致性中心"
          desc={`七段对账矩阵 · 全部实时派生 · ${report?.generated_at ?? ''}`}
        />
        <button
          onClick={load}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-black/10 px-3 py-1.5 text-[13px] text-text-secondary hover:bg-black/[0.03]"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 运行 Check
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={load} />}

      {!error && report && (
        <>
          <div
            className={`mb-5 flex items-center gap-2 rounded-lg p-4 text-[14px] font-medium ${
              report.overall === 'PASS'
                ? 'bg-emerald-50 text-emerald-700'
                : 'bg-red-50 text-red-700'
            }`}
          >
            {report.overall === 'PASS' ? (
              <>
                <CheckCircle2 size={18} /> 全部一致（PASS）——代码与管理端数字同源
              </>
            ) : (
              <>
                <XCircle size={18} /> 存在漂移：{report.failed_sections.map((s) => SECTION_LABEL[s] ?? s).join('、')}
              </>
            )}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            {report.sections.map((s) => (
              <SectionCard key={s.name} {...s} />
            ))}
          </div>

          <p className="mt-4 text-[11px] leading-relaxed text-text-muted">
            对账事实源：builder 节点表 / skills registry / capabilities.yaml / tool_registry（AST 扫描）/
            workflows register_all / MCP manager / tool_contracts.lock.json——全部运行时派生，禁止手抄（G2）。
            契约 lock 漂移时先运行 python -m backend.scripts.gen_tool_contract_lock 重新生成并随变更提交。
          </p>
        </>
      )}
    </div>
  )
}
