'use client'

import { Fragment, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleAlert, Edit3, FlaskConical, Save, SlidersHorizontal, X } from 'lucide-react'
import type { ModelCatalogEntry } from '@/api/modelConfig'
import { checkModelHealth, saveModelRole, saveModelRolePolicy } from '@/api/modelConfig'
import type {
  FailurePolicy,
  ModelHealthStatus,
  ModelKind,
  ModelOption,
  RoleBinding,
  RolePolicy,
  SelectableVerdict,
} from '@/types/modelConfig'
import {
  failurePolicyLabel,
  healthStatusLabel,
  healthStatusTone,
  OTHER_ROLE_GROUP_ID,
  ROLE_GROUPS,
  isModelSelectable,
  modelKindLabel,
  roleLabel,
  roleModelKind,
  sourceLabel,
} from '@/types/modelConfig'
import { formatRelative } from '@/types/trace'
import { useToast } from '@/components/shared/Toast'
import EmptyState from '@/components/shared/EmptyState'

interface Props {
  roles: RoleRow[]
  catalog: ModelCatalogEntry[]
  canAdmin: boolean
  onSaved: () => Promise<unknown>
  /** B3：从供应商页角色徽标跳转而来 —— 高亮该角色行并滚动定位，直到用户离开。 */
  highlightRole?: string | null
}

/**
 * 厂商中文名由后端下发（`PROVIDERS[].label`，db 自建供应商回落 provider 代码）。
 * ⚠️ 字段尚未并入 `RoleBinding` 接口（types/modelConfig.ts 正被其他会话改动），
 * 待其落定后把 `providerLabel` 移进接口并删除此交叉类型。
 */
type RoleRow = RoleBinding & { providerLabel?: string | null }

/** 生效来源 → 徽章配色（§6：DB 覆盖蓝 / 环境变量灰 / 跟随父角色紫 / 代码默认浅灰）。 */
const SOURCE_BADGE: Record<string, string> = {
  db: 'border-blue-200 bg-blue-50 text-blue-700',
  env: 'border-slate-200 bg-slate-100 text-text-secondary',
  inherit: 'border-purple-200 bg-purple-50 text-purple-700',
  default: 'border-slate-200 bg-white text-text-muted',
}

/** 未知来源不求鲜艳，只求不冒充「代码默认」。 */
const SOURCE_BADGE_FALLBACK = 'border-slate-300 bg-slate-50 text-text-muted'

/** failurePolicy 全枚举（后端 CHECK 约束镜像）。 */
const FAILURE_POLICIES: FailurePolicy[] = [
  'fallback', 'skip', 'fail_fast', 'template_response', 'mark_failed',
]

/** 各角色默认策略（后端 ROLE_RUNTIME_DEFAULTS 的展示镜像，仅用于弹窗预填）。 */
const POLICY_PRESETS: Record<string, Pick<RolePolicy, 'fallbackModel' | 'timeoutSeconds' | 'maxRetries' | 'failurePolicy'>> = {
  main: { fallbackModel: '', timeoutSeconds: 30, maxRetries: 1, failurePolicy: 'fallback' },
  tool_selector: { fallbackModel: '', timeoutSeconds: 10, maxRetries: 0, failurePolicy: 'skip' },
  fallback: { fallbackModel: '', timeoutSeconds: 30, maxRetries: 0, failurePolicy: 'fail_fast' },
  doc: { fallbackModel: '', timeoutSeconds: 20, maxRetries: 1, failurePolicy: 'skip' },
  metadata_extract: { fallbackModel: '', timeoutSeconds: 20, maxRetries: 1, failurePolicy: 'skip' },
  question_gen: { fallbackModel: '', timeoutSeconds: 20, maxRetries: 1, failurePolicy: 'skip' },
  table_describe: { fallbackModel: '', timeoutSeconds: 20, maxRetries: 1, failurePolicy: 'skip' },
  ocr: { fallbackModel: '', timeoutSeconds: 120, maxRetries: 0, failurePolicy: 'mark_failed' },
  embedding: { fallbackModel: '', timeoutSeconds: 15, maxRetries: 1, failurePolicy: 'fail_fast' },
  rerank: { fallbackModel: '', timeoutSeconds: 15, maxRetries: 1, failurePolicy: 'skip' },
  eval_gen: { fallbackModel: '', timeoutSeconds: 30, maxRetries: 1, failurePolicy: 'fail_fast' },
}

/**
 * 角色的可用性判定。`modelName` 传行内生效值 = 展示态，传下拉选中值 = 编辑态预览。
 *
 * 后端对**当前生效值**的结论优先 —— 它能看到前端模型目录里没有的信息（供应商 Key 状态等）。
 * 编辑态则一律以目录项为准现算，这样用户在下拉里换模型时，可用性列会立刻跟着变，
 * 而不是等他保存完才发现挑了个不可用的模型。
 */
function roleVerdict(
  row: RoleRow,
  modelName: string,
  entry: ModelCatalogEntry | undefined,
  expectedKind: ModelKind,
): SelectableVerdict {
  if (!modelName) return { selectable: false, reason: '未配置' }
  const isCurrent = modelName === row.effectiveModel
  if (isCurrent && row.availabilityReason) {
    return { selectable: false, reason: row.availabilityReason }
  }
  const option: ModelOption = {
    name: modelName,
    provider: entry?.provider ?? (isCurrent ? row.provider : null),
    modelKind: entry?.modelKind,
    registered: isCurrent ? row.registered : Boolean(entry),
    missingKeyEnv: isCurrent ? row.missingKeyEnv : null,
    availabilityReason: entry?.availabilityReason ?? null,
  }
  return isModelSelectable(option, expectedKind)
}

/** 索引兼容告警条目数（embedding 角色行）：mismatch 或 rebuild_required 的 collection 数。 */
function incompatibleIndexes(row: RoleRow) {
  return (row.indexCompat ?? []).filter(
    (item) => item.mismatch || item.status === 'rebuild_required',
  )
}

export default function RoleBindingsTab({ roles, catalog, canAdmin, onSaved, highlightRole }: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmRow, setConfirmRow] = useState<RoleRow | null>(null)
  const [onlyProblem, setOnlyProblem] = useState(false)
  // 手动健康探测：同一时刻只允许一个角色行在探（探测本身是同步极低成本调用）
  const [checkingHealthRole, setCheckingHealthRole] = useState<string | null>(null)
  // 策略编辑态：editingPolicy = 角色名；表单值独立保存
  const [editingPolicy, setEditingPolicy] = useState<RoleRow | null>(null)
  const [policyForm, setPolicyForm] = useState({
    fallbackModel: '',
    timeoutSeconds: 30,
    maxRetries: 0,
    failurePolicy: 'fail_fast' as FailurePolicy,
  })
  const byName = useMemo(() => new Map(catalog.map((item) => [item.name, item])), [catalog])
  const byRole = useMemo(() => new Map(roles.map((item) => [item.role, item])), [roles])

  // 高亮定位：等表格渲染完（roles 数据到达后）再滚动，避免对空 DOM 查询。
  useEffect(() => {
    if (!highlightRole) return
    const node = document.querySelector(`[data-role-row="${highlightRole}"]`)
    node?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [highlightRole, roles])

  const unavailableCount = useMemo(
    () => roles.filter((row) => !roleVerdict(row, row.effectiveModel, byName.get(row.effectiveModel), roleModelKind(row.role)).selectable).length,
    [roles, byName],
  )

  function begin(row: RoleRow) {
    setEditing(row.role)
    setValue(row.effectiveModel)
  }

  function cancel() {
    setEditing(null)
    setValue('')
  }

  /** 手动触发当前绑定模型的健康探测（beat 自动扫描之外的手动兜底）。
   *  结果由后端写入 llm_model_health 缓存，onSaved() 拉回后本行即时刷新。 */
  async function manualHealthCheck(row: RoleRow) {
    const model = (row.effectiveModel || '').trim()
    if (!model) {
      toast.error('该角色未绑定模型，无可探测对象')
      return
    }
    setCheckingHealthRole(row.role)
    try {
      const result = await checkModelHealth(model)
      const ms = result.latencyMs != null ? ` · ${result.latencyMs}ms` : ''
      toast.success(`手动探测完成：${healthStatusLabel(result.status as ModelHealthStatus)}${ms}`)
      await onSaved()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '手动探测失败')
    } finally {
      setCheckingHealthRole(null)
    }
  }

  function beginPolicy(row: RoleRow) {
    const preset = row.policy ?? {
      role: row.role,
      source: 'default' as const,
      ...(POLICY_PRESETS[row.role] ?? {
        fallbackModel: '', timeoutSeconds: 10, maxRetries: 0, failurePolicy: 'fail_fast' as FailurePolicy,
      }),
    }
    setEditingPolicy(row)
    setPolicyForm({
      fallbackModel: preset.fallbackModel ?? '',
      timeoutSeconds: preset.timeoutSeconds,
      maxRetries: preset.maxRetries,
      failurePolicy: preset.failurePolicy,
    })
  }

  async function persistPolicy(row: RoleRow) {
    setBusy(true)
    try {
      await saveModelRolePolicy(row.role, policyForm)
      toast.success('运行策略已保存，下一次对应链路调用生效')
      setEditingPolicy(null)
      await onSaved()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '运行策略保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function persist(row: RoleRow) {
    setBusy(true)
    try {
      await saveModelRole(row.role, value.trim())
      if (row.requiresReindex) {
        toast.success('已保存；重建索引时会读取新模型，当前索引不会自动切换')
      } else {
        toast.success('模型角色已保存，下一次对应链路调用生效')
      }
      cancel()
      setConfirmRow(null)
      await onSaved()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '模型角色保存失败')
    } finally {
      setBusy(false)
    }
  }

  function submit(row: RoleRow) {
    const next = value.trim()
    if (!next && row.role !== 'eval_gen') {
      toast.error('模型名不能为空')
      return
    }
    const expectedKind = roleModelKind(row.role)
    const picked = next ? byName.get(next) : undefined
    if (next && !(picked && (picked.modelKind || 'chat') === expectedKind)) {
      toast.error(`请从已配置的${modelKindLabel(expectedKind)}中选择`)
      return
    }
    // 向量化模型换值会换掉语义空间，必须走带代价说明的确认弹窗（§6：不用 window.confirm）
    if (row.requiresReindex && next !== row.effectiveModel) {
      setConfirmRow(row)
      return
    }
    void persist(row)
  }

  const groups = useMemo(() => {
    const visible = (list: RoleRow[]) =>
      list.filter((row) => !onlyProblem || !roleVerdict(
        row, row.effectiveModel, byName.get(row.effectiveModel), roleModelKind(row.role),
      ).selectable)
    const built = ROLE_GROUPS.map((group) => ({
      id: group.id,
      label: group.label,
      hint: group.hint,
      rows: visible(group.roles
        .map((role) => byRole.get(role))
        .filter((row): row is RoleRow => Boolean(row))),
    }))
    // 后端新增角色时不该从界面上消失：未登记进分组的一律收进末位「其他」
    const covered = new Set(ROLE_GROUPS.flatMap((group) => group.roles))
    const rest = visible(roles.filter((row) => !covered.has(row.role)))
    if (rest.length) {
      built.push({
        id: OTHER_ROLE_GROUP_ID,
        label: '其他角色',
        hint: '尚未归入任何链路，请同步分组与中文名',
        rows: rest,
      })
    }
    return built.filter((group) => group.rows.length > 0)
  }, [roles, byRole, byName, onlyProblem])

  // 索引兼容全局告警（embedding 换模型后既有索引进入待重建态）
  const embeddingRow = byRole.get('embedding')
  const badIndexes = embeddingRow ? incompatibleIndexes(embeddingRow) : []

  return (
    <section className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
      {badIndexes.length > 0 && (
        <div
          data-testid="index-compat-warning"
          className="flex items-start gap-2 border-b border-amber-200 bg-amber-50 px-4 py-3 text-[11px] text-amber-800"
        >
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />
          <div>
            <div className="font-medium">当前向量化模型与现有知识库索引不一致，请重建索引后再使用。</div>
            <div className="mt-0.5 font-mono text-[10px] text-amber-700">
              {badIndexes.map((item) => (
                <span key={item.collection} className="mr-3">
                  {item.collection}: {item.embeddingModel || '（未记录）'} → {item.runtimeModel}（{item.status}）
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-xs font-medium text-text-primary">角色绑定</h2>
          <p className="mt-1 text-[11px] text-text-muted">
            共 {roles.length} 个角色，按业务链路分组。角色决定业务链路使用哪个模型；非索引链路会在刷新周期内读取新值，
            向量化与重排模型必须先重建索引。健康状态来自后台周期探测（默认 5 分钟），页面不做在线探测。
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-text-secondary">
          <input
            type="checkbox"
            checked={onlyProblem}
            onChange={(event) => setOnlyProblem(event.target.checked)}
            data-testid="only-problem-toggle"
            className="accent-accent"
          />
          只看不可用{unavailableCount ? `（${unavailableCount}）` : ''}
        </label>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-xs">
          <colgroup>
            <col className="w-[17%]" />
            <col className="w-[24%]" />
            <col className="w-[22%]" />
            <col className="w-[12%]" />
            <col className="w-[13%]" />
            <col className="w-[12%]" />
          </colgroup>
          <thead>
            <tr className="border-b border-slate-100 text-[10px] text-text-muted">
              <th scope="col" className="px-4 py-3 font-normal">业务角色</th>
              <th scope="col" className="px-4 py-3 font-normal">当前生效模型</th>
              <th scope="col" className="px-4 py-3 font-normal">来源</th>
              <th scope="col" className="px-4 py-3 font-normal">健康状态</th>
              <th scope="col" className="px-4 py-3 font-normal">可用性 / 风险</th>
              <th scope="col" className="px-4 py-3 text-right font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={group.id}>
                <tr className="bg-slate-50/80">
                  <td colSpan={6} className="px-4 py-2">
                    <span className="text-[11px] font-medium text-text-secondary">{group.label}</span>
                    <span className="ml-2 text-[10px] text-text-muted">{group.hint} · {group.rows.length} 个角色</span>
                  </td>
                </tr>
                {group.rows.map((row) => {
                  const expectedKind = roleModelKind(row.role)
                  const isEditing = editing === row.role
                  const isHighlighted = highlightRole === row.role
                  const compatibleModels = catalog.filter((item) => (item.modelKind || 'chat') === expectedKind)
                  const currentEntry = byName.get(row.effectiveModel)
                  const shownModel = isEditing ? value : row.effectiveModel
                  const verdict = roleVerdict(row, shownModel, byName.get(shownModel), expectedKind)
                  const usable = verdict.selectable
                  const sourceTone = SOURCE_BADGE[row.source] ?? SOURCE_BADGE_FALLBACK
                  const isInherit = row.source === 'inherit'
                  const policy = row.policy
                  const health = row.health
                  return (
                    <tr
                      key={row.role}
                      data-role-row={row.role}
                      className={`border-b border-slate-50 align-top last:border-0 ${isEditing ? 'bg-accent/5' : isHighlighted ? 'bg-amber-50 ring-1 ring-inset ring-amber-200' : ''}`}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-text-primary">{roleLabel(row.role)}</div>
                        <div className="mt-0.5 font-mono text-[10px] text-text-muted">{row.role}</div>
                      </td>

                      <td className="px-4 py-3">
                        {isEditing ? (
                          <>
                            <select
                              value={value}
                              onChange={(event) => setValue(event.target.value)}
                              aria-label={`${roleLabel(row.role)}的生效模型`}
                              data-testid={`role-model-select-${row.role}`}
                              className="w-full rounded-lg border border-accent/40 bg-white px-2 py-1.5 font-mono text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20"
                            >
                              {row.role === 'eval_gen' && <option value="">未配置（停用评测生成）</option>}
                              {row.role !== 'eval_gen' && !compatibleModels.length && !row.effectiveModel && (
                                <option value="" disabled>暂无已配置的{modelKindLabel(expectedKind)}，请先到供应商页面新增模型</option>
                              )}
                              {row.effectiveModel && !compatibleModels.some((item) => item.name === row.effectiveModel) && (
                                <option value={row.effectiveModel} disabled>当前值：{row.effectiveModel}（未登记或用途不匹配）</option>
                              )}
                              {compatibleModels.map((item) => {
                                const optionVerdict = isModelSelectable({
                                  name: item.name,
                                  provider: item.provider,
                                  modelKind: item.modelKind,
                                  registered: true,
                                  missingKeyEnv: null,
                                  availabilityReason: item.availabilityReason,
                                }, expectedKind)
                                return (
                                  <option key={item.name} value={item.name} disabled={!optionVerdict.selectable}>
                                    {item.name}{optionVerdict.reason ? `（${optionVerdict.reason}）` : ''}
                                  </option>
                                )
                              })}
                            </select>
                            <div className="mt-1 text-[10px] text-text-muted">
                              只能从已配置的{modelKindLabel(expectedKind)}中选择；新增模型请先到供应商与密钥页面登记并测试。
                            </div>
                          </>
                        ) : (
                          <div className="flex flex-wrap items-center gap-1.5">
                            {row.providerLabel && (
                              <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
                                {row.providerLabel}
                              </span>
                            )}
                            <span className={usable ? 'font-mono text-text-primary' : 'font-mono text-red-700'}>
                              {row.effectiveModel || '—'}
                            </span>
                            {currentEntry?.modelKind && (
                              <span className="rounded bg-slate-100 px-1.5 py-0.5 font-sans text-[10px] text-text-muted">
                                {modelKindLabel(currentEntry.modelKind)}
                              </span>
                            )}
                            {!row.providerLabel && row.provider && (
                              <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
                                {row.provider}
                              </span>
                            )}
                          </div>
                        )}
                      </td>

                      <td className="px-4 py-3">
                        {isEditing ? (
                          <span className="text-[10px] text-text-muted">沿用当前来源，保存后更新</span>
                        ) : (
                          <>
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className={`rounded-full border px-2 py-0.5 text-[10px] ${sourceTone}`}>
                                {sourceLabel(row.source, row.inheritedFrom, isInherit ? row.effectiveModel : null)}
                              </span>
                              {(row.updatedBy || row.updatedAt) && (
                                <span className="text-[10px] text-text-muted">
                                  {row.updatedBy ? `最后由 ${row.updatedBy}` : '最后修改'}
                                  {row.updatedAt ? ` · ${formatRelative(row.updatedAt)}` : ''}
                                </span>
                              )}
                            </div>
                            {isInherit ? (
                              // 「继承 main」的用户语义：不是「配置值为空」，而是运行时跟随主模型
                              <div
                                className="mt-1 text-[10px] text-text-muted"
                                title="当前角色未配置独立模型，因此运行时跟随 main 角色的当前绑定模型。"
                                data-testid={`inherit-hint-${row.role}`}
                              >
                                来源：继承主问答模型 · 当前生效 {row.effectiveModel || '—'}
                              </div>
                            ) : (row.literalValue && row.literalValue !== row.effectiveModel) ? (
                              <div className="mt-1 text-[10px] text-text-muted">配置值：{row.literalValue}</div>
                            ) : null}
                          </>
                        )}
                      </td>

                      <td className="px-4 py-3" data-testid={`role-health-${row.role}`}>
                        {!health ? (
                          <span className="text-[10px] text-text-muted">未探测</span>
                        ) : (
                          <div>
                            <span className={`inline-block rounded-full border px-2 py-0.5 text-[10px] ${healthStatusTone(health.status)}`}>
                              {healthStatusLabel(health.status)}
                            </span>
                            <div className="mt-1 text-[10px] text-text-muted">
                              {health.lastLatencyMs != null ? `${health.lastLatencyMs}ms` : ''}
                              {health.lastCheckedAt ? ` · ${formatRelative(health.lastCheckedAt)}` : ''}
                              {health.consecutiveFailures > 0 ? ` · 连续失败 ${health.consecutiveFailures}` : ''}
                            </div>
                          </div>
                        )}
                        {/* 手动测试：beat 300s 自动扫描之外的手动兜底，点一次真探一次 */}
                        {canAdmin && row.effectiveModel && (
                          <button
                            type="button"
                            data-testid={`role-health-check-${row.role}`}
                            disabled={checkingHealthRole === row.role}
                            onClick={() => { void manualHealthCheck(row) }}
                            aria-label={`手动测试 ${roleLabel(row.role)} 的健康`}
                            className="mt-1.5 flex items-center gap-1 rounded-lg border border-black/10 px-2 py-1 text-[10px] text-text-secondary hover:bg-slate-50 disabled:opacity-40"
                          >
                            <FlaskConical size={10} />
                            {checkingHealthRole === row.role ? '探测中…' : '手动测试'}
                          </button>
                        )}
                      </td>

                      <td className="px-4 py-3" data-testid={`role-availability-${row.role}`}>
                        <div className="flex items-start gap-1.5">
                          {usable
                            ? <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-emerald-600" />
                            : <CircleAlert size={13} className="mt-0.5 shrink-0 text-red-600" />}
                          <div className="min-w-0">
                            <div className={usable ? 'text-emerald-700' : 'text-red-700'}>
                              {usable ? '可用' : verdict.reason || '不可用'}
                            </div>
                            {isEditing && <div className="mt-0.5 text-[10px] text-text-muted">按所选模型实时判定</div>}
                            {row.requiresReindex && <div className="mt-0.5 text-[10px] text-amber-700">变更需重建索引</div>}
                            {policy && (
                              <div className="mt-0.5 text-[10px] text-text-muted" title={`超时 ${policy.timeoutSeconds}s · 重试 ${policy.maxRetries} 次`}>
                                {failurePolicyLabel(policy.failurePolicy)}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>

                      <td className="px-4 py-3 text-right">
                        {canAdmin && (isEditing ? (
                          <div className="flex justify-end gap-1.5">
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => submit(row)}
                              aria-label={`保存 ${roleLabel(row.role)}`}
                              className="flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1.5 text-[11px] text-white disabled:opacity-50"
                            >
                              <Save size={12} />保存
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={cancel}
                              aria-label="取消编辑"
                              className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-text-secondary"
                            >
                              <X size={12} />取消
                            </button>
                          </div>
                        ) : (
                          <div className="flex justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => beginPolicy(row)}
                              disabled={row.policyEnforced === false}
                              title={
                                row.policyEnforced === false
                                  ? '该角色的运行策略尚未接入运行时（当前仅「工具选择」生效），编辑不会影响行为'
                                  : undefined
                              }
                              aria-label={`修改 ${roleLabel(row.role)} 的运行策略`}
                              data-testid={`role-policy-${row.role}`}
                              className={`flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-[11px] ${
                                row.policyEnforced === false
                                  ? 'cursor-not-allowed border-black/5 text-text-muted/50'
                                  : 'border-black/10 text-text-secondary hover:bg-accent/5'
                              }`}
                            >
                              <SlidersHorizontal size={12} />策略
                            </button>
                            <button
                              type="button"
                              onClick={() => begin(row)}
                              aria-label={`修改 ${roleLabel(row.role)}`}
                              className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"
                            >
                              <Edit3 size={12} />修改
                            </button>
                          </div>
                        ))}
                      </td>
                    </tr>
                  )
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
        {!groups.length && (
          <div className="px-4 py-8">
            {onlyProblem ? (
              <EmptyState
                kind="no_data"
                title="所有角色当前都可用"
                description="「只看不可用」没有筛出任何角色，关闭筛选可查看全部角色绑定。"
                onAction={() => setOnlyProblem(false)}
                actionLabel="查看全部角色"
              />
            ) : (
              <EmptyState
                kind="no_data"
                title="未登记任何模型角色"
                description="角色清单来自后端预置目录；若持续为空，请确认 /sys/model-roles 接口是否正常返回。"
                onAction={onSaved}
                actionLabel="重新加载"
              />
            )}
          </div>
        )}
      </div>

      {confirmRow && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="确认修改向量化模型">
          <div className="w-full max-w-md overflow-hidden rounded-xl bg-white shadow-2xl">
            <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
              <AlertTriangle size={15} className="text-amber-600" />
              <div className="text-sm font-medium text-text-primary">修改{roleLabel(confirmRow.role)}</div>
            </div>
            <div className="space-y-3 px-5 py-4 text-xs text-text-secondary">
              <p>
                已有索引与查询向量不在同一空间，<strong className="text-red-700">检索结果会不可用</strong>，
                必须全量重建索引（耗时较长）。
              </p>
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 font-mono text-[11px] text-text-primary">
                {confirmRow.effectiveModel || '—'} → {value.trim() || '（空）'}
              </div>
              <p className="text-[11px] text-text-muted">
                不同向量模型通常不共享同一语义空间。保存后现有向量索引将进入「待重建」状态，
                完成重建前系统会拒绝按新模型查询旧索引（INDEX_EMBEDDING_MISMATCH），不会静默降级。
              </p>
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
              <button
                type="button"
                onClick={() => setConfirmRow(null)}
                disabled={busy}
                className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void persist(confirmRow)}
                disabled={busy}
                className="rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"
              >
                {busy ? '保存中…' : '保存配置'}
              </button>
            </div>
          </div>
        </div>
      )}

      {editingPolicy && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="编辑运行策略">
          <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl bg-white shadow-2xl" data-testid="policy-modal">
            <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
              <SlidersHorizontal size={15} className="text-accent" />
              <div className="text-sm font-medium text-text-primary">
                {roleLabel(editingPolicy.role)} · 运行策略
              </div>
            </div>
            <div className="space-y-4 px-5 py-4 text-xs text-text-secondary">
              <p className="text-[11px] text-text-muted">
                策略决定该角色模型失败时怎么兜底、单次调用超时多久。所有角色的
                「超时 ×（重试 + 1）」预算封顶 60s（OCR 除外），防止一次请求拖满三分钟。
              </p>

              {/* 能力校验面板 */}
              <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                <div className="text-[11px] font-medium text-text-secondary">能力要求</div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
                  <span>要求：{modelKindLabel(roleModelKind(editingPolicy.role))}</span>
                  <span className="text-text-muted">当前生效模型：{editingPolicy.effectiveModel || '—'}</span>
                  <span className={editingPolicy.registered ? 'text-emerald-700' : 'text-red-700'}>
                    {editingPolicy.registered ? '兼容' : (editingPolicy.availabilityReason || '不兼容')}
                  </span>
                </div>
              </div>

              {/* Failure Policy */}
              <label className="block">
                <span className="text-[11px] font-medium text-text-secondary">失败策略（Failure Policy）</span>
                <select
                  value={policyForm.failurePolicy}
                  onChange={(event) => setPolicyForm((prev) => ({ ...prev, failurePolicy: event.target.value as FailurePolicy }))}
                  data-testid="policy-failure-policy"
                  className="mt-1 w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20"
                >
                  {FAILURE_POLICIES.map((policy) => (
                    <option
                      key={policy}
                      value={policy}
                      disabled={editingPolicy.role === 'embedding' && policy === 'fallback'}
                    >
                      {failurePolicyLabel(policy)}{editingPolicy.role === 'embedding' && policy === 'fallback' ? '（embedding 禁用）' : ''}
                    </option>
                  ))}
                </select>
                {editingPolicy.role === 'embedding' && (
                  <span className="mt-1 block text-[10px] text-amber-700">
                    embedding 不允许 fallback：不同向量模型不共享语义空间，静默切换会让新向量查询旧索引。
                  </span>
                )}
              </label>

              {/* Fallback Model（非 fail_fast/skip 才有意义，但始终展示，禁用逻辑在 submit） */}
              <label className="block">
                <span className="text-[11px] font-medium text-text-secondary">Fallback 模型（留空 = 不启用备用）</span>
                <select
                  value={policyForm.fallbackModel}
                  onChange={(event) => setPolicyForm((prev) => ({ ...prev, fallbackModel: event.target.value }))}
                  data-testid="policy-fallback-model"
                  disabled={editingPolicy.role === 'embedding'}
                  className="mt-1 w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 font-mono text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-50"
                >
                  <option value="">（不启用备用模型）</option>
                  {catalog
                    .filter((item) => (item.modelKind || 'chat') === roleModelKind(editingPolicy.role))
                    .filter((item) => item.name !== editingPolicy.effectiveModel)
                    .map((item) => (
                      <option key={item.name} value={item.name}>{item.name}</option>
                    ))}
                </select>
              </label>

              {/* Timeout / Retry */}
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-[11px] font-medium text-text-secondary">超时（秒）</span>
                  <input
                    type="number"
                    min={1}
                    max={600}
                    value={policyForm.timeoutSeconds}
                    onChange={(event) => setPolicyForm((prev) => ({ ...prev, timeoutSeconds: Number(event.target.value) }))}
                    data-testid="policy-timeout"
                    className="mt-1 w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] font-medium text-text-secondary">重试次数（0~3）</span>
                  <input
                    type="number"
                    min={0}
                    max={3}
                    value={policyForm.maxRetries}
                    onChange={(event) => setPolicyForm((prev) => ({ ...prev, maxRetries: Number(event.target.value) }))}
                    data-testid="policy-retries"
                    className="mt-1 w-full rounded-lg border border-black/10 bg-white px-2 py-1.5 text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20"
                  />
                </label>
              </div>
              <div className="text-[10px] text-text-muted">
                本轮总预算：{policyForm.timeoutSeconds * (policyForm.maxRetries + 1)}s
                {policyForm.timeoutSeconds * (policyForm.maxRetries + 1) > 60 && editingPolicy.role !== 'ocr' && (
                  <span className="text-amber-700">（超过 60s 推荐上限，保存可能被拒绝）</span>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4">
              <button
                type="button"
                onClick={() => setEditingPolicy(null)}
                disabled={busy}
                className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void persistPolicy(editingPolicy)}
                disabled={busy}
                data-testid="policy-save"
                className="rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"
              >
                {busy ? '保存中…' : '保存策略'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
