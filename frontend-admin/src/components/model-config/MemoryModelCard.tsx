/** 记忆模型配置卡片（2026-11-20）。
 *
 *  「记忆模型」= 记忆系统做向量化用的 embedding 模型（后端复用全局
 *  embedding 角色绑定，见 backend/memory/long_term.py → get_embedding）。
 *  之前该配置只藏在「角色绑定 → 检索链路 → 向量化模型」且仅超管可改，
 *  用户找不到 —— 本卡片以「记忆模型」语义单独露出同一份配置。
 *
 *  改绑走既有 saveModelRole('embedding')（超管门控与后端一致），切换前
 *  previewEmbeddingChange 预览受影响向量；需要重建时调 rebuildEmbeddingIndex
 *  并轮询进度。不新建任何后端契约。
 */
import { useState } from 'react'
import { AlertTriangle, Database, Edit3, RefreshCw } from 'lucide-react'
import {
  previewEmbeddingChange,
  rebuildEmbeddingIndex,
  saveModelRole,
  type EmbeddingPreview,
  type EmbeddingRebuildStatus,
  type ModelCatalogEntry,
} from '@/api/modelConfig'
import type { RoleBinding } from '@/types/modelConfig'

export default function MemoryModelCard({
  roles,
  catalog,
  canManage,
  onSaved,
}: {
  roles: RoleBinding[]
  catalog: ModelCatalogEntry[]
  /** 超管门控（与后端 embedding 改绑权限一致） */
  canManage: boolean
  onSaved: () => void
}) {
  const embedding = roles.find((role) => role.role === 'embedding') ?? null
  const embeddingModels = catalog.filter((item) => item.modelKind === 'embedding')

  const [value, setValue] = useState('')
  const [editing, setEditing] = useState(false)
  const [preview, setPreview] = useState<EmbeddingPreview | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [rebuild, setRebuild] = useState<EmbeddingRebuildStatus | null>(null)

  async function begin() {
    if (!embedding) return
    setValue(embedding.effectiveModel)
    setEditing(true)
    setPreview(null)
    setError(null)
  }

  async function loadPreview() {
    if (!value.trim()) return
    setPreviewing(true)
    setError(null)
    try {
      setPreview(await previewEmbeddingChange(value.trim()))
    } catch (err) {
      setError(err instanceof Error ? err.message : '影响预览失败')
    } finally {
      setPreviewing(false)
    }
  }

  async function persist() {
    if (!value.trim()) return
    setBusy(true)
    setError(null)
    try {
      await saveModelRole('embedding', value.trim(), preview ?? undefined)
      setEditing(false)
      setPreview(null)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function startRebuild() {
    if (!embedding) return
    setBusy(true)
    setError(null)
    try {
      // 重建用当前模型的实时预览拿 fingerprint（与 RoleBindingsTab 同一链路）
      const preview = await previewEmbeddingChange(embedding.effectiveModel)
      const status = await rebuildEmbeddingIndex(preview)
      setRebuild(status)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : '重建任务提交失败')
    } finally {
      setBusy(false)
    }
  }

  if (!embedding) {
    return (
      <section className="rounded-xl border border-black/5 bg-white p-4 shadow-card">
        <div className="flex items-center gap-2 text-sm font-medium text-text-primary"><Database size={15} className="text-accent" />记忆模型</div>
        <p className="mt-2 text-[11px] text-text-muted">尚未读到 embedding 角色绑定（/sys/model-roles 未返回 embedding 行），请检查角色绑定数据。</p>
      </section>
    )
  }

  const rebuilding = rebuild?.taskId && rebuild.status !== 'completed' && rebuild.status !== 'failed'

  return (
    <section className="rounded-xl border border-black/5 bg-white p-4 shadow-card" data-testid="memory-model-card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-text-primary"><Database size={15} className="text-accent" />记忆模型</div>
          <p className="mt-1 text-[11px] text-text-muted">会话记忆与长期记忆的向量化使用此模型（与会话摘要、用户画像的记忆检索共用）。切换影响全平台记忆索引，需超管权限。</p>
        </div>
        <button
          type="button"
          onClick={() => { void begin() }}
          disabled={!canManage}
          title={canManage ? undefined : '全平台向量模型切换需要超级管理员权限'}
          className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5 disabled:cursor-not-allowed disabled:text-text-muted/50"
        ><Edit3 size={12} />修改</button>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
        <span className="rounded bg-slate-100 px-2 py-1 font-mono text-text-primary">{embedding.effectiveModel || '—'}</span>
        <span className={embedding.available ? 'rounded bg-emerald-50 px-2 py-1 text-emerald-700' : 'rounded bg-red-50 px-2 py-1 text-red-700'}>
          {embedding.available ? '可用' : (embedding.availabilityReason || '不可用')}
        </span>
        {embedding.provider && <span className="text-text-muted">供应商：{embedding.provider}</span>}
      </div>

      {(embedding.indexCompat ?? []).some((item) => item.status === 'rebuild_required' || item.status === 'failed') && (
        <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
          <span>存在待重建的历史向量索引。</span>
          <button
            type="button"
            onClick={() => { void startRebuild() }}
            disabled={!canManage || busy || Boolean(rebuilding)}
            className="flex items-center gap-1 rounded-lg border border-amber-300 px-2.5 py-1.5 text-[11px] text-amber-800 disabled:opacity-50"
          ><RefreshCw size={12} />{rebuilding ? `重建中 ${rebuild?.progress ?? ''}` : '重建全部向量'}</button>
        </div>
      )}
      {rebuilding && <p className="mt-2 text-[10px] text-text-muted">重建进行中：{rebuild?.progress ?? '进行中'}（完成前记忆向量检索暂停，失败会保留旧向量）。</p>}
      {error && <div className="mt-3 break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{error}</div>}

      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label="修改记忆模型">
          <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-sm font-semibold text-text-primary">修改记忆模型</div>
                <p className="mt-1 text-[11px] text-text-muted">当前：{embedding.effectiveModel || '—'}。请选择一个已登记的向量模型（embedding）。</p>
              </div>
              <button onClick={() => setEditing(false)} className="text-text-muted hover:text-text-primary" aria-label="关闭">✕</button>
            </div>
            <div className="mt-4 space-y-3">
              <label className="block text-xs text-text-secondary">向量模型
                <select data-testid="memory-model-select" value={value} onChange={(event) => { setValue(event.target.value); setPreview(null) }} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs">
                  <option value="">请选择</option>
                  {embeddingModels.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                  {!embeddingModels.some((item) => item.name === value) && value && <option value={value}>{value}（未登记）</option>}
                </select>
              </label>
              {preview && (
                <div className="rounded border border-amber-200 bg-amber-50 p-3 text-[11px] text-text-secondary">
                  <p className="flex items-center gap-1 font-medium text-amber-800"><AlertTriangle size={12} />将处理 {preview.vectors} 条向量，分布在 {preview.collections.length} 个数据集合（已验证 {preview.dimension} 维）。</p>
                  <p className="mt-1">{preview.billing === 'local' ? '本地推理不产生模型费用。' : '会产生模型调用费用，以实际用量结算。'}</p>
                  {preview.requiresRebuild && <p className="mt-1">切换后需重建索引，完成前记忆检索暂停。</p>}
                </div>
              )}
              {error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{error}</div>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button disabled={busy} onClick={() => setEditing(false)} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
              <button
                data-testid="memory-model-preview"
                disabled={busy || previewing || !value.trim() || value === embedding.effectiveModel}
                onClick={() => { void loadPreview() }}
                className="rounded-lg border border-accent/30 px-3 py-2 text-xs text-accent disabled:opacity-50"
              >{previewing ? '预览中…' : '预览影响'}</button>
              <button
                data-testid="memory-model-save"
                disabled={busy || !value.trim() || value === embedding.effectiveModel}
                title={value === embedding.effectiveModel ? '与当前模型一致，无需保存' : undefined}
                onClick={() => { void persist() }}
                className="rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"
              >{busy ? '保存中…' : '保存'}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
