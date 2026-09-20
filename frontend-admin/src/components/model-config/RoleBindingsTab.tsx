'use client'

import { Fragment, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, CircleAlert, Edit3, Save, X } from 'lucide-react'
import type { ModelCatalogEntry } from '@/api/modelConfig'
import { saveModelRole } from '@/api/modelConfig'
import type { ModelKind, ModelOption, RoleBinding, SelectableVerdict } from '@/types/modelConfig'
import {
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
  roles: RoleBinding[]
  catalog: ModelCatalogEntry[]
  canAdmin: boolean
  onSaved: () => Promise<unknown>
}

/** 生效来源 → 徽章配色（§6：DB 覆盖蓝 / 环境变量灰 / 跟随父角色紫 / 代码默认浅灰）。 */
const SOURCE_BADGE: Record<string, string> = {
  db: 'border-blue-200 bg-blue-50 text-blue-700',
  env: 'border-slate-200 bg-slate-100 text-text-secondary',
  inherit: 'border-purple-200 bg-purple-50 text-purple-700',
  default: 'border-slate-200 bg-white text-text-muted',
}

/** 未知来源不求鲜艳，只求不冒充「代码默认」。 */
const SOURCE_BADGE_FALLBACK = 'border-slate-300 bg-slate-50 text-text-muted'

/**
 * 角色的可用性判定。`modelName` 传行内生效值 = 展示态，传下拉选中值 = 编辑态预览。
 *
 * 后端对**当前生效值**的结论优先 —— 它能看到前端模型目录里没有的信息（供应商 Key 状态等）。
 * 编辑态则一律以目录项为准现算，这样用户在下拉里换模型时，可用性列会立刻跟着变，
 * 而不是等他保存完才发现挑了个不可用的模型。
 */
function roleVerdict(
  row: RoleBinding,
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

export default function RoleBindingsTab({ roles, catalog, canAdmin, onSaved }: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmRow, setConfirmRow] = useState<RoleBinding | null>(null)
  const [onlyProblem, setOnlyProblem] = useState(false)
  const byName = useMemo(() => new Map(catalog.map((item) => [item.name, item])), [catalog])
  const byRole = useMemo(() => new Map(roles.map((item) => [item.role, item])), [roles])

  const unavailableCount = useMemo(
    () => roles.filter((row) => !roleVerdict(row, row.effectiveModel, byName.get(row.effectiveModel), roleModelKind(row.role)).selectable).length,
    [roles, byName],
  )

  function begin(row: RoleBinding) {
    setEditing(row.role)
    setValue(row.effectiveModel)
  }

  function cancel() {
    setEditing(null)
    setValue('')
  }

  async function persist(row: RoleBinding) {
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

  function submit(row: RoleBinding) {
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
    const visible = (list: RoleBinding[]) =>
      list.filter((row) => !onlyProblem || !roleVerdict(
        row, row.effectiveModel, byName.get(row.effectiveModel), roleModelKind(row.role),
      ).selectable)
    const built = ROLE_GROUPS.map((group) => ({
      id: group.id,
      label: group.label,
      hint: group.hint,
      rows: visible(group.roles
        .map((role) => byRole.get(role))
        .filter((row): row is RoleBinding => Boolean(row))),
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

  return (
    <section className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <h2 className="text-xs font-medium text-text-primary">角色绑定</h2>
          <p className="mt-1 text-[11px] text-text-muted">
            共 {roles.length} 个角色，按业务链路分组。角色决定业务链路使用哪个模型；非索引链路会在刷新周期内读取新值，
            向量化与重排模型必须先重建索引。
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
        <table className="w-full min-w-[860px] text-left text-xs">
          <colgroup>
            <col className="w-[21%]" />
            <col className="w-[29%]" />
            <col className="w-[27%]" />
            <col className="w-[13%]" />
            <col className="w-[10%]" />
          </colgroup>
          <thead>
            <tr className="border-b border-slate-100 text-[10px] text-text-muted">
              <th scope="col" className="px-4 py-3 font-normal">角色</th>
              <th scope="col" className="px-4 py-3 font-normal">当前绑定</th>
              <th scope="col" className="px-4 py-3 font-normal">来源</th>
              <th scope="col" className="px-4 py-3 font-normal">可用性</th>
              <th scope="col" className="px-4 py-3 text-right font-normal">操作</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={group.id}>
                <tr className="bg-slate-50/80">
                  <td colSpan={5} className="px-4 py-2">
                    <span className="text-[11px] font-medium text-text-secondary">{group.label}</span>
                    <span className="ml-2 text-[10px] text-text-muted">{group.hint} · {group.rows.length} 个角色</span>
                  </td>
                </tr>
                {group.rows.map((row) => {
                  const expectedKind = roleModelKind(row.role)
                  const isEditing = editing === row.role
                  const compatibleModels = catalog.filter((item) => (item.modelKind || 'chat') === expectedKind)
                  const currentEntry = byName.get(row.effectiveModel)
                  const shownModel = isEditing ? value : row.effectiveModel
                  const verdict = roleVerdict(row, shownModel, byName.get(shownModel), expectedKind)
                  const usable = verdict.selectable
                  const sourceTone = SOURCE_BADGE[row.source] ?? SOURCE_BADGE_FALLBACK
                  return (
                    <tr
                      key={row.role}
                      className={`border-b border-slate-50 align-top last:border-0 ${isEditing ? 'bg-accent/5' : ''}`}
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
                            <span className={usable ? 'font-mono text-text-primary' : 'font-mono text-red-700'}>
                              {row.effectiveModel || '—'}
                            </span>
                            {currentEntry?.modelKind && (
                              <span className="rounded bg-slate-100 px-1.5 py-0.5 font-sans text-[10px] text-text-muted">
                                {modelKindLabel(currentEntry.modelKind)}
                              </span>
                            )}
                            {row.provider && (
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
                                {sourceLabel(row.source, row.inheritedFrom)}
                              </span>
                              {(row.updatedBy || row.updatedAt) && (
                                <span className="text-[10px] text-text-muted">
                                  {row.updatedBy ? `最后由 ${row.updatedBy}` : '最后修改'}
                                  {row.updatedAt ? ` · ${formatRelative(row.updatedAt)}` : ''}
                                </span>
                              )}
                            </div>
                            {row.literalValue !== row.effectiveModel && (
                              <div className="mt-1 text-[10px] text-text-muted">
                                {row.literalValue
                                  ? `配置值：${row.literalValue}`
                                  : '配置值：空白（该角色的空值有语义，不等于「未配置」）'}
                              </div>
                            )}
                          </>
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
                          <button
                            type="button"
                            onClick={() => begin(row)}
                            aria-label={`修改 ${roleLabel(row.role)}`}
                            className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"
                          >
                            <Edit3 size={12} />修改
                          </button>
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
              <p className="text-[11px] text-text-muted">保存后当前索引不会自动切换，重建索引时才读取新模型。</p>
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
                {busy ? '保存中…' : '确认修改'}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
