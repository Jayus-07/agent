/** 新增/改价模型弹窗（2026-09-22 起测试与保存独立：保存不探测，
 * 「测试连接」为独立动作，用供应商托管凭据测弹窗内填写的模型名）。
 *
 * 2026-09-22：按量计费（metered）供应商在此直接填单价 —— 登记即生效，
 * 同步写入目录展示价与计费表（model_price），不再走模型价格页的
 * 导入+双人审核流程。embedding/rerank 只有输入单价一个字段。
 */
import { FlaskConical, Save, X } from 'lucide-react'
import { modelKindLabel, type ModelKind } from '@/types/modelConfig'
import type { ProbeResponse } from '@/api/modelConfig'
import type { ModelDraft } from './draft'

const PRICE_HINT = '每 1M tokens'

export default function ProviderModelEditor({
  draft,
  busy,
  setDraft,
  onSave,
  onCancel,
  onTest,
  testing,
  probe,
  probeError,
}: {
  draft: ModelDraft
  busy: boolean
  setDraft: (value: ModelDraft) => void
  onSave: () => void
  onCancel: () => void
  onTest: () => void
  testing: boolean
  probe: ProbeResponse | null
  probeError: string | null
}) {
  const metered = draft.provider.billing === 'metered'
  const isLlmFamily = draft.modelKind !== 'embedding' && draft.modelKind !== 'rerank'
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={`${draft.editingExisting ? '调整' : '新增'} ${draft.provider.displayName} 模型`}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-text-primary">{draft.editingExisting ? '调整模型价格' : '新增模型'}</div>
            <p className="mt-1 text-[11px] text-text-muted">供应商：{draft.provider.displayName}。保存即按填写的单价登记生效{metered ? '（登记即生效）' : ''}；连通性验证请用供应商卡片上的「测试连接」。</p>
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
              <option value="ocr">OCR 模型（文档识别 / 视觉问答）</option>
            </select>
          </label>
          {metered && (
            <fieldset className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5">
              <legend className="px-1 text-[11px] text-text-secondary">Token 计费（{PRICE_HINT}，保存即生效）</legend>
              <label className="mb-2 block text-[11px] text-text-secondary">货币
                <select value={draft.priceCurrency} onChange={(event) => setDraft({ ...draft, priceCurrency: event.target.value as 'CNY' | 'USD', error: null })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs">
                  <option value="CNY">CNY（人民币）</option>
                  <option value="USD">USD（美元）</option>
                </select>
              </label>
              <div className={`grid gap-2 ${isLlmFamily ? 'grid-cols-2' : 'grid-cols-1'}`}>
                <label className="block text-[11px] text-text-secondary">输入价格
                  <input value={draft.inputPrice} onChange={(event) => setDraft({ ...draft, inputPrice: event.target.value, error: null })} inputMode="decimal" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="3" />
                </label>
                {isLlmFamily && (
                  <label className="block text-[11px] text-text-secondary">输出价格
                    <input value={draft.outputPrice} onChange={(event) => setDraft({ ...draft, outputPrice: event.target.value, error: null })} inputMode="decimal" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="9" />
                  </label>
                )}
              </div>
              {isLlmFamily && (
                <label className="mt-2 block text-[11px] text-text-secondary">缓存命中价格（可选）
                  <input value={draft.cachedInputPrice} onChange={(event) => setDraft({ ...draft, cachedInputPrice: event.target.value, error: null })} inputMode="decimal" className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="0.3" />
                  <span className="mt-1 block text-[10px] leading-4 text-text-muted">仅当模型 Provider 能返回缓存命中 Token 数量时，缓存价格才会参与精确成本计算；留空 = 未配置，命中时按输入价保守估算（标记 estimated）。</span>
                </label>
              )}
              <label className="mt-2 block text-[11px] text-text-secondary">上游模型名（可选）
                <input value={draft.upstreamName} onChange={(event) => setDraft({ ...draft, upstreamName: event.target.value, error: null })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="默认与上方「模型名称」相同" />
                <span className="mt-1 block text-[10px] leading-4 text-text-muted">发给厂商 API 的真实模型名。仅在登记名与上游不一致时需要填——例如登记名用了 qwen3.7-plus@relay 以区分厂商，这里填厂商文档里的原名 qwen3.7-plus。</span>
              </label>
              {!isLlmFamily && (
                <div className="mt-2 text-[10px] leading-4 text-text-muted">向量 / 重排模型按输入 token 计量计费：API 不产生输出 token，故无输出单价（与系统 token 计量口径一致）。</div>
              )}
            </fieldset>
          )}
          {probe && (
            <div className={`rounded-lg px-3 py-2 text-[11px] ${probe.ok ? 'border border-emerald-200 bg-emerald-50 text-emerald-800' : 'border border-red-200 bg-red-50 text-red-800'}`}>
              {probe.ok ? '✓ ' : '✗ '}{probe.summary}
              {probe.steps?.some((s) => s.raw) && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-[10px] opacity-70">技术细节（上游原文，排障用）</summary>
                  <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-4">{probe.steps.filter((s) => s.raw).map((s) => `${s.grade}: ${s.summary}${s.raw ? ` | ${s.raw}` : ''}`).join('\n')}</pre>
                </details>
              )}
            </div>
          )}
          {draft.error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{draft.error}</div>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button disabled={busy} onClick={onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
          <button
            disabled={busy || testing || !draft.modelName.trim()}
            title={!draft.modelName.trim() ? '先填模型名称再测试' : '用供应商已托管的密钥测试此模型名（不影响保存）'}
            onClick={onTest}
            className="flex items-center gap-1 rounded-lg border border-accent/30 px-3 py-2 text-xs text-accent disabled:opacity-50"
          ><FlaskConical size={13} />{testing ? '测试中…' : '测试连接'}</button>
          <button disabled={busy} onClick={onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Save size={13} />{busy ? '保存中…' : '保存'}</button>
        </div>
      </div>
    </div>
  )
}
