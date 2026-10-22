'use client'

import { FormEvent, useEffect, useMemo, useState } from 'react'
import { Archive, Check, Loader2, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import RoleGate from '@/components/auth/RoleGate'
import {
  activateKnowledgeBase,
  createKnowledgeBase,
  deactivateKnowledgeBase,
  listKbAuthority,
  updateKnowledgeBase,
  type KbAuthorityItem,
  type KbAuthorityOverview,
  type KbAuthorityWrite,
} from '@/api/knowledge'
import { atLeast } from '@/lib/auth'

type Draft = KbAuthorityWrite & { kb_id: string }

const EMPTY_DRAFT: Draft = {
  kb_id: '',
  name: '',
  domain: '',
  owner_depts: [],
  audience: 'internal',
}

const AUDIENCE_LABEL: Record<KbAuthorityItem['audience'], string> = {
  customer: '对客',
  internal: '内部',
  test: '测试',
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '知识库操作失败，请重试'
}

export default function KbAuthorityPanel() {
  const [data, setData] = useState<KbAuthorityOverview>({
    knowledge_bases: [], departments: [], total: 0,
  })
  const [includeInactive, setIncludeInactive] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const isSuperAdmin = atLeast('super_admin')

  const departmentLabels = useMemo(
    () => new Map(data.departments.map(department => [department.id, department.label])),
    [data.departments],
  )

  const load = async (showSpinner = true) => {
    if (showSpinner) setLoading(true)
    setError('')
    try {
      setData(await listKbAuthority(includeInactive))
    } catch (loadError) {
      setError(errorMessage(loadError))
    } finally {
      if (showSpinner) setLoading(false)
    }
  }

  useEffect(() => { void load() }, [includeInactive])

  const beginCreate = () => {
    setEditingId(null)
    setDraft({ ...EMPTY_DRAFT })
    setError('')
    setNotice('')
  }

  const beginEdit = (item: KbAuthorityItem) => {
    setEditingId(item.kb_id)
    setDraft({
      kb_id: item.kb_id,
      name: item.name,
      domain: item.domain,
      owner_depts: [...item.owner_depts],
      audience: item.audience,
    })
    setError('')
    setNotice('')
  }

  const closeEditor = () => {
    if (saving) return
    setDraft(null)
    setEditingId(null)
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!draft || saving) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const body = {
        name: draft.name.trim(),
        domain: draft.domain.trim().toLowerCase(),
        owner_depts: draft.owner_depts,
        audience: draft.audience,
      }
      if (editingId) {
        const existing = data.knowledge_bases.find(item => item.kb_id === editingId)
        if (!existing) throw new Error('知识库已不存在，请刷新列表')
        const editable = isSuperAdmin ? body : {
          name: body.name,
          domain: body.domain,
          owner_depts: body.owner_depts,
        }
        await updateKnowledgeBase(editingId, editable, existing.version)
      } else {
        await createKnowledgeBase(draft.kb_id.trim().toLowerCase(), body)
      }
      setDraft(null)
      setEditingId(null)
      setNotice(editingId ? '知识库配置已更新' : '知识库已创建')
      await load(false)
    } catch (saveError) {
      setError(errorMessage(saveError))
    } finally {
      setSaving(false)
    }
  }

  const toggleDepartment = (departmentId: string) => {
    setDraft(current => {
      if (!current) return current
      const selected = new Set(current.owner_depts.filter(value => value !== 'all'))
      if (selected.has(departmentId)) selected.delete(departmentId)
      else selected.add(departmentId)
      return { ...current, owner_depts: [...selected] }
    })
  }

  const setAllDepartments = (checked: boolean) => {
    setDraft(current => current
      ? { ...current, owner_depts: checked ? ['all'] : [] }
      : current)
  }

  const changeStatus = async (item: KbAuthorityItem) => {
    const action = item.active ? '停用' : '重新启用'
    if (item.active && !window.confirm(
      `确定停用「${item.name}」吗？该库的 ${item.doc_count} 份文档会保留，但停用后不能上传或检索。`,
    )) return
    setError('')
    setNotice('')
    setSaving(true)
    try {
      if (item.active) await deactivateKnowledgeBase(item.kb_id, item.version)
      else await activateKnowledgeBase(item.kb_id, item.version)
      setNotice(`知识库已${action}`)
      await load(false)
    } catch (statusError) {
      setError(errorMessage(statusError))
    } finally {
      setSaving(false)
    }
  }

  const ownerLabels = (item: KbAuthorityItem) => item.owner_depts.includes('all')
    ? ['全部启用部门']
    : item.owner_depts.map(department => departmentLabels.get(department) || department)

  return (
    <RoleGate minRole="admin" pageName="知识库管理">
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-800">
              <ShieldCheck className="h-4 w-4 text-emerald-600" />知识库管理
            </h2>
            <p className="mt-1 text-xs text-gray-500">
              一个知识库可关联多个部门；每份上传文档仍归属一个部门。平台级目录仅限 super_admin 修改。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={event => setIncludeInactive(event.target.checked)}
              />
              显示已停用
            </label>
            <button
              onClick={() => { void load() }}
              disabled={loading}
              className="flex items-center gap-1 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              刷新
            </button>
            {isSuperAdmin && (
              <button
                onClick={beginCreate}
                disabled={saving}
                className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" />新建知识库
              </button>
            )}
          </div>
        </div>

        {error && <div role="alert" className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
        {notice && <div role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{notice}</div>}

        <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-xs">
              <thead className="bg-gray-50 text-gray-500">
                <tr>
                  <th className="px-3 py-2.5 font-medium">知识库</th>
                  <th className="px-3 py-2.5 font-medium">负责部门</th>
                  <th className="px-3 py-2.5 font-medium">受众</th>
                  <th className="px-3 py-2.5 text-right font-medium">文档数</th>
                  <th className="px-3 py-2.5 font-medium">状态</th>
                  <th className="px-3 py-2.5 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.knowledge_bases.map(item => (
                  <tr key={item.kb_id} className="border-t border-gray-100 align-top hover:bg-gray-50/70">
                    <td className="px-3 py-3">
                      <div className="font-medium text-gray-800">{item.name}</div>
                      <div className="mt-1 font-mono text-[10px] text-gray-400">{item.kb_id} · {item.domain}</div>
                      {item.read_only && <span className="mt-1 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">兼容只读</span>}
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex max-w-sm flex-wrap gap-1">
                        {ownerLabels(item).map(label => (
                          <span key={label} className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700">{label}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-gray-600">
                      <span className={`rounded px-1.5 py-0.5 ${item.audience === 'customer' ? 'bg-blue-50 text-blue-700' : item.audience === 'test' ? 'bg-gray-100 text-gray-500' : 'bg-amber-50 text-amber-700'}`}>
                        {AUDIENCE_LABEL[item.audience]}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-gray-700">{item.doc_count}</td>
                    <td className="px-3 py-3">
                      <span className={`rounded px-1.5 py-0.5 ${item.active ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                        {item.active ? '启用' : '已停用'}
                      </span>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex justify-end gap-1.5">
                        {isSuperAdmin && !item.read_only && (
                          <button
                            onClick={() => beginEdit(item)}
                            disabled={saving}
                            className="rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-white disabled:opacity-50"
                          >编辑</button>
                        )}
                        {isSuperAdmin && !item.read_only && (
                          <button
                            onClick={() => { void changeStatus(item) }}
                            disabled={saving}
                            className="flex items-center gap-1 rounded border border-gray-200 px-2 py-1 text-gray-600 hover:bg-white disabled:opacity-50"
                          >
                            {item.active ? <><Archive className="h-3 w-3" />停用</> : <><Check className="h-3 w-3" />启用</>}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {!loading && data.knowledge_bases.length === 0 && (
                  <tr><td colSpan={6} className="px-3 py-8 text-center text-gray-400">没有匹配的知识库</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {draft && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4" role="presentation">
            <form
              onSubmit={save}
              className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl"
              role="dialog"
              aria-modal="true"
              aria-labelledby="kb-editor-title"
            >
              <div className="mb-4 flex items-center justify-between">
                <h3 id="kb-editor-title" className="text-sm font-semibold text-gray-800">
                  {editingId ? '编辑知识库' : '新建知识库'}
                </h3>
                <button type="button" onClick={closeEditor} disabled={saving} aria-label="关闭" className="rounded p-1 text-gray-400 hover:bg-gray-100">
                  <X className="h-4 w-4" />
                </button>
              </div>

              {!editingId && (
                <label className="mb-3 block text-xs text-gray-600">
                  知识库标识
                  <input
                    required
                    pattern="[a-z][a-z0-9_-]{1,63}"
                    value={draft.kb_id}
                    onChange={event => setDraft({ ...draft, kb_id: event.target.value.toLowerCase() })}
                    placeholder="例如 policy_safety"
                    className="mt-1.5 w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs text-gray-800 outline-none focus:border-blue-400"
                  />
                </label>
              )}
              <label className="mb-3 block text-xs text-gray-600">
                名称
                <input required maxLength={120} value={draft.name}
                  onChange={event => setDraft({ ...draft, name: event.target.value })}
                  className="mt-1.5 w-full rounded-md border border-gray-200 px-3 py-2 text-xs text-gray-800 outline-none focus:border-blue-400" />
              </label>
              <label className="mb-4 block text-xs text-gray-600">
                内容域标识
                <input required pattern="[a-z][a-z0-9_-]{0,63}" value={draft.domain}
                  onChange={event => setDraft({ ...draft, domain: event.target.value.toLowerCase() })}
                  placeholder="例如 safety"
                  className="mt-1.5 w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-xs text-gray-800 outline-none focus:border-blue-400" />
              </label>

              <fieldset className="mb-4 rounded-lg border border-gray-200 p-3">
                <legend className="px-1 text-xs font-medium text-gray-700">允许上传的部门（可多选）</legend>
                <label className="mb-2 flex items-center gap-2 text-xs text-gray-700">
                  <input type="checkbox" checked={draft.owner_depts.includes('all')}
                    onChange={event => setAllDepartments(event.target.checked)} />
                  全部启用部门
                </label>
                <div className="grid grid-cols-2 gap-2 border-t border-gray-100 pt-2 sm:grid-cols-3">
                  {data.departments.map(department => (
                    <label key={department.id} className="flex items-center gap-2 text-xs text-gray-600">
                      <input
                        type="checkbox"
                        disabled={draft.owner_depts.includes('all') || (!department.active && !draft.owner_depts.includes(department.id))}
                        checked={draft.owner_depts.includes(department.id)}
                        onChange={() => toggleDepartment(department.id)}
                      />
                      {department.label}{!department.active && '（已停用）'}
                    </label>
                  ))}
                  {data.departments.length === 0 && <span className="text-xs text-gray-400">当前租户没有启用部门</span>}
                </div>
              </fieldset>

              {isSuperAdmin && (
                <label className="mb-4 block text-xs text-gray-600">
                  受众范围
                  <select value={draft.audience}
                    onChange={event => setDraft({ ...draft, audience: event.target.value as Draft['audience'] })}
                    className="mt-1.5 w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-xs text-gray-800">
                    <option value="internal">内部</option>
                    <option value="customer">对客</option>
                    <option value="test">测试</option>
                  </select>
                </label>
              )}

              <p className="mb-4 text-[11px] leading-5 text-gray-400">
                知识库归属部门决定可上传范围；文档仍需选择一个部门归档。知识库停用后原文档保留，可重新启用。
                {!isSuperAdmin && '平台级知识库目录仅限 super_admin 修改。'}
              </p>
              <div className="flex justify-end gap-2">
                <button type="button" onClick={closeEditor} disabled={saving}
                  className="rounded-md border border-gray-200 px-3 py-2 text-xs text-gray-600 hover:bg-gray-50">取消</button>
                <button type="submit" disabled={saving || !draft.owner_depts.length || !data.departments.some(department => department.active)}
                  className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-2 text-xs text-white hover:bg-blue-700 disabled:opacity-50">
                  {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                  保存
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </RoleGate>
  )
}
