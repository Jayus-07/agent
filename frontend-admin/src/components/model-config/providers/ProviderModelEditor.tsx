/** 新增模型弹窗（后端先测后入库；B4 拆分迁出）。 */
import { FlaskConical, X } from 'lucide-react'
import { modelKindLabel, type ModelKind } from '@/types/modelConfig'
import type { ModelDraft } from './draft'

export default function ProviderModelEditor({
  draft,
  busy,
  setDraft,
  onSave,
  onCancel,
}: {
  draft: ModelDraft
  busy: boolean
  setDraft: (value: ModelDraft) => void
  onSave: () => void
  onCancel: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={`新增 ${draft.provider.displayName} 模型`}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-text-primary">新增模型</div>
            <p className="mt-1 text-[11px] text-text-muted">供应商：{draft.provider.displayName}。后端会用已保存的 URL 和密钥测试，通过后才加入目录。</p>
          </div>
          <button onClick={onCancel} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
        </div>
        <div className="mt-5 space-y-3">
          <label className="block text-xs text-text-secondary">模型名称
            <input autoFocus value={draft.modelName} onChange={(event) => setDraft({ ...draft, modelName: event.target.value, error: null })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-sm" placeholder="例如：text-embedding-3-large" />
          </label>
          <label className="block text-xs text-text-secondary">模型用途
            <select value={draft.modelKind} onChange={(event) => setDraft({ ...draft, modelKind: event.target.value as ModelKind, error: null })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs">
              <option value="chat">文本模型（对话 / 评测 / 文档处理）</option>
              <option value="embedding">向量模型（Embedding）</option>
              <option value="rerank">重排模型（Rerank）</option>
              <option value="vision">视觉模型（Vision）</option>
              <option value="speech">语音模型（Speech）</option>
            </select>
          </label>
          <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-[11px] text-text-muted">将测试 {modelKindLabel(draft.modelKind)} 的对应端点；API Key 不会显示或返回。</div>
          {draft.error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{draft.error}</div>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button disabled={busy} onClick={onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
          <button disabled={busy} onClick={onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><FlaskConical size={13} />{busy ? '测试中…' : '测试并新增'}</button>
        </div>
      </div>
    </div>
  )
}
