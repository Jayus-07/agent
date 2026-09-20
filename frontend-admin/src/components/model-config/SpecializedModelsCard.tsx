'use client'

import { useState } from 'react'
import { CheckCircle2, CircleAlert, Edit3, KeyRound, Save, X } from 'lucide-react'
import { testAndSaveSpecialized } from '@/api/modelConfig'
import type {
  SpecializedConfigureResponse,
  SpecializedModelBinding,
  SpecializedModelConfigureInput,
  SpecializedModelResponse,
  SpecializedRole,
} from '@/types/modelConfig'
import { maskSecret } from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  config: SpecializedModelResponse
  canAdmin: boolean
  onSaved: () => Promise<unknown>
}

interface Draft {
  providerId?: string
  displayName: string
  providerBaseUrl: string
  apiKey: string
  embeddingModel: string
  embeddingAdapter: string
  embeddingBaseUrl: string
  embeddingDimensions: string
  rerankModel: string
  rerankAdapter: string
  rerankBaseUrl: string
}

const DEFAULT_PROVIDER_BASE = 'https://dashscope.aliyuncs.com'
const DEFAULT_EMBEDDING_BASE = `${DEFAULT_PROVIDER_BASE}/compatible-mode/v1`
const DEFAULT_RERANK_BASE = `${DEFAULT_PROVIDER_BASE}/api/v1`

function roleLabel(role: SpecializedRole): string {
  return role === 'embedding' ? '向量化' : '重排'
}

function formatElapsed(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${Math.max(0, Math.round(ms))}ms` : `${(ms / 1000).toFixed(1)}s`
}

function bindingOf(config: SpecializedModelResponse, role: SpecializedRole): SpecializedModelBinding | undefined {
  return config.items.find((item) => item.role === role)
}

function defaultAdapter(config: SpecializedModelResponse, role: SpecializedRole): string {
  const current = bindingOf(config, role)?.adapter
  if (current) return current
  const expected = role === 'embedding' ? 'embedding' : 'rerank'
  return config.adapters.find((item) => item.includes(expected)) || (role === 'embedding' ? 'dashscope_embedding' : 'dashscope_rerank')
}

function buildDraft(config: SpecializedModelResponse): Draft {
  const embedding = bindingOf(config, 'embedding')
  const rerank = bindingOf(config, 'rerank')
  const current = embedding || rerank
  const bindingBaseUrl = current?.baseUrl || DEFAULT_PROVIDER_BASE
  const inferredProviderBaseUrl = bindingBaseUrl
    .replace(/\/(?:compatible-mode\/v1|api\/v1)$/i, '')
    .replace(/\/$/, '')
  return {
    providerId: current?.providerId,
    displayName: current?.providerName || '阿里云百炼专项',
    providerBaseUrl: config.providerBaseUrl || inferredProviderBaseUrl || DEFAULT_PROVIDER_BASE,
    apiKey: '',
    embeddingModel: embedding?.modelName || 'qwen3.7-text-embedding',
    embeddingAdapter: defaultAdapter(config, 'embedding'),
    embeddingBaseUrl: embedding?.baseUrl || DEFAULT_EMBEDDING_BASE,
    embeddingDimensions: String(embedding?.options?.dimensions ?? 1024),
    rerankModel: rerank?.modelName || 'qwen3.7-text-rerank',
    rerankAdapter: defaultAdapter(config, 'rerank'),
    rerankBaseUrl: rerank?.baseUrl || DEFAULT_RERANK_BASE,
  }
}

function ProbeResults({ result }: { result: SpecializedConfigureResponse | null }) {
  if (!result || result.tests.length === 0) return null
  return (
    <div className={`mt-3 rounded-lg border px-3 py-2 text-[11px] ${result.ok ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-red-200 bg-red-50 text-red-800'}`}>
      <div className="font-medium">{result.summary || (result.ok ? '测试通过' : '测试失败，未保存')}</div>
      <div className="mt-2 space-y-1.5">
        {result.tests.map((item) => (
          <div key={`${item.role}-${item.adapter}`} className="flex items-start justify-between gap-3 border-t border-current/10 pt-1.5 first:border-0 first:pt-0">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 font-medium">
                {item.ok ? <CheckCircle2 size={13} /> : <CircleAlert size={13} />}
                {roleLabel(item.role)} · {item.ok ? '通过' : item.summary}
              </div>
              {!item.ok && <div className="mt-0.5 break-words opacity-85">{item.detail}</div>}
            </div>
            <span className="shrink-0 font-mono opacity-75">{formatElapsed(item.elapsedMs)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function SpecializedModelsCard({ config, canAdmin, onSaved }: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SpecializedConfigureResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const embedding = bindingOf(config, 'embedding')
  const rerank = bindingOf(config, 'rerank')
  const credential = embedding?.credential || rerank?.credential

  function openEditor() {
    setDraft(buildDraft(config))
    setResult(null)
    setError(null)
    setEditing(true)
  }

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => current ? { ...current, [key]: value } : current)
  }

  async function submit() {
    if (!draft) return
    if (!draft.displayName.trim() || !draft.providerBaseUrl.trim()) {
      setError('供应商名称和 Base URL 不能为空')
      return
    }
    if (!draft.embeddingModel.trim() && !draft.rerankModel.trim()) {
      setError('至少填写一个专项模型名')
      return
    }
    const currentEmbedding = embedding?.modelName || ''
    if (draft.embeddingModel.trim() && currentEmbedding && draft.embeddingModel.trim() !== currentEmbedding && !window.confirm('修改向量模型后必须全量重建索引，确认继续？')) {
      return
    }

    const bindings: SpecializedModelConfigureInput['bindings'] = {}
    if (draft.embeddingModel.trim()) {
      bindings.embedding = {
        modelName: draft.embeddingModel.trim(),
        adapter: draft.embeddingAdapter,
        baseUrl: draft.embeddingBaseUrl.trim(),
        options: { dimensions: Number(draft.embeddingDimensions || 1024) },
      }
    }
    if (draft.rerankModel.trim()) {
      bindings.rerank = {
        modelName: draft.rerankModel.trim(),
        adapter: draft.rerankAdapter,
        baseUrl: draft.rerankBaseUrl.trim(),
      }
    }
    setBusy(true)
    setError(null)
    try {
      const next = await testAndSaveSpecialized({
        provider: {
          providerId: draft.providerId,
          displayName: draft.displayName.trim(),
          baseUrl: draft.providerBaseUrl.trim(),
          apiKey: draft.apiKey.trim() || undefined,
        },
        bindings,
      })
      setResult(next)
      if (!next.ok || !next.saved) {
        setError(next.summary || '测试未通过，配置未保存')
        toast.error(next.summary || '专项模型测试失败')
        return
      }
      toast.success('专项模型测试通过，配置已保存')
      setEditing(false)
      await onSaved()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '专项模型配置失败'
      setError(message)
      toast.error(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="mb-5 overflow-hidden rounded-xl border border-black/5 bg-white shadow-card" data-testid="specialized-models-card">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-text-primary"><KeyRound size={14} />专项模型</div>
          <p className="mt-1 text-[11px] text-text-muted">向量化和重排使用独立协议适配器；Key 只显示掩码，切换向量模型后必须重建索引。</p>
        </div>
        {canAdmin && <button onClick={openEditor} className="flex shrink-0 items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Edit3 size={12} />配置专项模型</button>}
      </div>
      <div className="grid gap-3 p-4 md:grid-cols-2">
        {[embedding, rerank].map((item, index) => {
          const role: SpecializedRole = index === 0 ? 'embedding' : 'rerank'
          return (
            <div key={role} className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-text-secondary">{roleLabel(role)}模型</span>
                {item?.lastProbe?.ok ? <span className="flex items-center gap-1 text-[10px] text-emerald-700"><CheckCircle2 size={12} />已通过</span> : <span className="text-[10px] text-text-muted">{item ? '未通过或未测试' : '未配置'}</span>}
              </div>
              <div className="mt-2 font-mono text-xs text-text-primary">{item?.modelName || '—'}</div>
              <div className="mt-1 text-[10px] text-text-muted">{item?.providerName || '—'} · {item?.adapter || '—'}</div>
              {item?.lastProbe && <div className="mt-1 text-[10px] text-text-muted">最近测试：{formatElapsed(item.lastProbe.elapsedMs)}{item.lastProbe.summary ? ` · ${item.lastProbe.summary}` : ''}</div>}
              {item?.requiresReindex && <div className="mt-1 text-[10px] text-amber-700">变更后必须全量重建索引</div>}
            </div>
          )
        })}
      </div>
      {credential?.configured && <div className="border-t border-slate-100 px-4 py-2 text-[10px] text-text-muted">API Key：{maskSecret(credential.last4, credential.fingerprint)}</div>}

      {editing && draft && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="配置专项模型">
          <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><div className="text-sm font-medium text-text-primary">配置专项模型供应商</div><button onClick={() => setEditing(false)} disabled={busy} className="text-text-muted hover:text-text-primary"><X size={16} /></button></div>
            <div className="space-y-4 px-5 py-4 text-xs">
              <div className="grid gap-3 md:grid-cols-2">
                <label className="block"><span className="mb-1 block text-text-secondary">供应商名称</span><input value={draft.displayName} onChange={(event) => update('displayName', event.target.value)} name="displayName" className="w-full rounded-lg border border-black/10 px-3 py-2 outline-none focus:border-accent" /></label>
                <label className="block"><span className="mb-1 block text-text-secondary">供应商 Base URL</span><input value={draft.providerBaseUrl} onChange={(event) => update('providerBaseUrl', event.target.value)} name="providerBaseUrl" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
              </div>
              <label className="block"><span className="mb-1 block text-text-secondary">API Key</span><input value={draft.apiKey} onChange={(event) => update('apiKey', event.target.value)} name="apiKey" type="password" placeholder={credential?.configured ? `已配置 ****${credential.last4 || ''}，留空沿用` : '输入 API Key'} autoComplete="new-password" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
              <div className="rounded-lg border border-slate-100 p-3">
                <div className="mb-2 font-medium text-text-secondary">向量化模型</div>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="block"><span className="mb-1 block text-text-muted">模型名</span><input value={draft.embeddingModel} onChange={(event) => update('embeddingModel', event.target.value)} name="embeddingModel" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
                  <label className="block"><span className="mb-1 block text-text-muted">协议适配器</span><select value={draft.embeddingAdapter} onChange={(event) => update('embeddingAdapter', event.target.value)} name="embeddingAdapter" className="w-full rounded-lg border border-black/10 px-3 py-2 text-[11px] outline-none focus:border-accent">{config.adapters.filter((item) => item.includes('embedding')).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
                  <label className="block md:col-span-2"><span className="mb-1 block text-text-muted">Embedding Base URL</span><input value={draft.embeddingBaseUrl} onChange={(event) => update('embeddingBaseUrl', event.target.value)} name="embeddingBaseUrl" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
                  <label className="block"><span className="mb-1 block text-text-muted">向量维度</span><input value={draft.embeddingDimensions} onChange={(event) => update('embeddingDimensions', event.target.value)} name="embeddingDimensions" inputMode="numeric" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
                </div>
              </div>
              <div className="rounded-lg border border-slate-100 p-3">
                <div className="mb-2 font-medium text-text-secondary">重排模型</div>
                <div className="grid gap-3 md:grid-cols-2">
                  <label className="block"><span className="mb-1 block text-text-muted">模型名</span><input value={draft.rerankModel} onChange={(event) => update('rerankModel', event.target.value)} name="rerankModel" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
                  <label className="block"><span className="mb-1 block text-text-muted">协议适配器</span><select value={draft.rerankAdapter} onChange={(event) => update('rerankAdapter', event.target.value)} name="rerankAdapter" className="w-full rounded-lg border border-black/10 px-3 py-2 text-[11px] outline-none focus:border-accent">{config.adapters.filter((item) => item.includes('rerank')).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
                  <label className="block md:col-span-2"><span className="mb-1 block text-text-muted">Rerank Base URL</span><input value={draft.rerankBaseUrl} onChange={(event) => update('rerankBaseUrl', event.target.value)} name="rerankBaseUrl" className="w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-[11px] outline-none focus:border-accent" /></label>
                </div>
              </div>
              {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{error}</div>}
              <ProbeResults result={result} />
            </div>
            <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-4"><button onClick={() => setEditing(false)} disabled={busy} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button><button onClick={() => { void submit() }} disabled={busy} className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Save size={13} />{busy ? '测试中…' : '测试并保存'}</button></div>
          </div>
        </div>
      )}
    </section>
  )
}
