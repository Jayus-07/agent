'use client'

/** 供应商与密钥 tab —— 主组件（列表 + 编排）。
 *
 *  B4 拆分：纯函数与子组件迁入 `providers/` 子目录 ——
 *  `presets.ts`（预置反查/地址诊断）、`draft.ts`（草稿类型/工厂）、
 *  `format.ts`（展示格式化）、`catalogSections.ts`（目录分组）、
 *  `fixHints.ts`（去修动作）、`ProbeResultDetails.tsx`、`BaseUrlAdvisor.tsx`、
 *  `ErrorNote.tsx`、`ModelCatalogPicker.tsx`、`ProviderEditor.tsx`、
 *  `ProviderModelEditor.tsx`。本文件只保留列表渲染与数据编排。
 */
import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Edit3, FlaskConical, LockKeyhole, Plus, Search, SlidersHorizontal, Trash2, X } from 'lucide-react'
import {
  addProviderModel,
  createProvider,
  removeProviderModel,
  saveProvider,
  deleteProvider,
  verifyDraftProvider,
  verifyProvider,
  type ProbeMode,
  type ProbeResponse,
} from '@/api/modelConfig'
import {
  maskSecret,
  probeFallbackSummary,
  type ModelKind,
  type PlanId,
  type PresetPlan,
  type ProviderPreset,
  type ProviderRow,
} from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'
import ErrorNote from './providers/ErrorNote'
import ProbeResultDetails from './providers/ProbeResultDetails'
import ProviderEditor from './providers/ProviderEditor'
import ProviderModelEditor from './providers/ProviderModelEditor'
import { billingForPlan, presetDisplayName, unresolvedPlaceholder } from './providers/presets'
import { formatElapsed, formatLiveElapsed, modelKindBadgeClass, modelKindShortLabel, probeModeLabel } from './providers/format'
import { draftFromRow, modelRemovalBlockReason, newDraft, type Draft, type ModelDraft, type ModelRemoval } from './providers/draft'

interface Props {
  providers: ProviderRow[]
  defaultModels: Record<string, string>
  source: 'db' | 'builtin'
  canAdmin: boolean
  onChanged: () => Promise<unknown>
  /** 计费计划「三选一」选项（来自后端预置目录接口）。 */
  plans: PresetPlan[]
  /** 预置端点目录。为空表示接口不可用，抽屉降级为手填 Base URL。 */
  presets: ProviderPreset[]
  presetsLoading: boolean
  /** B3：点角色占用徽标 → 跳「模型角色」页并高亮该角色。缺省则徽标只展示不可点。 */
  onGoToRoles?: (role: string) => void
}

export default function ProvidersTab({
  providers,
  defaultModels,
  source,
  canAdmin,
  onChanged,
  plans,
  presets,
  presetsLoading,
  onGoToRoles,
}: Props) {
  const toast = useToast()
  // ── B3：列表筛选/搜索 ──
  // 搜索匹配显示名 / ID / Base URL / 已登记模型名；用途按该供应商下任一模型
  // 命中即保留（无 models 数据时退回 row.modelKind）；状态看最近一次探测。
  const [search, setSearch] = useState('')
  const [kindFilter, setKindFilter] = useState<'all' | ModelKind>('all')
  const [statusFilter, setStatusFilter] = useState<'all' | 'verified' | 'failed' | 'unverified'>('all')
  const [editing, setEditing] = useState<Draft | null>(null)
  const [addingModel, setAddingModel] = useState<ModelDraft | null>(null)
  const [removingModel, setRemovingModel] = useState<ModelRemoval | null>(null)
  const [deletingProvider, setDeletingProvider] = useState<{
    row: ProviderRow
    busy: boolean
    error: string | null
  } | null>(null)
  const [modelBusy, setModelBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [probeResults, setProbeResults] = useState<Record<string, ProbeResponse>>({})
  const [probeErrors, setProbeErrors] = useState<Record<string, string>>({})
  const [draftProbe, setDraftProbe] = useState<ProbeResponse | null>(null)
  const [draftProbeError, setDraftProbeError] = useState<string | null>(null)
  const [testingTarget, setTestingTarget] = useState<string | null>(null)
  const [testingMode, setTestingMode] = useState<ProbeMode | null>(null)
  const [testingSince, setTestingSince] = useState<number | null>(null)
  const [testingElapsedMs, setTestingElapsedMs] = useState(0)

  const filteredProviders = useMemo(() => {
    const q = search.trim().toLowerCase()
    return providers.filter((row) => {
      if (kindFilter !== 'all') {
        const kinds = row.models?.length
          ? row.models.map((model) => model.modelKind)
          : [row.modelKind ?? ('chat' as ModelKind)]
        if (!kinds.includes(kindFilter)) return false
      }
      if (statusFilter !== 'all') {
        const hasProbe = Boolean(row.lastProbe)
        const ok = row.lastProbe?.ok === true
        if (statusFilter === 'verified' && !ok) return false
        if (statusFilter === 'failed' && (!hasProbe || ok)) return false
        if (statusFilter === 'unverified' && hasProbe) return false
      }
      if (q) {
        const haystack = [
          row.displayName,
          row.id,
          row.baseUrl,
          ...(row.models ?? []).map((model) => model.name),
        ].join('\n').toLowerCase()
        if (!haystack.includes(q)) return false
      }
      return true
    })
  }, [providers, search, kindFilter, statusFilter])

  useEffect(() => {
    if (testingSince === null) return
    const updateElapsed = () => setTestingElapsedMs(Date.now() - testingSince)
    updateElapsed()
    const timer = window.setInterval(updateElapsed, 100)
    return () => window.clearInterval(timer)
  }, [testingSince])

  function beginTesting(target: string, mode: ProbeMode): number {
    const startedAt = Date.now()
    setTestingTarget(target)
    setTestingMode(mode)
    setTestingSince(startedAt)
    setTestingElapsedMs(0)
    return startedAt
  }

  function endTesting(startedAt: number) {
    setTestingElapsedMs(Date.now() - startedAt)
    setTestingTarget(null)
    setTestingMode(null)
    setTestingSince(null)
  }

  function resetProbe() {
    setDraftProbe(null)
    setDraftProbeError(null)
  }

  function beginNew() {
    resetProbe()
    setEditing(newDraft())
  }

  function begin(row: ProviderRow) {
    resetProbe()
    setEditing(draftFromRow(row, defaultModels, presets, plans))
  }

  function beginAddModel(row: ProviderRow) {
    setAddingModel({ provider: row, modelName: '', modelKind: 'chat', error: null })
  }

  async function saveAddedModel() {
    if (!addingModel) return
    const modelName = addingModel.modelName.trim()
    if (!modelName) {
      setAddingModel({ ...addingModel, error: '模型名称不能为空' })
      return
    }
    setModelBusy(true)
    setAddingModel({ ...addingModel, error: null })
    try {
      await addProviderModel(addingModel.provider.id, {
        modelName,
        modelKind: addingModel.modelKind,
      })
      toast.success('模型测试通过，已加入供应商目录')
      setAddingModel(null)
      await onChanged()
    } catch (error) {
      const message = error instanceof Error ? error.message : '模型测试失败，未保存'
      setAddingModel({ ...addingModel, error: message })
      toast.error(message)
    } finally {
      setModelBusy(false)
    }
  }

  function beginRemoveModel(row: ProviderRow, model: { name: string; modelKind: ModelKind }) {
    setRemovingModel({
      provider: row,
      name: model.name,
      modelKind: model.modelKind,
      busy: false,
      error: null,
    })
  }

  async function confirmRemoveModel() {
    if (!removingModel) return
    setRemovingModel({ ...removingModel, busy: true, error: null })
    try {
      await removeProviderModel(removingModel.provider.id, removingModel.name)
      toast.success(`已移除模型 ${removingModel.name}`)
      setRemovingModel(null)
      await onChanged()
    } catch (error) {
      // 不弹 toast：409 的原因（被哪个角色占用）就在弹窗里，用户要能对着看
      const message = error instanceof Error ? error.message : '移除模型失败'
      setRemovingModel({ ...removingModel, busy: false, error: message })
    }
  }

  /** 供应商删除阻断原因；null 表示可删（2026-09-21 拍板：名下没有被角色
   *  绑定的模型即可删，未绑定模型与凭据由后端级联处理）。 */
  function providerDeleteBlockReason(row: ProviderRow): string | null {
    if (row.isBuiltin) return '内置供应商由代码目录管理，不能删除'
    const bound = (row.models ?? []).filter((model) => (model.usedByRoles?.length ?? 0) > 0)
    if (bound.length > 0) {
      const detail = bound
        .map((model) => `${model.name}（${model.usedByRoles!.join('、')}）`)
        .join('、')
      return `模型 ${detail} 正被角色占用，删除前请先在「模型角色」页改绑`
    }
    return null
  }

  function beginDeleteProvider(row: ProviderRow) {
    setDeletingProvider({ row, busy: false, error: null })
  }

  async function confirmDeleteProvider() {
    if (!deletingProvider) return
    setDeletingProvider({ ...deletingProvider, busy: true, error: null })
    try {
      const result = await deleteProvider(deletingProvider.row.id)
      const removed = Array.isArray(result.removedModels) ? result.removedModels.length : 0
      toast.success(`已删除供应商 ${deletingProvider.row.displayName}${removed ? `（连带移除 ${removed} 个未绑定模型）` : ''}`)
      setDeletingProvider(null)
      await onChanged()
    } catch (error) {
      // 不弹 toast：409 的原因（被哪个角色/专项占用）就在弹窗里，用户要能对着看
      const message = error instanceof Error ? error.message : '删除供应商失败'
      setDeletingProvider({ ...deletingProvider, busy: false, error: message })
    }
  }

  /** 切换计费计划（三选一）。
   *
   *  若当前地址是「上一个计划的预置」带出来的，必须一并清掉 —— 留着它
   *  正是本功能要消灭的事故：把火山引擎按量 `/api/v3` 用在 Coding Plan 上。
   *  手填的自定义地址则保留，不丢用户输入。
   */
  function applyPlan(nextPlan: PlanId | '') {
    setEditing((current) => {
      if (!current) return current
      const cameFromPreset = current.presetId !== null
      return {
        ...current,
        plan: nextPlan,
        presetId: null,
        baseUrl: cameFromPreset ? '' : current.baseUrl,
        billing: billingForPlan(plans, nextPlan) ?? current.billing,
      }
    })
    resetProbe()
  }

  /** 选中预置条目：一次回填地址、协议、显示名与推荐计费口径。 */
  function applyPreset(presetId: string) {
    setEditing((current) => {
      if (!current) return current
      if (presetId === '__custom') return { ...current, presetId: null }
      const preset = presets.find((item) => item.id === presetId)
      if (!preset) return current
      const planLabel = plans.find((item) => item.id === preset.plan)?.label ?? ''
      return {
        ...current,
        plan: preset.plan,
        presetId: preset.id,
        driver: preset.driver,
        baseUrl: preset.baseUrl,
        displayName: presetDisplayName(preset, planLabel),
        billing: billingForPlan(plans, preset.plan) ?? current.billing,
      }
    })
    resetProbe()
  }

  function draftPayload(draft: Draft) {
    return {
      driver: draft.driver,
      baseUrl: draft.baseUrl.trim(),
      modelName: draft.modelName.trim(),
      modelKind: draft.modelKind,
      apiKey: draft.apiKey?.trim() || undefined,
      networkScope: draft.networkScope,
    }
  }

  async function performDraftTest(mode: ProbeMode = 'fast'): Promise<ProbeResponse | null> {
    if (!editing) return null
    const baseUrl = editing.baseUrl.trim()
    const modelName = editing.modelName.trim()
    if (!baseUrl) {
      const message = 'Base URL 不能为空'
      setDraftProbeError(message)
      toast.error(message)
      return null
    }
    const placeholder = unresolvedPlaceholder(baseUrl)
    if (placeholder) {
      const message = `请先把 Base URL 里的 {${placeholder}} 替换为你的实际取值`
      setDraftProbeError(message)
      toast.error(message)
      return null
    }
    if (!modelName) {
      const message = '模型名称不能为空'
      setDraftProbeError(message)
      toast.error(message)
      return null
    }
    const canUseSavedCredential = Boolean(
      editing.id
      && editing.credentialConfigured
      && !editing.apiKey?.trim()
      && editing.originalBaseUrl === baseUrl
      && editing.originalModelName === modelName
      && editing.originalModelKind === editing.modelKind,
    )

    if (!canUseSavedCredential && !editing.apiKey?.trim() && editing.driver !== 'ollama') {
      const message = '请输入 API Key 后再测试'
      setDraftProbeError(message)
      toast.error(message)
      return null
    }

    const startedAt = beginTesting('draft', mode)
    setBusy(true)
    setDraftProbeError(null)
    try {
      const result = canUseSavedCredential && editing.id
        ? await verifyProvider(editing.id, { mode })
        : await verifyDraftProvider(draftPayload(editing), { mode })
      setDraftProbe(result)
      toast[result.ok ? 'success' : 'error'](result.summary || (result.ok ? '连接测试通过' : '连接测试失败'))
      return result
    } catch (error) {
      const message = error instanceof Error ? error.message : '连接测试失败'
      setDraftProbeError(`${message}（耗时 ${formatElapsed(Date.now() - startedAt)}）`)
      toast.error(message)
      return null
    } finally {
      endTesting(startedAt)
      setBusy(false)
    }
  }

  async function save() {
    if (!editing) return
    const modelName = editing.modelName.trim()
    if (!modelName) {
      const message = '模型名称不能为空'
      setDraftProbeError(message)
      toast.error(message)
      return
    }
    const placeholder = unresolvedPlaceholder(editing.baseUrl.trim())
    if (placeholder) {
      const message = `请先把 Base URL 里的 {${placeholder}} 替换为你的实际取值`
      setDraftProbeError(message)
      toast.error(message)
      return
    }

    const needsTest = !editing.id
      || editing.baseUrl.trim() !== editing.originalBaseUrl
      || editing.modelName.trim() !== editing.originalModelName
      || editing.modelKind !== editing.originalModelKind
      || Boolean(editing.apiKey?.trim())
    if (needsTest) {
      const result = await performDraftTest('fast')
      if (!result?.ok) return
    }

    setBusy(true)
    try {
      const common = {
        displayName: editing.displayName.trim(),
        driver: editing.driver,
        baseUrl: editing.baseUrl.trim(),
        modelName,
        modelKind: editing.modelKind,
        networkScope: editing.networkScope,
        billing: editing.billing,
        enabled: editing.enabled,
        apiKey: editing.apiKey?.trim() || undefined,
        clearApiKey: editing.clearApiKey,
      }
      if (editing.id) {
        await saveProvider(editing.id, common)
      } else {
        if (!common.apiKey && editing.driver !== 'ollama') {
          toast.error('新增供应商必须填写 API Key')
          return
        }
        await createProvider({
          ...common,
          apiKey: common.apiKey || '',
          modelKind: editing.modelKind,
        })
      }
      toast.success(editing.id ? '供应商配置已保存，密钥不会回显' : '测试通过，供应商已保存')
      setEditing(null)
      resetProbe()
      await onChanged()
    } catch (error) {
      const message = error instanceof Error ? error.message : '供应商保存失败'
      setDraftProbeError(message)
      toast.error(message)
    } finally {
      setBusy(false)
    }
  }

  async function testSaved(row: ProviderRow, mode: ProbeMode = 'fast') {
    const startedAt = beginTesting(row.id, mode)
    let testingEnded = false
    let measuredElapsedMs = 0
    const stopTesting = () => {
      if (testingEnded) return measuredElapsedMs
      testingEnded = true
      measuredElapsedMs = Date.now() - startedAt
      endTesting(startedAt)
      return measuredElapsedMs
    }
    setBusy(true)
    try {
      const result = await verifyProvider(row.id, { mode })
      stopTesting()
      setProbeResults((previous) => ({ ...previous, [row.id]: result }))
      setProbeErrors((previous) => ({ ...previous, [row.id]: '' }))
      toast[result.ok ? 'success' : 'error'](result.summary || (result.ok ? '厂商连通性通过' : '探测未通过'))
      await onChanged()
    } catch (error) {
      const elapsedMs = stopTesting()
      const message = error instanceof Error ? error.message : '供应商探测失败'
      setProbeErrors((previous) => ({ ...previous, [row.id]: `${message}（耗时 ${formatElapsed(elapsedMs)}）` }))
      toast.error(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-xs font-medium text-text-primary">供应商与密钥</h2>
          <p className="mt-1 text-[11px] text-text-muted">只列出已配置的供应商；每个供应商下平铺它已登记的模型。模型清单统一存于数据库；被角色占用的模型会就地标明原因，需先改绑才能移除。</p>
        </div>
        <div className="flex items-center gap-2">
          {source !== 'db' && <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">DB 未就绪</span>}
          {canAdmin && source === 'db' && <button onClick={beginNew} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-[11px] text-white hover:bg-accent/90"><Plus size={13} />新增供应商</button>}
        </div>
      </div>
        {/* B3：列表筛选/搜索。纯客户端过滤 —— 清单量级（供应商数）不需要服务端分页。 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2.5">
          <div className="relative min-w-[200px] flex-1">
            <Search size={12} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              data-testid="provider-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索显示名 / ID / 地址 / 模型名"
              className="w-full rounded-lg border border-black/10 bg-white py-1.5 pl-7 pr-2 text-[11px] text-text-primary placeholder:text-text-muted"
              autoComplete="off"
            />
          </div>
          <select
            data-testid="provider-kind-filter"
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value as typeof kindFilter)}
            className="rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-text-secondary"
            aria-label="按模型用途筛选"
          >
            <option value="all">全部用途</option>
            <option value="chat">文本</option>
            <option value="embedding">向量</option>
            <option value="rerank">重排</option>
            <option value="vision">视觉</option>
            <option value="speech">语音</option>
          </select>
          <select
            data-testid="provider-status-filter"
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}
            className="rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-text-secondary"
            aria-label="按验证状态筛选"
          >
            <option value="all">全部状态</option>
            <option value="verified">已验证</option>
            <option value="failed">探测失败</option>
            <option value="unverified">未验证</option>
          </select>
          <span data-testid="provider-filter-count" className="shrink-0 font-mono text-[10px] text-text-muted">
            {filteredProviders.length}/{providers.length} 家
          </span>
        </div>
        <div className="divide-y divide-slate-100">
          {filteredProviders.map((row) => {
            const liveResult = probeResults[row.id]
            const liveError = probeErrors[row.id]
            const isTesting = testingTarget === row.id
            const visibleModels: NonNullable<ProviderRow['models']> = row.models?.length
              ? row.models
              : [{
                  name: row.modelName || defaultModels[row.id] || '—',
                  modelKind: (row.modelKind || 'chat') as ModelKind,
                  source: 'user' as const,
                  usedByRoles: [],
                }]
            return (
              <div key={row.id} data-testid="provider-card" className="px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-text-primary">{row.displayName}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-muted">{row.isBuiltin ? '内置' : '自建'}</span>
                      <span className="rounded bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent">{row.driver}</span>
                      <span className="text-[10px] text-text-muted">{row.networkScope === 'private' ? '内网白名单' : '公网'} · {row.billing}</span>
                    </div>
                    <div className="mt-1.5 truncate font-mono text-[10px] text-text-secondary">{row.baseUrl || '跟随环境变量 / 默认地址'}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-muted">
                      <span className="font-mono">{row.id}</span>
                      {row.credential.configured
                        ? <span className="flex items-center gap-1 text-emerald-700"><LockKeyhole size={11} />密钥已配置 <span className="font-mono">{maskSecret(row.credential.last4, null) || '****'}</span></span>
                        : <span>未托管密钥（可走环境变量）</span>}
                      {row.credential.fingerprint && <span className="font-mono">指纹 {row.credential.fingerprint}</span>}
                      {isTesting
                        ? <span className="text-accent">正在{probeModeLabel(testingMode || 'fast')} · 已耗时 {formatLiveElapsed(testingElapsedMs)}</span>
                        : row.lastProbe
                          ? <span className={row.lastProbe.ok ? 'flex items-center gap-1 text-emerald-700' : 'text-red-700'}>
                              {row.lastProbe.ok && <CheckCircle2 size={11} />}
                              {row.lastProbe.ok ? '已通过' : `卡在 ${row.lastProbe.worstGrade || '未知'}`}
                            </span>
                          : <span>未验证</span>}
                    </div>
                  </div>
                  <div className="flex flex-shrink-0 flex-wrap justify-end gap-1.5">
                    <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'fast') }} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-text-secondary disabled:opacity-40"><FlaskConical size={12} />{isTesting && testingMode === 'fast' ? `测试中 ${formatLiveElapsed(testingElapsedMs)}` : '测试'}</button>
                    <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'full') }} className="flex items-center gap-1 rounded-lg border border-accent/20 px-2.5 py-1.5 text-[11px] text-accent disabled:opacity-40">{isTesting && testingMode === 'full' ? `完整测试中 ${formatLiveElapsed(testingElapsedMs)}` : '完整测试'}</button>
                    {canAdmin && source === 'db' && <><button onClick={() => beginAddModel(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Plus size={12} />新增模型</button><button onClick={() => begin(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Edit3 size={12} />编辑</button></>}
                    {canAdmin && source === 'db' && !row.isBuiltin && (() => {
                      const deleteBlock = providerDeleteBlockReason(row)
                      return (
                        <button
                          disabled={Boolean(deleteBlock)}
                          title={deleteBlock ?? '删除该供应商及其未被绑定的模型与托管密钥'}
                          aria-label={`删除供应商 ${row.displayName}`}
                          onClick={() => beginDeleteProvider(row)}
                          className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
                        >
                          <Trash2 size={12} />删除
                        </button>
                      )
                    })()}
                  </div>
                </div>

                <div className="mt-3 overflow-hidden rounded-lg border border-slate-100">
                  {visibleModels.map((model) => {
                    const blockReason = modelRemovalBlockReason(model)
                    return (
                      <div key={model.name} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-50 px-3 py-2 last:border-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${modelKindBadgeClass(model.modelKind)}`}>{modelKindShortLabel(model.modelKind)}</span>
                          <span className="truncate font-mono text-[11px] text-text-secondary">{model.name}</span>
                          {model.source !== 'user' && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-muted">内置</span>}
                        </div>
                        <div className="flex flex-shrink-0 items-center gap-2">
                          {/* B3：角色占用徽标 —— 每个占用角色一枚，可点跳「模型角色」页改绑。 */}
                          {(model.usedByRoles?.length ?? 0) > 0 && (
                            <span className="flex flex-wrap items-center gap-1">
                              {model.usedByRoles!.map((role) => (
                                <button
                                  key={role}
                                  type="button"
                                  data-testid={`used-by-role-${role}`}
                                  title={onGoToRoles ? `点击前往「模型角色」页改绑 ${role}` : undefined}
                                  disabled={!onGoToRoles}
                                  onClick={() => onGoToRoles?.(role)}
                                  className="flex items-center gap-0.5 rounded bg-purple-50 px-1.5 py-0.5 text-[10px] text-purple-700 hover:bg-purple-100 disabled:cursor-default disabled:hover:bg-purple-50"
                                >
                                  <SlidersHorizontal size={10} />{role}
                                </button>
                              ))}
                              <span className="text-[10px] text-text-muted">使用中，移除前需先改绑</span>
                            </span>
                          )}
                          {canAdmin && source === 'db' && <button disabled={Boolean(blockReason)} title={blockReason ?? undefined} aria-label={`移除模型 ${model.name}`} onClick={() => beginRemoveModel(row, model)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2 py-1 text-[11px] text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"><Trash2 size={11} />移除</button>}
                        </div>
                      </div>
                    )
                  })}
                </div>

                {(liveResult || liveError || (!isTesting && row.lastProbe && !row.lastProbe.ok)) && (
                  <div className="mt-2 space-y-2">
                    {liveResult && <ProbeResultDetails result={liveResult} />}
                    {liveError && <ErrorNote message={liveError} />}
                    {!liveResult && !liveError && !isTesting && row.lastProbe && !row.lastProbe.ok && row.lastProbe.worstGrade && (
                      <div className="rounded-lg border border-red-100 bg-red-50/60 px-2.5 py-2 text-[10px] text-red-700">建议：{probeFallbackSummary(row.lastProbe.worstGrade, 'fail')}</div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {!providers.length && <div className="px-4 py-10 text-center text-xs text-text-muted">暂无供应商配置</div>}
          {Boolean(providers.length) && !filteredProviders.length && (
            <div className="px-4 py-10 text-center text-xs text-text-muted">没有匹配当前筛选条件的供应商 —— 试试清空搜索词或放宽用途/状态。</div>
          )}
        </div>

      {editing && <ProviderEditor
        draft={editing}
        providers={providers}
        plans={plans}
        presets={presets}
        presetsLoading={presetsLoading}
        busy={busy}
        setDraft={setEditing}
        onPlanChange={applyPlan}
        onPresetChange={applyPreset}
        probeResult={draftProbe}
        probeError={draftProbeError}
        testingMode={testingTarget === 'draft' ? testingMode : null}
        testingElapsedMs={testingElapsedMs}
        onSave={() => { void save() }}
        onTest={(mode) => { void performDraftTest(mode) }}
        onCancel={() => { setEditing(null); resetProbe() }}
      />}
      {addingModel && <ProviderModelEditor
        draft={addingModel}
        busy={modelBusy}
        setDraft={setAddingModel}
        onSave={() => { void saveAddedModel() }}
        onCancel={() => setAddingModel(null)}
      />}
      {removingModel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={`移除模型 ${removingModel.name}`}>
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-text-primary"><Trash2 size={15} className="text-red-700" />移除模型</div>
                <p className="mt-1 text-[11px] text-text-muted">供应商：{removingModel.provider.displayName}</p>
              </div>
              <button onClick={() => setRemovingModel(null)} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
            </div>
            <div className="mt-5 space-y-3">
              <div className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${modelKindBadgeClass(removingModel.modelKind)}`}>{modelKindShortLabel(removingModel.modelKind)}</span>
                <span className="font-mono text-xs text-text-secondary">{removingModel.name}</span>
              </div>
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">移除后该模型从供应商目录消失。若仍被角色或价格表引用，后端会拒绝并说明原因，不会留下悬空引用。</div>
              {removingModel.error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{removingModel.error}</div>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button disabled={removingModel.busy} onClick={() => setRemovingModel(null)} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
              <button disabled={removingModel.busy} onClick={() => { void confirmRemoveModel() }} className="flex items-center gap-1 rounded-lg bg-red-700 px-3 py-2 text-xs text-white disabled:opacity-50"><Trash2 size={13} />{removingModel.busy ? '移除中…' : '确认移除'}</button>
            </div>
          </div>
        </div>
      )}
      {deletingProvider && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={`删除供应商 ${deletingProvider.row.displayName}`}>
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-text-primary"><Trash2 size={15} className="text-red-700" />删除供应商</div>
                <p className="mt-1 font-mono text-[11px] text-text-muted">{deletingProvider.row.id}</p>
              </div>
              <button onClick={() => setDeletingProvider(null)} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
            </div>
            <div className="mt-5 space-y-3">
              <div className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                <span className="text-sm font-medium text-text-primary">{deletingProvider.row.displayName}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-muted">{deletingProvider.row.isBuiltin ? '内置' : '自建'}</span>
              </div>
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                将删除该供应商、其托管密钥及名下 {(deletingProvider.row.models ?? []).length} 个未被角色绑定的模型。若名下模型仍被角色/专项通道/价格表引用，后端会拒绝并说明原因，不会留下悬空引用。
              </div>
              {deletingProvider.error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{deletingProvider.error}</div>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button disabled={deletingProvider.busy} onClick={() => setDeletingProvider(null)} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
              <button disabled={deletingProvider.busy} onClick={() => { void confirmDeleteProvider() }} className="flex items-center gap-1 rounded-lg bg-red-700 px-3 py-2 text-xs text-white disabled:opacity-50"><Trash2 size={13} />{deletingProvider.busy ? '删除中…' : '确认删除'}</button>
            </div>
          </div>
        </div>
      )}
      </section>
    </div>
  )
}
