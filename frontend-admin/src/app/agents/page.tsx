'use client'

/**
 * /agents — Agent 层节点总览（只读，B13）
 *
 * 数据源：后端 GET /agents（backend/app/api/routes/agents.py）。
 * 展示主图三类节点：编排节点（router/planner/supervisor 等）、
 * Skill 节点（含能力归属）、域图节点（含域归属）。
 * 事实源在代码，本页只读不写：域归属（谁是谁的子流）由接口的
 * domain/domain_label/subflow 字段下发，本页不复写归属关系。
 */
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowRight, Bot, Database, GitBranch, RefreshCw, Sparkles, Wrench } from 'lucide-react'
import { AssetActionButton, AssetPageShell, AssetSection, AssetStatCard, AssetState } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import { getAgents, type AgentKind, type AgentNode } from '@/api/registry'
import { subflowAttribution, orderDomainGraphs } from '@/lib/domain-attribution'

const KIND_META: Record<AgentKind, { label: string; icon: typeof Bot; desc: string }> = {
  orchestration: { label: '编排节点', icon: Bot, desc: '主图内置：路由 / 规划 / 调度 / 汇总' },
  skill: { label: 'Skill 节点', icon: Wrench, desc: '各 Skill 包自注册，执行具体能力' },
  domain_graph: { label: '域图节点', icon: GitBranch, desc: '独立子图，自带 reporter 直接到 END' },
}

const KIND_ORDER: AgentKind[] = ['orchestration', 'skill', 'domain_graph']

const RELATION_STEPS = [
  {
    label: 'Agent：调度与执行节点',
    detail: '决定执行路径，组织子流程和能力调用',
    icon: Bot,
    className: 'bg-blue-50 text-blue-700',
  },
  {
    label: 'Skill：业务能力封装',
    detail: '把一个业务目标封装成可路由、可执行的能力',
    icon: Sparkles,
    className: 'bg-violet-50 text-violet-700',
  },
  {
    label: 'Tool：原子操作',
    detail: '具体调用数据库、外部 API 或 MCP 数据源',
    icon: Database,
    className: 'bg-emerald-50 text-emerald-700',
  },
] as const

function AssetRelationGuide() {
  return (
    <section
      aria-labelledby="agent-skill-tool-relation"
      className="mb-5 rounded-xl border border-blue-100 bg-blue-50/40 p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="agent-skill-tool-relation" className="text-sm font-semibold text-text-primary">AI 资产关系总览</h2>
          <p className="mt-1 text-[12px] text-text-secondary">平台运行时通常沿着 Agent → Skill → Tool 这条链路完成一次能力调用。</p>
        </div>
        <div className="flex items-center gap-3 text-[12px]">
          <Link href="/skills" className="text-accent hover:underline">查看 Skill 能力</Link>
          <Link href="/tools" className="text-accent hover:underline">查看 Tool 治理</Link>
        </div>
      </div>

      <div className="mt-4 grid gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-stretch">
        {RELATION_STEPS.map((step, index) => {
          const Icon = step.icon
          return (
            <div key={step.label} className="contents">
              <div className="rounded-lg border border-blue-100 bg-white p-3">
                <div className="flex items-center gap-2 text-[13px] font-medium text-text-primary">
                  <span className={`rounded-md p-1.5 ${step.className}`}><Icon size={15} /></span>
                  {step.label}
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-text-muted">{step.detail}</p>
              </div>
              {index < RELATION_STEPS.length - 1 && (
                <div className="hidden items-center justify-center text-blue-300 md:flex" aria-hidden="true">
                  <ArrowRight size={16} />
                </div>
              )}
            </div>
          )
        })}
      </div>

      <p className="mt-3 rounded-lg bg-white/70 px-3 py-2 text-[11px] leading-relaxed text-text-secondary">
        关系不是一对一：一个 Agent 可以组织多个 Skill，一个 Skill 可以组合多个 Tool，同一个 Tool 也可以被多个 Skill 复用。
      </p>
    </section>
  )
}

function AgentCard({ node }: { node: AgentNode }) {
  // 归属后缀由后端字段拼装：route_mode 原样透传（内部调度契约，永久不改名），
  // 谁属于谁由 GET /agents 的 domain/domain_label/subflow 下发，前端不在本地存一份。
  const attribution = subflowAttribution(node)
  return (
    <div className="rounded-lg border border-border-subtle bg-surface-base p-3 transition-colors hover:border-accent/30">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-text-primary">{node.label}</span>
        {/* Skill 节点 label 与 name 相同，右侧重复无信息量，仅在两者不同时展示 */}
        {node.name !== node.label && (
          <code className="text-[11px] text-text-muted">{node.name}</code>
        )}
      </div>
      {node.route_mode && (
        <div className="mt-1 text-[11px] text-text-muted">
          route_mode: {node.route_mode}
          {attribution && (
            <span className="ml-1 text-text-secondary">· {attribution}</span>
          )}
        </div>
      )}
      {(node.capabilities?.length ?? 0) > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {node.capabilities!.map((cap) => (
            <span key={cap} className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[11px] text-text-secondary">
              {cap}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function AgentsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof getAgents>> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(silent = false) {
    if (!silent) setLoading(true)
    setError('')
    try {
      setData(await getAgents())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <AssetPageShell
        title="Agent 节点"
        desc="主图 Agent 层总览（只读）· 事实源在代码，节点由 builder / Skill 包 / 域图自动注册"
        actions={
          <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={() => load(true)}>
            刷新
          </AssetActionButton>
        }
      >

      <AssetRelationGuide />

      {loading ? (
        <AssetState>加载中…</AssetState>
      ) : error ? (
        <ErrorState title="Agent 注册表加载失败" message={error} onRetry={() => load()} />
      ) : !data || data.agents.length === 0 ? (
        <AssetState>
          未发现任何 Agent 节点（后端注册表为空，接口未就绪只出骨架 + 空态）
        </AssetState>
      ) : (
        <div className="space-y-4">
          {/* 汇总卡 */}
          <div className="grid grid-cols-3 gap-3">
            {KIND_ORDER.map((kind) => {
              const meta = KIND_META[kind]
              const Icon = meta.icon
              return (
                <AssetStatCard
                  key={kind}
                  label={meta.label}
                  value={data.summary[kind] ?? 0}
                  hint={meta.desc}
                  icon={<Icon size={14} />}
                />
              )
            })}
          </div>

          {/* 分组列表 */}
          {KIND_ORDER.map((kind) => {
            const inKind = data.agents.filter((a) => a.kind === kind)
            // 域图节点按归属排：父域在前、其子流紧随（顺序由后端 domain 字段派生）
            const nodes = kind === 'domain_graph' ? orderDomainGraphs(inKind) : inKind
            if (nodes.length === 0) return null
            const meta = KIND_META[kind]
            return (
              <AssetSection key={kind} title={meta.label} meta={`${nodes.length} 个`} bodyClassName="pt-3">
                <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
                  {nodes.map((n) => <AgentCard key={`${n.kind}-${n.name}`} node={n} />)}
                </div>
              </AssetSection>
            )
          })}
        </div>
      )}
    </AssetPageShell>
  )
}
