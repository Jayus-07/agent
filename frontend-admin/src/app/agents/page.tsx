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
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowRight, Bot, Database, GitBranch, RefreshCw, Sparkles, Wrench } from 'lucide-react'
import { AssetActionButton, AssetPageShell, AssetSection, AssetStatCard, AssetState } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import { getAgents, getWorkflowCatalog, type AgentKind, type AgentNode, type WorkflowCatalogEntry } from '@/api/registry'
import { atLeast } from '@/lib/auth'
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

function WorkflowCard({ workflow }: { workflow: WorkflowCatalogEntry }) {
  const runHref = `/observability/monitoring?tab=traces&workflow=${encodeURIComponent(workflow.name)}`
  return (
    <article className="border-t border-border-subtle px-4 py-3 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-mono text-[13px] font-medium text-text-primary">{workflow.name}</h3>
          {workflow.description && <p className="mt-1 max-w-3xl text-[12px] leading-relaxed text-text-secondary">{workflow.description}</p>}
        </div>
        <Link href={runHref} className="inline-flex min-h-9 shrink-0 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
          查看授权 Trace
        </Link>
      </div>
      {(workflow.objects.length > 0 || workflow.actions.length > 0) && (
        <dl className="mt-2 grid gap-2 text-[11px] md:grid-cols-2">
          {workflow.objects.length > 0 && (
            <div><dt className="text-text-muted">业务对象</dt><dd className="mt-1 flex flex-wrap gap-1">{workflow.objects.map((item) => <span key={item} className="rounded bg-bg-elevated px-2 py-0.5 text-text-secondary">{item}</span>)}</dd></div>
          )}
          {workflow.actions.length > 0 && (
            <div><dt className="text-text-muted">声明动作</dt><dd className="mt-1 flex flex-wrap gap-1">{workflow.actions.map((item) => <span key={item} className="rounded bg-bg-elevated px-2 py-0.5 text-text-secondary">{item}</span>)}</dd></div>
          )}
        </dl>
      )}
    </article>
  )
}

export default function AgentsPage() {
  const [data, setData] = useState<Awaited<ReturnType<typeof getAgents>> | null>(null)
  const [workflows, setWorkflows] = useState<WorkflowCatalogEntry[] | null>(null)
  const [agentError, setAgentError] = useState(false)
  const [workflowError, setWorkflowError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const requestSequence = useRef(0)
  const canReadWorkflowCatalog = atLeast('editor')
  const canOpenToolRegistry = atLeast('admin')

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current
    setRefreshing(true)
    const [agentsResult, workflowsResult] = await Promise.all([
      Promise.allSettled([getAgents()]).then(([result]) => result),
      canReadWorkflowCatalog
        ? Promise.allSettled([getWorkflowCatalog()]).then(([result]) => result)
        : Promise.resolve(null),
    ])
    if (sequence !== requestSequence.current) return

    if (agentsResult.status === 'fulfilled') {
      setData(agentsResult.value)
      setAgentError(false)
    } else {
      setAgentError(true)
    }
    if (workflowsResult?.status === 'fulfilled') {
      setWorkflows(workflowsResult.value.workflows)
      setWorkflowError(false)
    } else if (canReadWorkflowCatalog) {
      setWorkflowError(true)
    } else {
      setWorkflows(null)
      setWorkflowError(false)
    }
    setLoading(false)
    setRefreshing(false)
  }, [canReadWorkflowCatalog])

  useEffect(() => {
    void load()
    return () => { requestSequence.current += 1 }
  }, [load])

  return (
    <AssetPageShell
      title="Agent 与工作流"
      desc="只读展示运行时注册节点与 Workflow 元数据；执行历史通过受授权 Trace 查询。"
      actions={
        <>
          {canReadWorkflowCatalog && <Link href="/skills" className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Skill 与 Capability</Link>}
          {canOpenToolRegistry && <Link href="/tools" className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Tool 注册表</Link>}
          <AssetActionButton icon={<RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />} disabled={refreshing} onClick={() => void load()}>
            刷新
          </AssetActionButton>
        </>
      }
    >
      <AssetRelationGuide />
      <div className="space-y-4">
        <AssetSection title="Agent 节点" meta={data ? `${data.agents.length} 个` : undefined} bodyClassName="p-0">
          {loading && !data ? <AssetState>正在读取 Agent 注册表…</AssetState>
            : agentError && !data ? <ErrorState title="Agent 注册表暂不可用" message="读取失败，请检查服务后重试。" onRetry={() => void load()} />
              : <>
                {agentError && <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">刷新失败，以下保留上次成功读取的数据。</p>}
                {!data || data.agents.length === 0 ? <AssetState>注册表当前没有 Agent 节点。</AssetState>
                  : <div className="p-4">
                  <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
                    {KIND_ORDER.map((kind) => {
                      const meta = KIND_META[kind]
                      const Icon = meta.icon
                      return <AssetStatCard key={kind} label={meta.label} value={data.summary[kind] ?? 0} hint={meta.desc} icon={<Icon size={14} />} />
                    })}
                  </div>
                  <div className="space-y-4">
                    {KIND_ORDER.map((kind) => {
                      const inKind = data.agents.filter((a) => a.kind === kind)
                      const nodes = kind === 'domain_graph' ? orderDomainGraphs(inKind) : inKind
                      if (nodes.length === 0) return null
                      const meta = KIND_META[kind]
                      return <div key={kind}>
                        <h3 className="mb-2 text-[12px] font-medium text-text-secondary">{meta.label}（{nodes.length}）</h3>
                        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">{nodes.map((node) => <AgentCard key={`${node.kind}-${node.name}`} node={node} />)}</div>
                      </div>
                    })}
                  </div>
                </div>}
              </>}
        </AssetSection>

        {canReadWorkflowCatalog && <AssetSection title="Workflow 注册" meta={workflows ? `${workflows.length} 个` : undefined} bodyClassName="p-0">
          {loading && !workflows ? <AssetState>正在读取 Workflow 注册表…</AssetState>
            : workflowError && !workflows ? <ErrorState title="Workflow 注册表暂不可用" message="读取失败，请检查服务后重试。" onRetry={() => void load()} />
              : <>
                {workflowError && <p role="alert" className="border-b border-amber-200 bg-amber-50 px-4 py-2 text-[12px] text-amber-900">刷新失败，以下保留上次成功读取的数据。</p>}
                {!workflows || workflows.length === 0 ? <AssetState>注册表当前没有 Workflow。</AssetState>
                  : <div>
                    {workflows.map((workflow) => <WorkflowCard key={workflow.name} workflow={workflow} />)}
                    <p className="border-t border-border-subtle bg-bg-elevated/40 px-4 py-3 text-[11px] leading-relaxed text-text-muted">
                      当前接口提供描述、业务对象、动作和路由样例；契约版本、实际步骤及运行历史没有由此目录接口返回。本页不补造这些字段。Trace 下钻会继续执行现有资源授权。
                    </p>
                  </div>}
              </>}
        </AssetSection>}
      </div>
    </AssetPageShell>
  )
}
