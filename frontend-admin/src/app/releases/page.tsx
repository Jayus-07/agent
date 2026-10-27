'use client'

/**
 * /releases — 发布与版本中心（V2 两档）
 *
 * 治理配置发布（默认档）：治理配置（连接声明 + 能力包 + 授权）的快照草稿 →
 * 差异预览 → 发布 → 回滚 + 配置导入。后端 admin_governance_releases.py
 * （Batch 5-2），发布=确认草稿为生效版本，回滚按快照恢复并生成新版本；
 * 凭据绝不参与（导入连接一律 disabled 无凭据）。
 * 系统发布与质量 Gate（第二档）：12 门 Release Gate 结果（ai.release_records，
 * 原 M8 页面内容整体保留为第二档）。
 */
import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2, ChevronDown, ChevronRight, ClipboardPaste,
  History, RefreshCw, Rocket, RotateCcw, XCircle,
} from 'lucide-react'
import WorkbenchShell, { type WorkbenchTab } from '@/components/workbench/WorkbenchShell'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DetailDrawer from '@/components/layout/DetailDrawer'
import StatMetricBar from '@/components/layout/StatMetricBar'
import StatusChip from '@/components/layout/StatusChip'
import { AssetSection } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
import EmptyState from '@/components/shared/EmptyState'
import {
  getReleaseLatest, getReleases,
  type ReleaseLatestResponse, type ReleaseListResponse, type ReleaseRecord,
} from '@/api/governance'
import {
  createGovernanceRelease, getGovernanceReleaseDiff, importGovernanceConfiguration,
  listGovernanceReleases, publishGovernanceRelease, rollbackGovernanceRelease,
  type GovernanceImportResult, type GovernanceRelease, type GovernanceReleaseDiff,
  type GovernanceReleaseStatus,
} from '@/api/governanceReleases'

const RELEASE_TABS = [
  { id: 'governance', label: '治理配置发布' },
  { id: 'system', label: '系统发布与质量 Gate' },
] as const satisfies readonly WorkbenchTab<'governance' | 'system'>[]

const TAB_IDS = RELEASE_TABS.map((tab) => tab.id) as readonly (
  'governance' | 'system'
)[]

const RELEASE_STATUS_TONE: Record<GovernanceReleaseStatus, 'warn' | 'success' | 'neutral'> = {
  draft: 'warn', published: 'success', rolled_back: 'neutral',
}
const RELEASE_STATUS_LABEL: Record<GovernanceReleaseStatus, string> = {
  draft: '草稿', published: '已发布', rolled_back: '已回滚',
}
const CHANGE_LABEL: Record<string, string> = {
  modified: '变更', added: '当前新增（回滚不删除）', removed: '版本有、当前无',
}
const CHANGE_TONE: Record<string, 'info' | 'warn' | 'danger'> = {
  modified: 'info', added: 'warn', removed: 'danger',
}

function formatTime(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—'
}

// ═══════════════ 治理配置发布（第一档） ═══════════════

interface DiffDrawerState {
  release: GovernanceRelease
  diff: GovernanceReleaseDiff | null
  loading: boolean
  error: string | null
}

function GovernanceReleasesTab() {
  const [releases, setReleases] = useState<GovernanceRelease[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createDesc, setCreateDesc] = useState('')
  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [drawer, setDrawer] = useState<DiffDrawerState | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState<string | null>(null)
  const [importResult, setImportResult] = useState<GovernanceImportResult | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    listGovernanceReleases()
      .then((r) => setReleases(r.releases))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const onCreate = () => {
    setCreating(true)
    setNotice(null)
    createGovernanceRelease(createDesc.trim())
      .then((rel) => {
        setNotice(`已创建草稿 v${rel.version}（${rel.connection_count} 个连接 / ${rel.bundle_count} 个能力包）`)
        setCreateDesc('')
        load()
      })
      .catch((e: Error) => setNotice(`创建失败：${e.message}`))
      .finally(() => setCreating(false))
  }

  const onPublish = (rel: GovernanceRelease) => {
    setBusyId(rel.id)
    setNotice(null)
    publishGovernanceRelease(rel.id)
      .then(() => { setNotice(`v${rel.version} 已发布生效`); load() })
      .catch((e: Error) => setNotice(`发布失败：${e.message}`))
      .finally(() => setBusyId(null))
  }

  const onRollback = (rel: GovernanceRelease) => {
    if (!window.confirm(
      `回滚到 v${rel.version}？将按该版本快照恢复连接声明、能力包与授权关系`
      + '（不恢复凭据；不删除快照外的新增资产），并生成新的回滚版本。')) return
    setBusyId(rel.id)
    setNotice(null)
    rollbackGovernanceRelease(rel.id)
      .then((r) => {
        setNotice(`已回滚至 v${r.rolled_back_to}：恢复 ${r.restored_connections} 个连接 / `
          + `${r.restored_bundles} 个能力包，生成新版本 v${r.new_version}`)
        load()
      })
      .catch((e: Error) => setNotice(`回滚失败：${e.message}`))
      .finally(() => setBusyId(null))
  }

  const openDiff = (rel: GovernanceRelease) => {
    setDrawer({ release: rel, diff: null, loading: true, error: null })
    getGovernanceReleaseDiff(rel.id)
      .then((diff) => setDrawer({ release: rel, diff, loading: false, error: null }))
      .catch((e: Error) => setDrawer({ release: rel, diff: null, loading: false, error: e.message }))
  }

  const onImport = () => {
    let payload: unknown
    try {
      payload = JSON.parse(importText)
    } catch {
      setImportError('导入内容不是合法 JSON')
      return
    }
    setImporting(true)
    setImportError(null)
    setImportResult(null)
    importGovernanceConfiguration(payload)
      .then((r) => { setImportResult(r); setImportText('') })
      .catch((e: Error) => setImportError(e.message))
      .finally(() => setImporting(false))
  }

  const hasDraft = releases?.some((r) => r.status === 'draft') ?? false

  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-6">
      {notice && (
        <div data-testid="gov-release-notice" className="mb-4 rounded-lg border border-accent/30 bg-accent/5 px-4 py-2.5 text-[12px] text-text-primary">
          {notice}
        </div>
      )}
      <StatMetricBar
        className="mb-4"
        items={[
          { label: '版本总数', value: releases?.length ?? '…' },
          { label: '草稿', value: releases?.filter((r) => r.status === 'draft').length ?? 0, tone: 'warn' },
          { label: '已发布', value: releases?.filter((r) => r.status === 'published').length ?? 0, tone: 'accent' },
        ]}
      />
      <div className="mb-4 rounded-lg border border-border-subtle bg-white p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[12px] text-text-secondary">创建快照草稿（快照当前全部连接声明 + 能力包 + 授权关系，不含任何凭据）：</span>
          <input
            value={createDesc}
            onChange={(e) => setCreateDesc(e.target.value)}
            placeholder="版本说明（可留空）"
            className="w-56 rounded-md border border-border-subtle px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
          />
          <button
            type="button"
            data-testid="gov-create-snapshot"
            onClick={onCreate}
            disabled={creating}
            className="rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-50"
          >
            {creating ? '创建中…' : '创建快照'}
          </button>
          <button
            type="button"
            onClick={() => { setImportOpen(true); setImportResult(null); setImportError(null) }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle px-3 py-1.5 text-[12px] text-text-secondary hover:border-accent/30 hover:bg-bg-hover"
          >
            <ClipboardPaste size={13} /> 导入配置
          </button>
        </div>
        {hasDraft && (
          <p className="mt-2 text-[11px] text-text-muted">
            发布前请通过「查看差异」人工核对变更（连接声明 / 授权关系 / 能力包成员）——差异即发布前检查面。
          </p>
        )}
      </div>

      {error && <ErrorState message={error} onRetry={load} />}
      {!error && loading && (
        <div className="rounded-xl border border-border-subtle bg-white p-8 text-center text-[13px] text-text-muted">加载中…</div>
      )}
      {!error && !loading && releases && (
        <AssetSection title="版本历史" meta={`共 ${releases.length} 个版本`}>
          {releases.length === 0 ? (
            <EmptyState
              title="治理配置版本"
              description="尚无版本——创建首个快照草稿后可发布与回滚"
            />
          ) : (
            <div className="overflow-x-auto">
              <table data-testid="gov-release-table" className="w-full text-[12px]">
                <thead>
                  <tr className="border-b border-border-subtle text-left text-text-muted">
                    <th className="py-2 pr-3 font-medium">版本</th>
                    <th className="py-2 pr-3 font-medium">状态</th>
                    <th className="py-2 pr-3 font-medium">说明</th>
                    <th className="py-2 pr-3 font-medium">连接 / 包</th>
                    <th className="py-2 pr-3 font-medium">创建</th>
                    <th className="py-2 pr-3 font-medium">发布</th>
                    <th className="py-2 font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {releases.map((rel) => (
                    <tr key={rel.id} className="border-b border-black/[0.04] last:border-0">
                      <td className="py-2.5 pr-3 font-mono font-medium text-text-primary">v{rel.version}</td>
                      <td className="py-2.5 pr-3">
                        <StatusChip tone={RELEASE_STATUS_TONE[rel.status]}>{RELEASE_STATUS_LABEL[rel.status]}</StatusChip>
                      </td>
                      <td className="max-w-[16rem] truncate py-2.5 pr-3 text-text-secondary" title={rel.description}>
                        {rel.description || '—'}
                      </td>
                      <td className="py-2.5 pr-3 tabular-nums text-text-secondary">
                        {rel.connection_count} / {rel.bundle_count}
                      </td>
                      <td className="py-2.5 pr-3 text-text-muted">
                        {formatTime(rel.created_at)}{rel.created_by ? ` · ${rel.created_by}` : ''}
                      </td>
                      <td className="py-2.5 pr-3 text-text-muted">{formatTime(rel.published_at)}</td>
                      <td className="py-2.5">
                        <div className="flex flex-wrap gap-1.5">
                          <button type="button" onClick={() => openDiff(rel)}
                            className="rounded-md border border-border-subtle px-2 py-1 text-[11px] text-text-secondary hover:border-accent/30 hover:text-text-primary">
                            查看差异
                          </button>
                          {rel.status === 'draft' && (
                            <button type="button" data-testid={`gov-publish-${rel.version}`} onClick={() => onPublish(rel)}
                              disabled={busyId === rel.id}
                              className="rounded-md bg-accent px-2 py-1 text-[11px] font-medium text-white hover:bg-accent-hover disabled:opacity-50">
                              {busyId === rel.id ? '发布中…' : '检查并发布'}
                            </button>
                          )}
                          {rel.status === 'published' && (
                            <button type="button" data-testid={`gov-rollback-${rel.version}`} onClick={() => onRollback(rel)}
                              disabled={busyId === rel.id}
                              className="rounded-md border border-amber-300 px-2 py-1 text-[11px] font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50">
                              {busyId === rel.id ? '回滚中…' : '配置回滚'}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </AssetSection>
      )}

      {/* 差异预览抽屉 */}
      <DetailDrawer
        open={drawer !== null}
        onClose={() => setDrawer(null)}
        title={drawer ? `v${drawer.release.version} 差异预览` : ''}
        subtitle="方向 = 回滚到该版本将发生什么；「当前新增」不会被回滚删除，请显式处置"
        width="lg"
      >
        {drawer?.loading && <div className="text-[13px] text-text-muted">计算差异中…</div>}
        {drawer?.error && <ErrorState message={drawer.error} onRetry={() => openDiff(drawer.release)} />}
        {drawer?.diff && (
          <>
            <StatMetricBar
              className="mb-4"
              items={[
                { label: '变更', value: drawer.diff.counts.modified, tone: 'accent' },
                { label: '当前新增', value: drawer.diff.counts.added, tone: 'warn' },
                { label: '版本有当前无', value: drawer.diff.counts.removed, tone: 'danger' },
              ]}
            />
            {drawer.diff.changes.length === 0 ? (
              <EmptyState
                title="差异"
                description="与当前配置完全一致"
              />
            ) : (
              <div className="space-y-2">
                {drawer.diff.changes.map((ch) => (
                  <div key={`${ch.kind}-${ch.name}`} data-testid="gov-diff-entry" className="rounded-lg border border-border-subtle p-3">
                    <div className="flex items-center gap-2">
                      <StatusChip tone={CHANGE_TONE[ch.change]}>{CHANGE_LABEL[ch.change]}</StatusChip>
                      <span className="text-[11px] text-text-muted">{ch.kind === 'connection' ? '连接' : '能力包'}</span>
                      <span className="text-[12px] font-medium text-text-primary">{ch.name}</span>
                    </div>
                    {ch.fields.length > 0 && (
                      <table className="mt-2 w-full text-[11px]">
                        <thead>
                          <tr className="text-left text-text-muted">
                            <th className="py-1 pr-2 font-medium">字段</th>
                            <th className="py-1 pr-2 font-medium">版本值</th>
                            <th className="py-1 font-medium">当前值</th>
                          </tr>
                        </thead>
                        <tbody>
                          {ch.fields.map((f) => (
                            <tr key={f.field} className="border-t border-black/[0.04]">
                              <td className="py-1 pr-2 font-medium text-text-secondary">{f.field}</td>
                              <td className="max-w-[14rem] break-all py-1 pr-2 font-mono text-text-primary">
                                {typeof f.release === 'object' ? JSON.stringify(f.release) : String(f.release)}
                              </td>
                              <td className="max-w-[14rem] break-all py-1 font-mono text-text-secondary">
                                {typeof f.current === 'object' ? JSON.stringify(f.current) : String(f.current)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </DetailDrawer>

      {/* 导入配置抽屉 */}
      <DetailDrawer
        open={importOpen}
        onClose={() => setImportOpen(false)}
        title="导入治理配置"
        subtitle="粘贴导出包 JSON：连接一律 disabled 且不带凭据；能力包不建授权；名称冲突跳过"
        width="md"
      >
        <textarea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          rows={10}
          placeholder='{"version": 1, "connections": [...], "capability_bundles": [...]}'
          className="w-full rounded-lg border border-border-subtle p-3 font-mono text-[11px] outline-none focus:border-accent"
        />
        <div className="mt-3 flex items-center gap-2">
          <button type="button" data-testid="gov-import-submit" onClick={onImport} disabled={importing || !importText.trim()}
            className="rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-white hover:bg-accent-hover disabled:opacity-50">
            {importing ? '导入中…' : '导入'}
          </button>
          {importError && <span className="text-[12px] text-rose-600">{importError}</span>}
        </div>
        {importResult && (
          <div data-testid="gov-import-result" className="mt-4 space-y-2 rounded-lg border border-border-subtle p-3 text-[12px]">
            <div>新建连接：{importResult.created_connections.join('、') || '无'}</div>
            <div>新建能力包：{importResult.created_bundles.join('、') || '无'}</div>
            {importResult.skipped.length > 0 && (
              <div className="text-amber-700">跳过（名称冲突）：{importResult.skipped.map((s) => `${s.name}（${s.reason}）`).join('、')}</div>
            )}
            {importResult.redacted.length > 0 && (
              <div className="text-amber-700">URL 内嵌凭据已剔除：{importResult.redacted.map((r) => r.name).join('、')}</div>
            )}
            {importResult.rejected.length > 0 && (
              <div className="text-rose-600">拒绝导入：{importResult.rejected.map((r) => `${r.name}（${r.reason}）`).join('、')}</div>
            )}
            <p className="text-[11px] text-text-muted">
              导入的连接需重新配置凭据并启用后才能被发现；授权关系需在 Skill 与 Capability 页重新确认。
            </p>
          </div>
        )}
      </DetailDrawer>
    </div>
  )
}

// ═══════════════ 系统发布与质量 Gate（第二档，原 M8 页面内容） ═══════════════

const GATE_LABEL: Record<string, string> = {
  Gate0_BuildIdentity: '构建身份',
  Gate1_Migration: '迁移一致',
  Gate2_Auth: '认证拒绝',
  Gate3_Tenant: '租户隔离',
  Gate4_Chat: '主链路',
  Gate5_RAG: '知识问答',
  Gate6_SQL: '数据查询',
  Gate7_CS: '客服域',
  Gate8_Travel: '旅游域',
  Gate9_Selection: '选品漏斗',
  Gate10_TaskRuntime: '任务运行时',
  Gate11_Model: '模型治理',
  Gate12_Observability: '可观测',
}

function gateOrder(gates: Record<string, boolean>): string[] {
  return Object.keys(gates).sort((a, b) => {
    const na = Number(a.match(/^Gate(\d+)/)?.[1] ?? 99)
    const nb = Number(b.match(/^Gate(\d+)/)?.[1] ?? 99)
    return na - nb
  })
}

function GatesPills({ gates }: { gates: Record<string, boolean> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {gateOrder(gates).map((g) => (
        <span
          key={g}
          title={`${GATE_LABEL[g] ?? g}：${gates[g] ? 'PASS' : 'FAIL'}`}
          className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${
            gates[g] ? 'bg-emerald-50 text-emerald-700' : 'bg-red-100 text-red-700'
          }`}
        >
          {g.replace(/^Gate(\d+)_.*/, '$1')}
        </span>
      ))}
    </div>
  )
}

function ResultBadge({ result }: { result: 'PASS' | 'FAIL' }) {
  return result === 'PASS' ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[12px] font-medium text-emerald-700">
      <CheckCircle2 size={13} /> PASS
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[12px] font-medium text-red-700">
      <XCircle size={13} /> FAIL
    </span>
  )
}

function CurrentVersionCard({ latest, last_pass }: ReleaseLatestResponse) {
  if (!latest) {
    return (
      <div className="mb-5 rounded-lg border border-dashed border-black/10 bg-white p-5 text-[13px] text-text-secondary">
        暂无发布记录——release.sh 跑完 12 门 Release Gate 后自动落库（M8）。
      </div>
    )
  }
  const passCount = Object.values(latest.gates).filter(Boolean).length
  const totalCount = Object.keys(latest.gates).length
  const rollbackNeeded = latest.result !== 'PASS'
  return (
    <div
      className={`mb-5 rounded-lg border p-5 ${
        latest.result === 'PASS' ? 'border-black/5 bg-white' : 'border-red-200 bg-red-50/40'
      }`}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Rocket size={18} className="text-text-secondary" />
        <span className="font-mono text-[16px] font-semibold text-text-primary">{latest.git_sha}</span>
        <ResultBadge result={latest.result} />
        <span className="text-[12px] text-text-secondary">
          门禁 {passCount}/{totalCount} · 构建 {latest.build_time || '?'} ·{' '}
          {latest.finished_at ?? latest.created_at}
          {latest.operator ? ` · 操作者 ${latest.operator}` : ''}
        </span>
      </div>
      <div className="mt-3">
        <GatesPills gates={latest.gates} />
      </div>
      {rollbackNeeded && last_pass && (
        <div className="mt-3 flex items-start gap-2 rounded-md bg-amber-50 p-3 text-[12px] leading-relaxed text-amber-800">
          <RotateCcw size={14} className="mt-0.5 shrink-0" />
          <span>
            当前发布 FAIL，最近一次 PASS 为{' '}
            <code className="rounded bg-amber-100 px-1 font-mono">{last_pass.git_sha}</code>
            （{last_pass.finished_at ?? last_pass.created_at}）。回滚 = 用该 commit 重建镜像发布
            （<code className="font-mono">git checkout {last_pass.git_sha} && bash scripts/release.sh</code>）。
          </span>
        </div>
      )}
    </div>
  )
}

function HistoryRow({ release }: { release: ReleaseRecord }) {
  const [open, setOpen] = useState(false)
  const passCount = Object.values(release.gates).filter(Boolean).length
  const totalCount = Object.keys(release.gates).length
  return (
    <div className="rounded-lg border border-black/5 bg-white">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-black/[0.02]"
      >
        {open ? <ChevronDown size={14} className="text-text-muted" />
          : <ChevronRight size={14} className="text-text-muted" />}
        <span className="font-mono text-[13px] font-medium text-text-primary">{release.git_sha}</span>
        <ResultBadge result={release.result} />
        <span className="text-[12px] text-text-secondary">
          {passCount}/{totalCount} 门 · {release.finished_at ?? release.created_at}
          {release.operator ? ` · ${release.operator}` : ''}
        </span>
      </button>
      {open && (
        <div className="border-t border-black/5 px-4 py-3">
          <GatesPills gates={release.gates} />
          <table className="mt-3 w-full text-[12px]">
            <tbody>
              {gateOrder(release.gates).map((g) => (
                <tr key={g} className="border-b border-black/[0.04] last:border-0">
                  <td className="w-28 py-1.5 text-text-secondary">{GATE_LABEL[g] ?? g}</td>
                  <td className="w-24 py-1.5">
                    {release.gates[g] ? (
                      <span className="text-emerald-600">PASS</span>
                    ) : (
                      <span className="font-medium text-red-600">FAIL</span>
                    )}
                  </td>
                  <td className="py-1.5 font-mono text-[11px] text-text-muted">
                    {release.gate_details?.[g] ?? release.gate_details?.[g.replace(/^Gate(\d+)_/, 'g$1')] ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function SystemGateTab() {
  const [latestInfo, setLatestInfo] = useState<ReleaseLatestResponse | null>(null)
  const [list, setList] = useState<ReleaseListResponse | null>(null)
  const [resultFilter, setResultFilter] = useState<'' | 'PASS' | 'FAIL'>('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = (filter: '' | 'PASS' | 'FAIL' = resultFilter) => {
    setLoading(true)
    setError(null)
    Promise.all([getReleaseLatest(), getReleases({ result: filter, limit: 50 })])
      .then(([latest, lst]) => {
        setLatestInfo(latest)
        setList(lst)
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load(resultFilter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultFilter])

  return (
    <div className="mx-auto w-full max-w-7xl px-6 py-6">
      <div className="mb-6 flex items-start justify-between">
        <p className="text-[12px] text-text-secondary">
          12 门 Release Gate 结果落库 · 发布时间轴与回滚判据（回滚 = 用 PASS commit 重建镜像，属代码部署）
        </p>
        <button
          onClick={() => load()}
          className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-black/10 px-3 py-1.5 text-[13px] text-text-secondary hover:bg-black/[0.03]"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={() => load()} />}

      {!error && latestInfo && <CurrentVersionCard {...latestInfo} />}

      {!error && list && (
        <>
          <div className="mb-3 mt-6 flex items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-text-primary">
              <History size={15} /> 历史发布
              <span className="font-normal text-text-muted">（共 {list.total} 条）</span>
            </span>
            <div className="ml-auto flex gap-1">
              {([['', '全部'], ['PASS', 'PASS'], ['FAIL', 'FAIL']] as const).map(([v, label]) => (
                <button
                  key={v || 'all'}
                  onClick={() => setResultFilter(v)}
                  className={`rounded-md px-2.5 py-1 text-[12px] ${
                    resultFilter === v
                      ? 'bg-black/[0.06] font-medium text-text-primary'
                      : 'text-text-secondary hover:bg-black/[0.03]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {list.releases.length === 0 ? (
            <div className="rounded-lg border border-dashed border-black/10 bg-white p-6 text-center text-[13px] text-text-secondary">
              无匹配记录
            </div>
          ) : (
            <div className="space-y-2">
              {list.releases.map((r) => (
                <HistoryRow key={r.id} release={r} />
              ))}
            </div>
          )}
        </>
      )}

      <p className="mt-5 text-[11px] leading-relaxed text-text-muted">
        落库链路：scripts/release.sh（契约 lock 漂移检测 → 迁移 preflight → 构建 → up → 12 门冒烟）
        → verify_release_gate.py 跑完直写 ai.release_records（落库失败 = 发布无记录 = 违规，rc=1）。
        契约 lock 漂移检测与 CI 接线（tool_quality workflow）同源：gen_tool_contract_lock --check。
      </p>
    </div>
  )
}

export default function ReleasesPage() {
  const { tab, selectTab } = useWorkbenchTab(TAB_IDS, 'governance')
  return (
    <WorkbenchShell
      title="发布记录"
      description="治理配置发布（快照 → 差异 → 发布 → 回滚）与系统发布质量 Gate"
      tabs={RELEASE_TABS}
      activeTab={tab}
      onTabChange={selectTab}
    >
      {tab === 'governance' ? <GovernanceReleasesTab /> : <SystemGateTab />}
    </WorkbenchShell>
  )
}
