'use client'

/**
 * /mcp — 连接中心（ADR-0011 外部工具接入：MCP Streamable HTTP 自动发现 +
 * HTTP API 手工登记；动态工具的审核、治理与授权统一在 /tools Tool 治理）。
 *
 * 四个页级 Tab：全部连接 / MCP 服务 / HTTP API（来源过滤）/ 凭据管理。
 * 连接详情抽屉：概览 / 已发现工具 / 配置 / 运行健康（壳位）/ 版本历史（壳位）。
 *
 * 结构：本文件只保留页面数据编排与 tab 装配；徽章（badges）、连接表单
 * （ConnectionForm）、HTTP 工具表单（HttpToolForm）、连接卡片（ConnectionCard）、
 * 健康区（ConnectionHealthSection）、凭据表（CredentialTable）、版本壳位
 * （VersionHistoryStub）各自独立文件。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  AlertTriangle, CheckCircle2, CircleSlash, Download, KeyRound, Loader2, Plus,
  Plug, RefreshCw, Search, Trash2,
} from 'lucide-react'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DetailDrawer from '@/components/layout/DetailDrawer'
import StatusChip, { type StatusTone } from '@/components/layout/StatusChip'
import StatMetricBar from '@/components/layout/StatMetricBar'
import { AssetActionButton, AssetSection } from '@/components/layout/AssetPageShell'
import EmptyState from '@/components/shared/EmptyState'
import ErrorState from '@/components/shared/ErrorState'
import Skeleton from '@/components/shared/Skeleton'
import { useToast } from '@/components/shared/Toast'
import {
  createMcpConnection, deleteMcpConnection, discoverMcpTools, exportMcpConfiguration,
  getMcpConnections, getMcpHealthOverview, getMcpOverview, getMcpTools, registerHttpTool,
  setMcpConnectionEnabled, testMcpConnection, updateHttpToolInvocation,
  updateMcpConnection,
  type ConnectionInput, type ConnectionTestResult, type DiscoverResult,
  type HttpToolInvocation, type McpConnection, type McpOverview,
  type McpToolSummary,
} from '@/api/mcp'
import { errMessage } from '@/api/client'
import { CONN_STATUS_META, TOOL_STATUS_META, TransportBadge } from './badges'
import { ConnectionForm } from './ConnectionForm'
import { HttpToolForm } from './HttpToolForm'
import { ConnectionCard } from './ConnectionCard'
import { ConnectionHealthSection } from './ConnectionHealthSection'
import { CredentialTable } from './CredentialTable'
import { VersionHistoryStub } from './VersionHistoryStub'

const MCP_PAGE_TABS = [
  { id: 'all', label: '全部连接' },
  { id: 'mcp', label: 'MCP 服务' },
  { id: 'http', label: 'HTTP API' },
  { id: 'credentials', label: '凭据管理' },
] as const satisfies readonly WorkbenchTab<'all' | 'mcp' | 'http' | 'credentials'>[]

const MCP_PAGE_TAB_IDS = MCP_PAGE_TABS.map((tab) => tab.id) as readonly (
  | 'all' | 'mcp' | 'http' | 'credentials')[]

/** 连接抽屉：概览 / 已发现工具 / 配置 / 运行健康（壳位）/ 版本历史（壳位） */
const CONN_DRAWER_TABS = [
  { id: 'overview', label: '概览' },
  { id: 'tools', label: '已发现工具' },
  { id: 'config', label: '配置' },
  { id: 'health', label: '运行健康' },
  { id: 'versions', label: '版本历史' },
]

export default function McpPage() {
  const toast = useToast()
  const { tab, selectTab } = useWorkbenchTab(MCP_PAGE_TAB_IDS, 'all')
  const [overview, setOverview] = useState<McpOverview | null>(null)
  const [connections, setConnections] = useState<McpConnection[]>([])
  const [mcpTools, setMcpTools] = useState<McpToolSummary[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [busyAction, setBusyAction] = useState('')
  const [testResult, setTestResult] = useState<Record<string, ConnectionTestResult>>({})
  const [discoverResult, setDiscoverResult] = useState<Record<string, DiscoverResult>>({})
  // 连接详情抽屉：connId 为空 = 关闭；抽屉数据始终取 connections 最新事实源
  const [drawerConnId, setDrawerConnId] = useState<string | null>(null)
  const [drawerTab, setDrawerTab] = useState('overview')
  // HTTP API 工具登记/编辑：{ connId, tool } | null（tool 空 = 新登记）
  const [httpToolForm, setHttpToolForm] = useState<
    { connId: string; tool: McpToolSummary | null } | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [ov, conns, tools] = await Promise.all([
        getMcpOverview(), getMcpConnections(), getMcpTools(),
      ])
      setOverview(ov)
      setConnections(conns)
      setMcpTools(tools)
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  /** 新建连接：表单渲染在连接卡片分支，凭据 Tab 下先切回「全部连接」保证可见。 */
  const handleNewConnection = () => {
    setShowForm(true)
    if (tab === 'credentials') selectTab('all')
  }

  const openDrawer = (conn: McpConnection, tabId = 'overview') => {
    setDrawerConnId(conn.id)
    setDrawerTab(tabId)
  }

  // ── 写操作（沿用原确认与提示文案）────────────────────────

  const submitConnection = async (
    input: ConnectionInput & { keepSecret?: boolean },
    mode: 'create' | 'edit',
  ) => {
    const editing = mode === 'edit'
      ? connections.find((c) => c.id === drawerConnId) ?? null
      : null
    setSubmitting(true)
    try {
      if (editing) {
        await updateMcpConnection(editing.id, {
          name: input.name, base_url: input.base_url, auth_type: input.auth_type,
          network_scope: input.network_scope,
          secret: input.keepSecret ? undefined : input.secret ?? '',
        })
        toast.success('连接已更新')
      } else {
        await createMcpConnection(input)
        toast.success('连接已创建；请先测试连接再发现工具')
        setShowForm(false)
      }
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const runTest = async (conn: McpConnection) => {
    setBusyAction(`test:${conn.id}`)
    try {
      const result = await testMcpConnection(conn.id)
      setTestResult((prev) => ({ ...prev, [conn.id]: result }))
      if (result.ok) toast.success(`连接成功 (${result.elapsed_ms}ms，${result.tool_count} 个工具)`)
      else toast.error(`连接失败 (${result.error_class})`)
      await reload()
    } catch (e) {
      toast.error(`测试失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const runDiscover = async (conn: McpConnection) => {
    setBusyAction(`discover:${conn.id}`)
    try {
      const result = await discoverMcpTools(conn.id)
      setDiscoverResult((prev) => ({ ...prev, [conn.id]: result }))
      if (result.ok) {
        toast.success(
          `发现完成 v${result.discovery_version}: 新增 ${result.new?.length ?? 0} / ` +
          `变化 ${result.changed?.length ?? 0} / 消失 ${result.retired?.length ?? 0}`)
      } else {
        toast.error(`发现失败 (${result.error_class})`)
      }
      await reload()
    } catch (e) {
      toast.error(`发现失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const toggleEnabled = async (conn: McpConnection) => {
    setBusyAction(`toggle:${conn.id}`)
    try {
      await setMcpConnectionEnabled(conn.id, !conn.enabled)
      toast.success(conn.enabled
        ? '连接已停用；写调用立即被拦截，读调用最迟 15 秒退出候选'
        : '连接已启用')
      await reload()
    } catch (e) {
      toast.error(`操作失败: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const removeConnection = async (conn: McpConnection) => {
    if (!window.confirm(`删除连接「${conn.name}」？仍存在工具记录或有效授权时会被拒绝。`)) return
    setBusyAction(`delete:${conn.id}`)
    try {
      await deleteMcpConnection(conn.id)
      toast.success('连接已删除')
      if (drawerConnId === conn.id) setDrawerConnId(null)
      await reload()
    } catch (e) {
      toast.error(`删除被拒绝: ${errMessage(e)}`)
    } finally {
      setBusyAction('')
    }
  }

  const submitRegisterHttpTool = async (
    connId: string,
    input: { tool_name: string; description: string;
      invocation: HttpToolInvocation; input_schema: Record<string, unknown> },
  ) => {
    setSubmitting(true)
    try {
      const r = await registerHttpTool(connId, input)
      toast.success(`已登记 ${r.capability}（待审核）`)
      setHttpToolForm(null)
      await reload()
    } catch (e) {
      toast.error(`登记失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  const submitUpdateHttpTool = async (
    toolId: number,
    input: { description: string; invocation: HttpToolInvocation;
      input_schema: Record<string, unknown> },
  ) => {
    setSubmitting(true)
    try {
      const r = await updateHttpToolInvocation(toolId, input)
      toast.success(r.schema_changed
        ? `已保存 v${r.version}；approved 工具已漂移，需重新审核`
        : `已保存 v${r.version}（待审核）`)
      setHttpToolForm(null)
      await reload()
    } catch (e) {
      toast.error(`保存失败: ${errMessage(e)}`)
    } finally {
      setSubmitting(false)
    }
  }

  /** 配置导出：GET /admin/mcp/export → 触发浏览器下载 JSON（凭据不导出）。 */
  const handleExport = async () => {
    setExporting(true)
    try {
      const data = await exportMcpConfiguration()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `mcp-export-${new Date().toISOString().slice(0, 10)}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      toast.success('配置已导出（凭据密文与指纹不包含在导出内容中）')
    } catch (e) {
      toast.error(`导出失败: ${errMessage(e)}`)
    } finally {
      setExporting(false)
    }
  }

  // ── 派生数据 ─────────────────────────────────────────────

  const toolCountByConn = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const t of mcpTools) counts[t.connection_id] = (counts[t.connection_id] ?? 0) + 1
    return counts
  }, [mcpTools])

  const healthyCount = connections.filter((c) => c.status === 'connected').length
  const errorCount = connections.filter((c) => c.status === 'error').length
  const driftCount = mcpTools.filter((t) => t.schema_changed).length

  const scopeFiltered = useMemo(() => {
    if (tab === 'mcp') return connections.filter((c) => c.transport === 'streamable_http')
    if (tab === 'http') return connections.filter((c) => c.transport === 'http_api')
    return connections
  }, [connections, tab])

  const filtered = useMemo(() => {
    const kw = search.trim().toLowerCase()
    if (!kw) return scopeFiltered
    return scopeFiltered.filter((c) =>
      c.name.toLowerCase().includes(kw) || c.base_url.toLowerCase().includes(kw))
  }, [scopeFiltered, search])

  const drawerConn = drawerConnId
    ? connections.find((c) => c.id === drawerConnId) ?? null
    : null
  const drawerTools = drawerConn
    ? mcpTools.filter((t) => t.connection_id === drawerConn.id)
    : []

  const showCardLoading = loading && connections.length === 0

  return (
    <WorkbenchShell
      title="连接中心"
      description="MCP（Streamable HTTP 自动发现）与 HTTP API（手工登记）统一接入；动态工具的审核、治理与授权在 Tool 治理进行。"
      tabs={MCP_PAGE_TABS}
      activeTab={tab}
      onTabChange={selectTab}
      actions={
        <>
          <AssetActionButton onClick={() => void reload()} disabled={loading}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            刷新
          </AssetActionButton>
          <AssetActionButton onClick={() => void handleExport()} disabled={exporting}>
            {exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            配置导出
          </AssetActionButton>
          <AssetActionButton tone="primary" onClick={handleNewConnection}>
            <Plus size={14} /> 新建连接
          </AssetActionButton>
        </>
      }
    >
      <div className="mx-auto w-full max-w-7xl space-y-4 px-4 py-5 sm:px-6">
        {error && <ErrorState title="加载失败" message={error} onRetry={() => void reload()} />}

        <StatMetricBar items={[
          { label: '连接总数', value: connections.length },
          { label: '健康连接', value: healthyCount, tone: healthyCount > 0 ? 'accent' : 'default' },
          { label: '连接异常', value: errorCount, tone: errorCount > 0 ? 'danger' : 'default' },
          { label: 'Schema 漂移', value: driftCount, tone: driftCount > 0 ? 'warn' : 'default' },
          {
            label: '发现工具数', value: mcpTools.length,
            hint: overview ? `运行时生效 ${overview.active_tools.length}` : undefined,
          },
        ]} />

        {overview && overview.derive_errors.length > 0 && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-[12px] text-amber-800">
            <div className="mb-1 flex items-center gap-1 font-medium">
              <AlertTriangle size={14} /> {overview.derive_errors.length} 个工具派生失败（未进入运行时）
            </div>
            {overview.derive_errors.map((e) => (
              <div key={e.capability} className="font-mono">{e.capability}: {e.reason}</div>
            ))}
          </div>
        )}

        {tab === 'credentials' ? (
          <AssetSection title="凭据管理" icon={<KeyRound size={16} />}
            meta="只读视图：凭据密文不出服务端，仅显示配置状态与尾号；绑定/更换在连接详情的配置 Tab">
            {showCardLoading ? <Skeleton rows={4} cols={5} /> : (
              <CredentialTable connections={connections}
                onGoBind={(conn) => openDrawer(conn, 'config')} />
            )}
          </AssetSection>
        ) : (
          <>
            {showForm && (
              <AssetSection title="新建连接" icon={<Plus size={16} />}
                meta="SSRF 校验在服务端；创建后先测试连接再发现工具">
                <ConnectionForm initial={null} submitting={submitting}
                  onSubmit={(input) => void submitConnection(input, 'create')}
                  onCancel={() => setShowForm(false)} />
              </AssetSection>
            )}

            <div className="flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted/60" />
                <input aria-label="搜索连接"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="搜索连接名称 / 地址"
                  className="min-w-[220px] rounded-md border border-border-subtle bg-surface-base py-1.5 pl-8 pr-2.5 text-[12px] text-text-primary" />
              </div>
              <span className="text-[11px] text-text-muted">{filtered.length} 个连接</span>
            </div>

            {showCardLoading ? (
              <Skeleton rows={3} cols={3} />
            ) : filtered.length === 0 ? (
              connections.length === 0 && !search ? (
                <EmptyState title="连接" description="尚未接入任何外部 MCP Server 或 HTTP API"
                  actionLabel="新建连接" onAction={() => setShowForm(true)} />
              ) : (
                <EmptyState title="连接" description="没有匹配筛选的连接；调整搜索词或切换页级 Tab" />
              )
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                {filtered.map((conn) => (
                  <ConnectionCard key={conn.id} conn={conn}
                    toolCount={toolCountByConn[conn.id] ?? 0}
                    onOpen={(c) => openDrawer(c)} />
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {drawerConn && (
        <DetailDrawer
          open
          width="lg"
          onClose={() => { setDrawerConnId(null); setHttpToolForm(null) }}
          title={drawerConn.name}
          subtitle={
            <span className="flex flex-wrap items-center gap-1.5">
              <TransportBadge transport={drawerConn.transport} />
              <span className="font-mono">{drawerConn.base_url}</span>
            </span>
          }
          tabs={CONN_DRAWER_TABS}
          activeTab={drawerTab}
          onTabChange={setDrawerTab}
          actions={
            <>
              <AssetActionButton disabled={busyAction === `test:${drawerConn.id}`}
                onClick={() => void runTest(drawerConn)}>
                {busyAction === `test:${drawerConn.id}`
                  ? <Loader2 size={13} className="animate-spin" /> : <Plug size={13} />}
                测试健康
              </AssetActionButton>
              {drawerConn.transport === 'http_api' ? (
                <AssetActionButton onClick={() => setHttpToolForm({ connId: drawerConn.id, tool: null })}>
                  <Plus size={13} /> 登记工具
                </AssetActionButton>
              ) : (
                <AssetActionButton disabled={busyAction === `discover:${drawerConn.id}`}
                  onClick={() => void runDiscover(drawerConn)}>
                  {busyAction === `discover:${drawerConn.id}`
                    ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                  重新发现
                </AssetActionButton>
              )}
              <AssetActionButton disabled={busyAction === `toggle:${drawerConn.id}`}
                onClick={() => void toggleEnabled(drawerConn)}>
                {busyAction === `toggle:${drawerConn.id}`
                  ? <Loader2 size={13} className="animate-spin" />
                  : drawerConn.enabled ? <CircleSlash size={13} /> : <CheckCircle2 size={13} />}
                {drawerConn.enabled ? '停用连接' : '启用连接'}
              </AssetActionButton>
            </>
          }
        >
          {drawerTab === 'overview' && (
            <div className="space-y-3 text-[12.5px]">
              <div className="grid gap-x-4 gap-y-2 md:grid-cols-2">
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">状态</span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <StatusChip tone={CONN_STATUS_META[drawerConn.status]?.tone ?? 'neutral'}>
                      {CONN_STATUS_META[drawerConn.status]?.label ?? drawerConn.status}
                    </StatusChip>
                    {!drawerConn.enabled && (
                      <span className="rounded bg-slate-100 px-1 text-[10px] text-slate-500">已停用</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">地址</span>
                  <span className="truncate font-mono text-[11px]" title={drawerConn.base_url}>{drawerConn.base_url}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">凭据</span>
                  <span>
                    {drawerConn.auth_type === 'none' ? '无认证' : (
                      <span title={`指纹 ${drawerConn.credential.fingerprint}`}>
                        Bearer ****{drawerConn.credential.last4 || '????'}
                      </span>
                    )}
                    {drawerConn.network_scope === 'private' && (
                      <span className="ml-1 rounded bg-amber-100 px-1 text-[10px] text-amber-700">内网</span>
                    )}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">传输方式</span>
                  <TransportBadge transport={drawerConn.transport} />
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">发现版本</span>
                  <span>v{drawerConn.discovery_version}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-text-muted">最近检查</span>
                  <span className="text-text-muted">
                    {drawerConn.last_synced_at ? new Date(drawerConn.last_synced_at).toLocaleString() : '—'}
                  </span>
                </div>
              </div>
              {drawerConn.last_error && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
                  <span className="font-medium">最近错误：</span>{drawerConn.last_error}
                </div>
              )}
              {testResult[drawerConn.id] && (
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[11px]">
                  {testResult[drawerConn.id].ok
                    ? <span className="text-emerald-700">连接测试：成功 {testResult[drawerConn.id].elapsed_ms}ms · {testResult[drawerConn.id].tool_count} 个工具</span>
                    : <span className="text-red-600">连接测试：失败 {testResult[drawerConn.id].error_class} · {testResult[drawerConn.id].error}</span>}
                </div>
              )}
              {discoverResult[drawerConn.id] && (
                <div className="rounded-lg border border-border-subtle bg-surface-base px-3 py-2 text-[11px] text-text-muted">
                  上次发现 v{discoverResult[drawerConn.id].discovery_version}：
                  新增 {(discoverResult[drawerConn.id].new ?? []).map((t) => t.tool).join('、') || '无'}；
                  变化 {(discoverResult[drawerConn.id].changed ?? []).map((t) => t.tool).join('、') || '无'}；
                  消失 {(discoverResult[drawerConn.id].retired ?? []).map((t) => t.tool).join('、') || '无'}
                  {(discoverResult[drawerConn.id].skipped ?? []).length > 0 &&
                    `；跳过 ${(discoverResult[drawerConn.id].skipped ?? []).map((t) => t.tool).join('、')}`}
                </div>
              )}
              <div className="text-[11px] text-text-muted">
                编辑连接信息请切换到「配置」Tab；动态工具的审核与授权在 Tool 治理进行。
              </div>
            </div>
          )}

          {drawerTab === 'tools' && (
            <div className="space-y-3">
              {httpToolForm?.connId === drawerConn.id && (
                <div className="rounded-lg border border-border-subtle bg-surface-elevated/40">
                  <div className="px-4 pt-3 text-[12px] font-medium">
                    {httpToolForm.tool
                      ? `编辑调用定义：${httpToolForm.tool.tool_name}`
                      : `登记 HTTP API 工具：${drawerConn.name}`}
                  </div>
                  <HttpToolForm mode={httpToolForm.tool ? 'edit' : 'register'}
                    initial={httpToolForm.tool}
                    submitting={submitting}
                    onRegister={(input) => void submitRegisterHttpTool(drawerConn.id, input)}
                    onUpdate={(input) =>
                      void submitUpdateHttpTool(httpToolForm.tool!.id, input)}
                    onCancel={() => setHttpToolForm(null)} />
                </div>
              )}
              {drawerTools.length === 0 && !httpToolForm ? (
                <EmptyState title="已发现工具"
                  description={drawerConn.transport === 'http_api'
                    ? '尚未登记工具；点击右上角「登记工具」手工登记'
                    : '尚未发现工具；点击右上角「重新发现」从上游拉取'} />
              ) : (
                <ul className="divide-y divide-border-subtle">
                  {drawerTools.map((tool) => {
                    const status = TOOL_STATUS_META[tool.status] ?? { label: tool.status, tone: 'neutral' as StatusTone }
                    const isHttp = tool.conn_transport === 'http_api'
                    return (
                      <li key={tool.id} className="py-2.5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[12px] text-text-primary">{tool.tool_name}</span>
                          <span className="font-mono text-[11px] text-text-muted">{tool.capability}</span>
                          <StatusChip tone={status.tone}>{status.label}</StatusChip>
                          {tool.schema_changed && (
                            <span className="rounded bg-red-100 px-1 text-[10px] text-red-700"
                              title="上游 Schema 已变化，需重新审核">Schema 漂移</span>
                          )}
                          <span className="text-[11px] text-text-muted">
                            风险 {tool.governance?.risk_level ?? '—'}
                          </span>
                          {isHttp && (
                            <AssetActionButton
                              onClick={() => setHttpToolForm({ connId: drawerConn.id, tool })}>
                              编辑调用
                            </AssetActionButton>
                          )}
                          <Link
                            href={`/tools?q=${encodeURIComponent(tool.capability)}`}
                            className="ml-auto text-[11px] font-medium text-accent hover:underline"
                            title="在 Tool 治理中查看并审核该工具（预填搜索）">
                            前往 Tool 治理 →
                          </Link>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
              <div className="text-[11px] text-text-muted">
                工具的审核、执行策略与授权在「Tool 治理」统一操作；此处仅查看发现结果。
              </div>
            </div>
          )}

          {drawerTab === 'config' && (
            <div className="space-y-4">
              <ConnectionForm initial={drawerConn} submitting={submitting}
                onSubmit={(input) => void submitConnection(input, 'edit')}
                onCancel={() => setDrawerTab('overview')} />
              <div className="rounded-lg border border-red-200 bg-red-50/50 p-3">
                <div className="mb-2 text-[12px] font-medium text-red-700">危险操作</div>
                <div className="mb-2 text-[11px] text-text-muted">
                  删除连接会一并校验工具记录与有效授权；仍被引用时服务端会拒绝。
                </div>
                <button type="button" disabled={busyAction === `delete:${drawerConn.id}`}
                  onClick={() => void removeConnection(drawerConn)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-300 bg-white px-3 py-1.5 text-[12px] font-medium text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50">
                  {busyAction === `delete:${drawerConn.id}`
                    ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  删除连接
                </button>
              </div>
            </div>
          )}

          {drawerTab === 'health' && <ConnectionHealthSection conn={drawerConn} />}

          {drawerTab === 'versions' && <VersionHistoryStub conn={drawerConn} />}
        </DetailDrawer>
      )}
    </WorkbenchShell>
  )
}
