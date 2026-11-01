'use client'

/**
 * /skills — 能力清单、路由状态与 Tool Discovery（决策 #18，B1）
 *
 * 两档 Workbench（照 /releases 改法）：
 *   - 能力与注册（原 B13 页面内容整体保留）：GET /capabilities 声明 ↔
 *     Skill 注册表对账 + MCP 注册情况 + 能力包授权矩阵；
 *   - Tool Discovery（B1）：发现策略表单（PUT /admin/tools/discovery/policy）
 *     + 发现模拟器（POST /admin/tools/discovery/simulate，只读候选解析，
 *     不进运行时路由链路）。
 */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { Play, RefreshCw, Save, Server, Search } from 'lucide-react'
import { clsx } from 'clsx'
import { AssetActionButton, AssetSection, AssetStatCard, AssetState } from '@/components/layout/AssetPageShell'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import EmptyState from '@/components/shared/EmptyState'
import { atLeast } from '@/lib/auth'
import {
  getCapabilities,
  getMcpServers,
  getMcpTools,
  type McpServer,
  type McpTool,
} from '@/api/registry'
import {
  getDiscoveryPolicy, simulateDiscovery, updateDiscoveryPolicy,
  type DiscoveryMode, type DiscoveryPolicyUpsertInput,
  type DiscoveryPolicyView, type DiscoverySimulateResult,
} from '@/api/discovery'
import BundleGrantMatrix from './BundleGrantMatrix'

const SKILLS_TABS = [
  { id: 'capabilities', label: '能力与注册' },
  { id: 'discovery', label: 'Tool Discovery' },
] as const satisfies readonly WorkbenchTab<'capabilities' | 'discovery'>[]

const TAB_IDS = SKILLS_TABS.map((tab) => tab.id) as readonly (
  'capabilities' | 'discovery'
)[]

const DISCOVERY_MODE_OPTIONS: { value: DiscoveryMode; label: string }[] = [
  { value: 'adaptive', label: '自适应（灰区交 LLM 选型）' },
  { value: 'reasoning', label: '推理档（仅 LLM 选型）' },
  { value: 'fast', label: 'Fast Path 优先' },
]

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

// ═══════════════ 第一档：能力与注册（原 B13 内容） ═══════════════

function CapabilitiesTab() {
  const canOpenToolRegistry = atLeast('admin')
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
    <div className="mx-auto w-full max-w-7xl px-6 py-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <p className="text-[12px] text-text-secondary">
          Capability 路由清单、Skill 注册对账与 MCP 注册情况；注册事实源沿用现有 manifest 和 registry。
        </p>
        <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={() => load(true)}>
          刷新
        </AssetActionButton>
      </div>

      {loading ? (
        <AssetState>加载中…</AssetState>
      ) : error ? (
        <AssetState className="text-red-700">{error}</AssetState>
      ) : !caps || caps.capabilities.length === 0 ? (
        <AssetState>
          能力清单为空（manifest 未声明或接口未就绪，只出骨架 + 空态）
        </AssetState>
      ) : (
        <div className="space-y-4">
          {/* 汇总卡 */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            <AssetStatCard label="Capability 总数" value={caps.count} hint="含 routed:false 的内部能力" />
            <AssetStatCard label="参与用户问题路由" value={caps.routed_count} hint="规则关键词 + 向量样例双路召回" />
            <AssetStatCard
              label="Skill 数"
              value={caps.skills.length}
              hint="运行时 Skill 注册表现状"
            />
          </div>

          {/* 能力包与 Agent 授权（Batch 2：写入口，授权立即生效） */}
          <BundleGrantMatrix />

          {/* 能力清单表 */}
          <AssetSection title="能力清单" meta={`${caps.count} 条`} bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[12px]">
                <thead>
                  <tr className="text-[11px] text-text-muted">
                    <th className="px-4 py-2 font-normal">Capability</th>
                    <th className="px-4 py-2 font-normal">Skill</th>
                    <th className="px-4 py-2 font-normal">负责人</th>
                    <th className="px-4 py-2 font-normal">路由状态</th>
                    <th className="px-4 py-2 font-normal">规则关键词</th>
                    <th className="px-4 py-2 font-normal">向量路由样例</th>
                    <th className="px-4 py-2 font-normal">注册</th>
                  </tr>
                </thead>
                <tbody>
                  {caps.capabilities.map((c) => (
                    <tr key={c.name} className="border-t border-border-subtle align-top">
                      <td className="px-4 py-2.5"><code className="text-text-primary">{c.name}</code></td>
                      <td className="px-4 py-2.5 text-text-secondary">{c.skill}</td>
                      <td className="px-4 py-2.5 text-text-secondary">{c.owner}</td>
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
          </AssetSection>

          <AssetSection title="Workflow 目录" meta="Agent 与工作流" bodyClassName="pt-3">
            <p className="text-[12px] leading-relaxed text-text-secondary">
              Workflow 注册描述和 Trace 下钻归入运行监控。运行历史继续通过受授权 Trace 查询，不读取没有资源授权保证的 Workflow run 输出。
            </p>
            <Link href="/agents" className="mt-3 inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] font-medium text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              打开 Agent 与工作流
            </Link>
          </AssetSection>

          {/* MCP Server / Tool 注册情况 */}
          <AssetSection title="MCP Server 与 Tool 注册情况" icon={<Server size={14} />} bodyClassName="pt-3">
            {servers === null ? (
              <div className="py-4 text-center text-[12px] text-text-muted">MCP 信息不可用（接口未就绪），仅展示能力清单</div>
            ) : (
              <>
                <div className="grid gap-2 md:grid-cols-2">
                  {servers.map((s) => (
                    <div key={s.name} className="rounded-lg border border-border-subtle bg-surface-base p-3">
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
                        <tr key={`${t.server}-${t.name}`} className="border-t border-border-subtle">
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
          </AssetSection>
        </div>
      )}
    </div>
  )
}

// ═══════════════ 第二档：Tool Discovery（决策 #18，B1） ═══════════════

const DEFAULT_FORM: DiscoveryPolicyUpsertInput = {
  name: '默认发现策略',
  mode: 'adaptive',
  top_k: 3,
  max_rounds: 2,
  cost_budget_milliunits: 50,
  timeout_ms: 8000,
  prompt_key: 'router.tool_selector',
}

function DiscoveryTab() {
  const [policyView, setPolicyView] = useState<DiscoveryPolicyView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [form, setForm] = useState<DiscoveryPolicyUpsertInput>(DEFAULT_FORM)
  const [saving, setSaving] = useState(false)
  // 模拟器
  const [simQuery, setSimQuery] = useState('')
  const [simDomain, setSimDomain] = useState('')
  const [simTopK, setSimTopK] = useState(3)
  const [simulating, setSimulating] = useState(false)
  const [simError, setSimError] = useState<string | null>(null)
  const [simResult, setSimResult] = useState<DiscoverySimulateResult | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    getDiscoveryPolicy()
      .then((v) => {
        setPolicyView(v)
        const p = v.policy
        setForm({
          name: p.name || DEFAULT_FORM.name,
          mode: p.mode,
          top_k: p.top_k,
          max_rounds: p.max_rounds,
          cost_budget_milliunits: p.cost_budget_milliunits,
          timeout_ms: p.timeout_ms,
          prompt_key: p.prompt_key || DEFAULT_FORM.prompt_key,
        })
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const onSave = () => {
    setSaving(true)
    setNotice(null)
    updateDiscoveryPolicy(form)
      .then((p) => {
        setNotice(`发现策略「${p.name}」已保存并生效（${DISCOVERY_MODE_OPTIONS.find((m) => m.value === p.mode)?.label ?? p.mode}，top_k=${p.top_k}）`)
        load()
      })
      .catch((e: Error) => setNotice(`保存失败：${e.message}`))
      .finally(() => setSaving(false))
  }

  const onSimulate = () => {
    setSimulating(true)
    setSimError(null)
    setSimResult(null)
    simulateDiscovery({
      query: simQuery,
      domain: simDomain.trim() || undefined,
      top_k: simTopK,
    })
      .then(setSimResult)
      .catch((e: Error) => setSimError(e.message))
      .finally(() => setSimulating(false))
  }

  const numberField = (
    label: string, key: 'top_k' | 'max_rounds' | 'cost_budget_milliunits' | 'timeout_ms',
    min: number, max: number, hint: string,
  ) => (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] text-text-muted">{label}</span>
      <input
        type="number"
        data-testid={`discovery-policy-${key}`}
        value={form[key]}
        min={min}
        max={max}
        onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })}
        className="w-32 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
      />
      <span className="text-[10px] text-text-muted">{hint}</span>
    </label>
  )

  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-6">
      <p className="mb-4 text-[12px] leading-relaxed text-text-secondary">
        B1 边界（如实声明）：本页为发现策略与只读候选解析模拟器，
        当前不进线上路由链路——候选 = 域 registry（域未知时 planner 可见全集）→ 产品/角色/范围过滤 → 前 top_k。
      </p>

      {notice && (
        <div data-testid="discovery-notice" className="mb-4 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5 text-[12px] text-text-primary">
          {notice}
        </div>
      )}

      {/* 上半：发现策略 */}
      <AssetSection
        title="发现策略"
        meta={policyView?.policy.active ? '当前生效' : '未配置（代码内默认值）'}
      >
        {loading ? (
          <div className="py-4 text-center text-[13px] text-text-muted">加载中…</div>
        ) : error ? (
          <div className="py-4 text-center text-[13px] text-red-700">{error}</div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-text-muted">策略名</span>
                <input
                  data-testid="discovery-policy-name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-44 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[11px] text-text-muted">发现模式</span>
                <select
                  data-testid="discovery-policy-mode"
                  value={form.mode}
                  onChange={(e) => setForm({ ...form, mode: e.target.value as DiscoveryMode })}
                  className="w-56 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
                >
                  {DISCOVERY_MODE_OPTIONS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </label>
              {numberField('候选数 top_k', 'top_k', 1, 20, '1-20')}
              {numberField('发现轮数', 'max_rounds', 1, 5, '1-5')}
              {numberField('费用预算（毫单位）', 'cost_budget_milliunits', 0, 1000000, '单次发现预算')}
              {numberField('超时（ms）', 'timeout_ms', 1000, 30000, '1000-30000')}
              <button
                type="button"
                data-testid="discovery-policy-save"
                onClick={onSave}
                disabled={saving || !form.name.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Save size={13} /> {saving ? '保存中…' : '保存策略'}
              </button>
            </div>
            <div className="text-[11px] text-text-muted">
              prompt_key：<code>{form.prompt_key}</code>（B1 固定复用 router.tool_selector 提示词版本）
              {policyView && policyView.policies.length > 0 && (
                <> · 本租户共 {policyView.policies.length} 条策略行，生效：{policyView.policy.name || '—'}</>
              )}
            </div>
          </div>
        )}
      </AssetSection>

      {/* 下半：发现模拟器 */}
      <div className="mt-4">
        <AssetSection title="发现模拟器" meta="主助手视角 · 纯读不执行" icon={<Search size={14} />}>
          <div className="flex flex-wrap items-center gap-2">
            <input
              data-testid="discovery-sim-query"
              value={simQuery}
              onChange={(e) => setSimQuery(e.target.value)}
              placeholder="输入用户问题，例如：规划福州 2 天的行程"
              className="min-w-[280px] flex-1 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
            />
            <input
              data-testid="discovery-sim-domain"
              value={simDomain}
              onChange={(e) => setSimDomain(e.target.value)}
              placeholder="业务域（可留空，如 travel / knowledge）"
              className="w-56 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
            />
            <input
              type="number"
              data-testid="discovery-sim-topk"
              value={simTopK}
              min={1}
              max={20}
              onChange={(e) => setSimTopK(Number(e.target.value))}
              className="w-20 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
              title="候选数 top_k"
            />
            <button
              type="button"
              data-testid="discovery-sim-run"
              onClick={onSimulate}
              disabled={simulating || !simQuery.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Play size={13} /> {simulating ? '模拟中…' : '模拟发现'}
            </button>
          </div>

          {simError && <div className="mt-3 text-[12px] text-rose-600">{simError}</div>}

          {simResult === null && !simError && (
            <div className="mt-4">
              <EmptyState
                title="发现模拟结果"
                description="输入问题后模拟候选解析：工具名 / 风险级别 / 来源 / 逐项权限过滤说明"
              />
            </div>
          )}

          {simResult !== null && (
            <div data-testid="discovery-sim-result" className="mt-4 space-y-2">
              <div className="text-[11px] text-text-muted">
                候选来源：{simResult.source === 'domain_registry' ? `域 registry（${simResult.domain}）` : 'planner 可见全集（域未知回退）'}
                · 解析 {simResult.candidate_count} 个 · 权限过滤剔除 {simResult.excluded_count} 个 · 保留 {simResult.candidates.length} 个
              </div>
              {simResult.candidates.length === 0 ? (
                <div className="rounded-lg border border-dashed border-black/10 bg-white p-4 text-center text-[12px] text-text-secondary">
                  当前产品/权限下无保留候选（全部被过滤剔除）
                </div>
              ) : (
                simResult.candidates.map((c) => (
                  <div key={c.name} data-testid="discovery-sim-candidate" className="rounded-lg border border-border-subtle bg-white p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-[12px] font-medium text-text-primary">{c.name}</code>
                      <span className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[10px] text-text-secondary">risk: {c.risk}</span>
                      <span className="rounded-full bg-black/[0.04] px-2 py-0.5 text-[10px] text-text-secondary">来源: {c.source}</span>
                    </div>
                    <div className="mt-1.5 text-[11px] leading-relaxed text-text-muted">{c.permission_note}</div>
                  </div>
                ))
              )}
              {simResult.notes.map((n, i) => (
                <p key={i} className="text-[11px] leading-relaxed text-text-muted">{n}</p>
              ))}
            </div>
          )}
        </AssetSection>
      </div>
    </div>
  )
}

// ═══════════════ 页面壳 ═══════════════

export default function SkillsPage() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'capabilities')
  const canOpenToolRegistry = atLeast('admin')
  return (
    <WorkbenchShell
      title="Skill 与 Capability"
      description="Capability 路由清单、Skill 注册对账、MCP 注册情况与 Tool Discovery（B1）"
      tabs={SKILLS_TABS}
      activeTab={tab}
      onTabChange={selectTab}
      actions={<>
        <Link href="/agents" className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Agent 与工作流</Link>
        {canOpenToolRegistry && <Link href="/tools" className="inline-flex min-h-9 items-center rounded-md border border-border-subtle px-3 text-[12px] text-text-secondary hover:bg-bg-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">Tool 注册表</Link>}
      </>}
    >
      {tab === 'capabilities' ? <CapabilitiesTab /> : <DiscoveryTab />}
    </WorkbenchShell>
  )
}
