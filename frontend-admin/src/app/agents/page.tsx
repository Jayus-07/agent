'use client'

/**
 * /agents — Agent 层节点总览（只读，B13）
 *
 * 数据源：后端 GET /agents（backend/app/api/routes/agents.py）。
 * 展示主图三类节点：编排节点（router/planner/supervisor 等）、
 * Skill 节点（含能力归属）、域图节点。事实源在代码，本页只读不写。
 */
import { useEffect, useState } from 'react'
import { Bot, GitBranch, RefreshCw, Wrench } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/layout/PageHeader'
import { getAgents, type AgentKind, type AgentNode } from '@/api/registry'

const KIND_META: Record<AgentKind, { label: string; icon: typeof Bot; desc: string }> = {
  orchestration: { label: '编排节点', icon: Bot, desc: '主图内置：路由 / 规划 / 调度 / 汇总' },
  skill: { label: 'Skill 节点', icon: Wrench, desc: '各 Skill 包自注册，执行具体能力' },
  domain_graph: { label: '域图节点', icon: GitBranch, desc: '独立子图，自带 reporter 直接到 END' },
}

const KIND_ORDER: AgentKind[] = ['orchestration', 'skill', 'domain_graph']

function AgentCard({ node }: { node: AgentNode }) {
  return (
    <div className="rounded-lg border border-black/5 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-text-primary">{node.label}</span>
        <code className="text-[11px] text-text-muted">{node.name}</code>
      </div>
      {node.route_mode && (
        <div className="mt-1 text-[11px] text-text-muted">route_mode: {node.route_mode}</div>
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
    <div>
      <PageHeader
        title="Agent 节点"
        desc="主图 Agent 层总览（只读）· 事实源在代码，节点由 builder / Skill 包 / 域图自动注册"
      />

      <div className="mb-4">
        <button
          onClick={() => load(true)}
          className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-black/[0.03]"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {loading ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] text-text-muted shadow-card">加载中…</div>
      ) : error ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] shadow-card" style={{ color: '#791F1F' }}>{error}</div>
      ) : !data || data.agents.length === 0 ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] text-text-muted shadow-card">
          未发现任何 Agent 节点（后端注册表为空，接口未就绪只出骨架 + 空态）
        </div>
      ) : (
        <>
          {/* 汇总卡 */}
          <div className="grid grid-cols-3 gap-3">
            {KIND_ORDER.map((kind) => {
              const meta = KIND_META[kind]
              const Icon = meta.icon
              return (
                <div key={kind} className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
                  <div className="flex items-center gap-2 text-[12px] text-text-secondary">
                    <Icon size={14} /> {meta.label}
                  </div>
                  <div className="mt-2 text-2xl font-semibold text-text-primary">{data.summary[kind] ?? 0}</div>
                  <div className="mt-1 text-[11px] text-text-muted">{meta.desc}</div>
                </div>
              )
            })}
          </div>

          {/* 分组列表 */}
          {KIND_ORDER.map((kind) => {
            const nodes = data.agents.filter((a) => a.kind === kind)
            if (nodes.length === 0) return null
            const meta = KIND_META[kind]
            return (
              <section key={kind} className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
                <h2 className={clsx('mb-3 text-[13px] font-medium text-text-primary')}>
                  {meta.label}（{nodes.length}）
                </h2>
                <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
                  {nodes.map((n) => <AgentCard key={`${n.kind}-${n.name}`} node={n} />)}
                </div>
              </section>
            )
          })}
        </>
      )}
    </div>
  )
}
