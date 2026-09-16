'use client'

/**
 * /skills — 能力清单与路由状态（只读，B13）
 *
 * 数据源：
 *   - GET /capabilities：capabilities.yaml 声明 ↔ Skill 注册表对账
 *     （routed / 规则路由关键词 / 向量路由样例 / registered 漂移信号）
 *   - GET /mcp/servers、/mcp/tools（复用既有只读接口）：内置 MCP Server
 *     与工具的注册情况。均为进程内注册，"健康"以「已注册 + 工具清单可
 *     枚举」呈现，不伪造运行时探活。
 */
import { useEffect, useState } from 'react'
import { RefreshCw, Server } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '@/components/layout/PageHeader'
import {
  getCapabilities,
  getMcpServers,
  getMcpTools,
  type McpServer,
  type McpTool,
} from '@/api/registry'

function RoutedBadge({ routed }: { routed: boolean }) {
  return (
    <span className={clsx('whitespace-nowrap rounded-full px-2 py-0.5 text-[10px]',
      routed ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500')}>
      {routed ? '对用户路由' : '内部'}
    </span>
  )
}

function RegisteredBadge({ ok }: { ok: boolean }) {
  return (
    <span className={clsx('whitespace-nowrap rounded-full px-2 py-0.5 text-[10px]',
      ok ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
      {ok ? '已注册' : '未注册!'}
    </span>
  )
}

function KeywordChips({ words, empty }: { words: string[]; empty: string }) {
  if (words.length === 0) return <span className="text-[11px] text-text-muted">{empty}</span>
  return (
    <div className="flex flex-wrap gap-1">
      {words.map((w) => (
        <span key={w} className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[11px] text-text-secondary">{w}</span>
      ))}
    </div>
  )
}

export default function SkillsPage() {
  const [caps, setCaps] = useState<Awaited<ReturnType<typeof getCapabilities>> | null>(null)
  const [servers, setServers] = useState<McpServer[] | null>(null)
  const [tools, setTools] = useState<McpTool[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function load(silent = false) {
    if (!silent) setLoading(true)
    setError('')
    try {
      // MCP 为可选信息：失败只降级对应区块，不阻塞能力清单主区块
      const [capRes, mcpServers, mcpTools] = await Promise.all([
        getCapabilities(),
        getMcpServers().then((r) => r.servers).catch(() => null),
        getMcpTools().then((r) => r.tools).catch(() => null),
      ])
      setCaps(capRes)
      setServers(mcpServers)
      setTools(mcpTools)
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
        title="能力与技能"
        desc="Capability 路由清单 × Skill 注册对账 × MCP 注册情况（只读）· 唯一事实源 capabilities.yaml，改动走代码与配置变更"
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
      ) : !caps || caps.capabilities.length === 0 ? (
        <div className="rounded-xl border border-black/5 bg-white p-10 text-center text-[13px] text-text-muted shadow-card">
          能力清单为空（manifest 未声明或接口未就绪，只出骨架 + 空态）
        </div>
      ) : (
        <>
          {/* 汇总卡 */}
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
              <div className="text-[12px] text-text-secondary">Capability 总数</div>
              <div className="mt-2 text-2xl font-semibold text-text-primary">{caps.count}</div>
              <div className="mt-1 text-[11px] text-text-muted">含 routed:false 的内部能力</div>
            </div>
            <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
              <div className="text-[12px] text-text-secondary">参与用户问题路由</div>
              <div className="mt-2 text-2xl font-semibold text-text-primary">{caps.routed_count}</div>
              <div className="mt-1 text-[11px] text-text-muted">规则关键词 + 向量样例双路召回</div>
            </div>
            <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
              <div className="text-[12px] text-text-secondary">Skill / Workflow</div>
              <div className="mt-2 text-2xl font-semibold text-text-primary">
                {caps.skills.length} / {caps.workflows.length}
              </div>
              <div className="mt-1 text-[11px] text-text-muted">Skill 注册表现状 · manifest 声明的 workflow</div>
            </div>
          </div>

          {/* 能力清单表 */}
          <section className="mt-4 overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
            <h2 className="border-b border-black/5 px-4 py-3 text-[13px] font-medium text-text-primary">
              能力清单（{caps.count}）
            </h2>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[12px]">
                <thead>
                  <tr className="text-[11px] text-text-muted">
                    <th className="px-4 py-2 font-normal">Capability</th>
                    <th className="px-4 py-2 font-normal">Skill</th>
                    <th className="px-4 py-2 font-normal">路由状态</th>
                    <th className="px-4 py-2 font-normal">规则关键词</th>
                    <th className="px-4 py-2 font-normal">向量路由样例</th>
                    <th className="px-4 py-2 font-normal">注册</th>
                  </tr>
                </thead>
                <tbody>
                  {caps.capabilities.map((c) => (
                    <tr key={c.name} className="border-t border-black/5 align-top">
                      <td className="px-4 py-2.5"><code className="text-text-primary">{c.name}</code></td>
                      <td className="px-4 py-2.5 text-text-secondary">{c.skill}</td>
                      <td className="px-4 py-2.5">
                        <RoutedBadge routed={c.routed} />
                        {!c.routed && c.reason && (
                          <div className="mt-1 max-w-[220px] text-[11px] text-text-muted">{c.reason}</div>
                        )}
                      </td>
                      <td className="px-4 py-2.5">
                        <KeywordChips words={c.rule_keywords} empty="—（规则路由回退内置缺省表）" />
                      </td>
                      <td className="px-4 py-2.5 text-text-secondary">
                        {c.examples.length === 0 ? (
                          <span className="text-text-muted">—</span>
                        ) : (
                          <ul className="space-y-0.5">
                            {c.examples.slice(0, 3).map((ex) => (
                              <li key={ex} className="max-w-[280px] truncate" title={ex}>{ex}</li>
                            ))}
                            {c.examples.length > 3 && (
                              <li className="text-[11px] text-text-muted">…共 {c.examples.length} 条</li>
                            )}
                          </ul>
                        )}
                      </td>
                      <td className="px-4 py-2.5"><RegisteredBadge ok={c.registered} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {/* Workflow */}
          <section className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
            <h2 className="mb-3 text-[13px] font-medium text-text-primary">Workflow（{caps.workflows.length}）</h2>
            <div className="grid gap-2 md:grid-cols-2">
              {caps.workflows.map((w) => (
                <div key={w.name} className="rounded-lg border border-black/5 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-[12px] text-text-primary">{w.name}</code>
                    <span className="text-[11px] text-text-muted">{w.examples.length} 条路由样例</span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* MCP Server / Tool 注册情况 */}
          <section className="mt-4 rounded-xl border border-black/5 bg-white p-4 shadow-card">
            <h2 className="mb-3 flex items-center gap-2 text-[13px] font-medium text-text-primary">
              <Server size={14} /> MCP Server 与 Tool 注册情况
            </h2>
            {servers === null ? (
              <div className="py-4 text-center text-[12px] text-text-muted">MCP 信息不可用（接口未就绪），仅展示能力清单</div>
            ) : (
              <>
                <div className="grid gap-2 md:grid-cols-2">
                  {servers.map((s) => (
                    <div key={s.name} className="rounded-lg border border-black/5 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex items-center gap-1.5 text-[13px] font-medium text-text-primary">
                          <span className="h-1.5 w-1.5 rounded-full bg-green-500" title="进程内已注册，工具清单可枚举" />
                          {s.name}
                        </span>
                        <span className="text-[11px] text-text-muted">{s.tool_count} 个工具</span>
                      </div>
                      <div className="mt-1 text-[11px] leading-relaxed text-text-muted">{s.description}</div>
                    </div>
                  ))}
                </div>
                {tools !== null && tools.length > 0 && (
                  <table className="mt-3 w-full text-left text-[12px]">
                    <thead>
                      <tr className="text-[11px] text-text-muted">
                        <th className="py-1.5 font-normal">工具</th>
                        <th className="py-1.5 font-normal">所属 Server</th>
                        <th className="py-1.5 font-normal">说明</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tools.map((t) => (
                        <tr key={`${t.server}-${t.name}`} className="border-t border-black/5">
                          <td className="py-2"><code className="text-text-primary">{t.name}</code></td>
                          <td className="py-2 text-text-secondary">{t.server}</td>
                          <td className="max-w-[420px] truncate py-2 text-text-secondary" title={t.description}>{t.description}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </section>
        </>
      )}
    </div>
  )
}
