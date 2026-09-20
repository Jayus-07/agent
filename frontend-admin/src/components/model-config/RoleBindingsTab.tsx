'use client'

import { useMemo, useState } from 'react'
import { CheckCircle2, Edit3, Save, X } from 'lucide-react'
import type { ModelCatalogEntry } from '@/api/modelConfig'
import { saveModelRole } from '@/api/modelConfig'
import type { RoleBinding } from '@/types/modelConfig'
import { isModelSelectable, modelKindLabel, roleLabel, roleModelKind, sourceLabel } from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  roles: RoleBinding[]
  catalog: ModelCatalogEntry[]
  canAdmin: boolean
  onSaved: () => Promise<unknown>
}

export default function RoleBindingsTab({ roles, catalog, canAdmin, onSaved }: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const byName = useMemo(() => new Map(catalog.map((item) => [item.name, item])), [catalog])

  function begin(row: RoleBinding) {
    setEditing(row.role)
    setValue(row.effectiveModel)
  }

  async function save(row: RoleBinding) {
    if (!value.trim() && row.role !== 'eval_gen') {
      toast.error('模型名不能为空')
      return
    }
    const expectedKind = roleModelKind(row.role)
    const selected = catalog.some((item) => (
      item.name === value.trim() && (item.modelKind || 'chat') === expectedKind
    ))
    if (value.trim() && !selected) {
      toast.error(`请从已配置的${modelKindLabel(expectedKind)}中选择`)
      return
    }
    if (row.requiresReindex && value !== row.effectiveModel && !window.confirm(
      '修改该模型后，已有索引与查询向量可能不在同一空间，必须全量重建索引。确认继续？',
    )) return
    setBusy(true)
    try {
      await saveModelRole(row.role, value.trim())
      if (row.requiresReindex) {
        toast.success('已保存；重建索引时会读取新模型，当前索引不会自动切换')
      } else {
        toast.success('模型角色已保存，下一次对应链路调用生效')
      }
      setEditing(null)
      await onSaved()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '模型角色保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="overflow-hidden rounded-xl border border-black/5 bg-white shadow-card">
      <div className="border-b border-slate-100 px-4 py-3">
        <h2 className="text-xs font-medium text-text-primary">角色绑定</h2>
        <p className="mt-1 text-[11px] text-text-muted">角色决定业务链路使用哪个模型；非索引链路会在刷新周期内读取新值，向量化模型必须先重建索引。</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-xs">
          <thead><tr className="border-b border-slate-100 text-[10px] text-text-muted"><th className="px-4 py-3">角色</th><th className="px-4 py-3">生效模型</th><th className="px-4 py-3">来源</th><th className="px-4 py-3">校验</th><th className="px-4 py-3 text-right">操作</th></tr></thead>
          <tbody>
            {roles.map((row) => {
              const expectedKind = roleModelKind(row.role)
              const compatibleModels = catalog.filter((item) => (item.modelKind || 'chat') === expectedKind)
              const current = byName.get(row.effectiveModel)
              const currentIsSelectable = compatibleModels.some((item) => item.name === row.effectiveModel)
              const currentVerdict = !row.effectiveModel
                ? { selectable: false, reason: '未配置' }
                : row.availabilityReason
                  ? { selectable: false, reason: row.availabilityReason }
                  : isModelSelectable(current && {
                      name: current.name,
                      provider: current.provider,
                      modelKind: current.modelKind,
                      registered: row.registered,
                      missingKeyEnv: row.missingKeyEnv,
                      availabilityReason: current.availabilityReason,
                    }, expectedKind)
              const currentUsable = currentVerdict.selectable
              return (
                <tr key={row.role} className="border-b border-slate-50 align-top last:border-0">
                  <td className="px-4 py-3"><div className="font-medium text-text-primary">{roleLabel(row.role)}</div><div className="mt-0.5 font-mono text-[10px] text-text-muted">{row.role}</div></td>
                  <td className="px-4 py-3">
                    {editing === row.role ? (
                      <>
                        <select value={value} onChange={(event) => setValue(event.target.value)} className="w-full rounded-lg border border-accent/40 bg-white px-2 py-1.5 font-mono text-[11px] text-text-primary focus:outline-none focus:ring-2 focus:ring-accent/20">
                          {row.role === 'eval_gen' && <option value="">未配置（停用评测生成）</option>}
                          {row.role !== 'eval_gen' && !compatibleModels.length && !row.effectiveModel && <option value="" disabled>暂无已配置的{modelKindLabel(expectedKind)}，请先到供应商页面新增模型</option>}
                          {row.effectiveModel && !currentIsSelectable && <option value={row.effectiveModel} disabled>当前值：{row.effectiveModel}（未登记或用途不匹配）</option>}
                          {compatibleModels.map((item) => {
                            const verdict = isModelSelectable({
                              name: item.name,
                              provider: item.provider,
                              modelKind: item.modelKind,
                              registered: true,
                              missingKeyEnv: null,
                              availabilityReason: item.availabilityReason,
                            }, expectedKind)
                            return <option key={item.name} value={item.name} disabled={!verdict.selectable}>{item.name}{verdict.reason ? `（${verdict.reason}）` : ''}</option>
                          })}
                        </select>
                        <div className="mt-1 text-[10px] text-text-muted">只能从已配置的{modelKindLabel(expectedKind)}中选择；新增模型请先到供应商与密钥页面登记并测试。</div>
                      </>
                    ) : (
                      <div className={currentVerdict.selectable ? 'font-mono text-text-primary' : 'font-mono text-red-700'}>{row.effectiveModel || '—'}{row.effectiveModel && <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-sans text-[10px] text-text-muted">{modelKindLabel(current?.modelKind || expectedKind)}</span>}{!currentVerdict.selectable && <span className="ml-2 text-[10px]">：{currentVerdict.reason}</span>}</div>
                    )}
                    {editing === row.role && row.role === 'eval_gen' && <div className="mt-1 text-[10px] text-text-muted">评测生成必须选择已登记且可调用的文本模型；不会再默认使用本地 Ollama。</div>}
                    {row.literalValue !== row.effectiveModel && <div className="mt-1 text-[10px] text-text-muted">字面值：{row.literalValue || '空（有继承语义）'}</div>}
                  </td>
                  <td className="px-4 py-3"><span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] text-text-secondary">{sourceLabel(row.source, row.inheritedFrom, row.effectiveModel)}</span></td>
                  <td className="px-4 py-3">{currentUsable ? <span className="flex items-center gap-1 text-emerald-700"><CheckCircle2 size={13} />可用</span> : <span className="text-red-700">{currentVerdict.reason || (row.registered ? `缺少 ${row.missingKeyEnv}` : '未注册')}</span>}{row.requiresReindex && <div className="mt-1 text-[10px] text-amber-700">变更需重建索引</div>}</td>
                  <td className="px-4 py-3 text-right">
                    {canAdmin && (editing === row.role ? <div className="flex justify-end gap-1.5"><button disabled={busy} onClick={() => { void save(row) }} className="flex items-center gap-1 rounded-lg bg-accent px-2.5 py-1.5 text-[11px] text-white disabled:opacity-50"><Save size={12} />保存</button><button disabled={busy} onClick={() => setEditing(null)} className="rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-text-secondary"><X size={12} /></button></div> : <button onClick={() => begin(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Edit3 size={12} />修改</button>)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!roles.length && <div className="px-4 py-10 text-center text-xs text-text-muted">未登记任何模型角色</div>}
      </div>
    </section>
  )
}
