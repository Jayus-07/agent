/** 供应商编辑抽屉（B4 拆分迁出；加载/保存逻辑仍由 ProvidersTab 主组件编排）。 */
import { useRef, useState } from 'react'
import { FlaskConical, KeyRound, Save, X } from 'lucide-react'
import {
  fetchModelCatalog,
  fetchProviderModelCatalog,
  type ProbeMode,
  type ProbeResponse,
} from '@/api/modelConfig'
import { maskSecret, modelKindLabel, type ModelCatalogResponse, type ModelKind, type PlanId, type PresetPlan, type ProviderPreset, type ProviderRow } from '@/types/modelConfig'
import BaseUrlAdvisor from './BaseUrlAdvisor'
import ErrorNote from './ErrorNote'
import ModelCatalogPicker from './ModelCatalogPicker'
import ProbeResultDetails from './ProbeResultDetails'
import type { Draft } from './draft'
import { fixHintsFor, type FixHint } from './fixHints'
import { formatLiveElapsed, probeModeLabel } from './format'
import { diagnoseBaseUrl, presetOptionLabel, unresolvedPlaceholder } from './presets'

export default function ProviderEditor({
  draft,
  providers,
  plans,
  presets,
  presetsLoading,
  busy,
  setDraft,
  onPlanChange,
  onPresetChange,
  probeResult,
  probeError,
  testingMode,
  testingElapsedMs,
  onSave,
  onTest,
  onCancel,
}: {
  draft: Draft
  providers: ProviderRow[]
  plans: PresetPlan[]
  presets: ProviderPreset[]
  presetsLoading: boolean
  busy: boolean
  setDraft: (value: Draft) => void
  onPlanChange: (value: PlanId | '') => void
  onPresetChange: (value: string) => void
  probeResult: ProbeResponse | null
  probeError: string | null
  testingMode: ProbeMode | null
  testingElapsedMs: number
  onSave: () => void
  /** 测试深度由抽屉内的「高级设置」决定，所以回调带 mode —— 底部不再放两个测试按钮。 */
  onTest: (mode: ProbeMode) => void
  onCancel: () => void
}) {
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch })
  const isNew = !draft.id
  const row = draft.id ? providers.find((item) => item.id === draft.id) ?? null : null

  // 测试深度：默认快速。完整测试只多查一项「流式是否回传 usage」，与可用性判断无关，
  // 因此收进高级设置，不再占用底部一个常驻按钮。
  const [probeDepth, setProbeDepth] = useState<ProbeMode>('fast')

  // 模型清单：用户点「从目录选」才去拉（见 ModelCatalogPicker 的说明）。
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [catalog, setCatalog] = useState<ModelCatalogResponse | null>(null)
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const apiKeyRef = useRef<HTMLInputElement | null>(null)

  async function loadCatalog() {
    if (catalogLoading) return
    const baseUrl = draft.baseUrl.trim()
    if (!baseUrl) {
      setCatalog(null)
      setCatalogError('请先填写 Base URL，才能读取该地址的模型清单')
      return
    }
    setCatalogLoading(true)
    setCatalogError(null)
    try {
      // 地址与密钥都没被改过、且有已保存密钥 → 走服务端已存的凭据（密钥不回显）；
      // 否则用草稿接口，把当前填的地址与 Key 带上去。
      const canUseSavedCredential = Boolean(
        draft.id
        && draft.credentialConfigured
        && !draft.apiKey?.trim()
        && draft.originalBaseUrl === baseUrl,
      )
      const result = canUseSavedCredential && draft.id
        ? await fetchProviderModelCatalog(draft.id)
        : await fetchModelCatalog({
            baseUrl,
            apiKey: draft.apiKey?.trim() || undefined,
            networkScope: draft.networkScope,
          })
      setCatalog(result)
    } catch (error) {
      setCatalog(null)
      setCatalogError(error instanceof Error ? error.message : '模型清单读取失败')
    } finally {
      setCatalogLoading(false)
    }
  }

  /** 把「去修」动作落到具体控件上。 */
  function handleFix(hint: FixHint) {
    if (hint.kind === 'use-preset') {
      onPresetChange(hint.presetId)
      return
    }
    if (hint.kind === 'use-url') {
      update({ baseUrl: hint.baseUrl })
      return
    }
    if (hint.kind === 'open-catalog') {
      setCatalogOpen(true)
      void loadCatalog()
      return
    }
    if (hint.kind === 'focus-api-key') {
      apiKeyRef.current?.focus()
      apiKeyRef.current?.select()
    }
  }

  // 只给「判死那一级」配动作 —— 降级/跳过的步骤不需要用户做什么。
  const fixHints: Record<string, FixHint[]> = {}
  for (const step of probeResult?.steps ?? []) {
    if (step.status !== 'fail') continue
    const hints = fixHintsFor(step.reason, draft, presets, plans)
    if (hints.length) fixHints[step.grade] = hints
  }

  // 预置目录可用性。不可用时整块降级为手填 —— 不能把「新增供应商」做成死路。
  const catalogReady = plans.length > 0 && presets.length > 0
  const planPresets = presets.filter((item) => item.plan === draft.plan)
  const selectedPreset = presets.find((item) => item.id === draft.presetId) ?? null
  const placeholder = unresolvedPlaceholder(draft.baseUrl)
  const baseUrlDiagnosis = diagnoseBaseUrl(draft, presets, plans)

  // 协议可选性：内置供应商的 driver 后端锁定（改了会 422）；ollama / specialized
  // 不在预置目录里，也不该被这个下拉悄悄改掉，故一并锁住。
  const driverOptions: Array<{ value: string; label: string }> = [
    { value: 'openai', label: 'OpenAI 兼容' },
    { value: 'anthropic', label: 'Anthropic 兼容' },
  ]
  const driverLocked = !isNew && (
    Boolean(row?.isBuiltin) || (draft.driver !== 'openai' && draft.driver !== 'anthropic')
  )

  // 已登记模型：决定「模型用途」能不能改 —— 后端对已登记模型一律拒绝改用途，
  // 所以这里必须前置拦住，否则用户只会看到一次必然失败的探测。
  const registeredModels = row?.models ?? []
  const trimmedName = draft.modelName.trim()
  const currentRegistered = registeredModels.find((model) => model.name === trimmedName) ?? null
  const kindLocked = Boolean(currentRegistered)
  // 模型名全局唯一：被别的供应商占了就一定会 409，提前点名。
  // 已经是本供应商自己的模型时不再提示 —— 那是「改自己」，不是「撞别人」。
  const occupiedBy = trimmedName && !currentRegistered
    ? providers.find((provider) => provider.id !== draft.id
        && (provider.models ?? []).some((model) => model.name === trimmedName)) ?? null
    : null

  function changeModelName(next: string) {
    // 改成另一个已登记模型名时，用途跟着走 —— 否则必然撞上「不可改用途」的 409。
    const registered = registeredModels.find((model) => model.name === next.trim())
    update(registered ? { modelName: next, modelKind: registered.modelKind } : { modelName: next })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={isNew ? '新增供应商' : `编辑 ${draft.displayName}`}>
      <div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-text-primary"><KeyRound size={16} className="text-accent" />{isNew ? '新增供应商' : `编辑 ${draft.displayName}`}</div>
            <p className="mt-1 text-[11px] text-text-muted">先选计费计划与厂商端点（自动填地址与协议），再填 API Key 和模型名称，测试通过后保存。</p>
          </div>
          <button onClick={onCancel} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
        </div>

        <div className="mt-5 space-y-3">
          {!catalogReady && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">{presetsLoading ? '预置厂商目录加载中…' : '预置厂商目录不可用（后端未部署该接口或请求失败）。可手动填写 Base URL，并在「高级设置」里选定计费口径。'}</div>}

          <label className="block text-xs text-text-secondary">计费计划
            <select data-testid="provider-plan" value={draft.plan} onChange={(event) => onPlanChange(event.target.value as PlanId | '')} disabled={!catalogReady} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm disabled:bg-slate-50">
              <option value="">未套用预置（自建 / 内网）</option>
              {plans.map((plan) => <option key={plan.id} value={plan.id}>{plan.label}</option>)}
            </select>
            <span className="mt-1 block text-[10px] text-text-muted">按量付费与 Token Plan / Coding Plan 走的是不同端点，用错会产生额外费用。</span>
          </label>

          <label className="block text-xs text-text-secondary">厂商 · 协议
            <select data-testid="provider-preset" value={draft.presetId ?? '__custom'} onChange={(event) => onPresetChange(event.target.value)} disabled={!catalogReady || !draft.plan} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm disabled:bg-slate-50">
              <option value="__custom">自定义（手填 Base URL）</option>
              {planPresets.map((preset) => <option key={preset.id} value={preset.id}>{presetOptionLabel(preset)}</option>)}
            </select>
            {!draft.plan && catalogReady && <span className="mt-1 block text-[10px] text-text-muted">先选计费计划，这里才会列出该计划下的厂商端点。</span>}
          </label>

          {selectedPreset && (selectedPreset.note || selectedPreset.apiKeyHint) && <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-[11px]">{selectedPreset.apiKeyHint && <div className="text-text-muted">API Key 格式：{selectedPreset.apiKeyHint}</div>}{selectedPreset.note && <div className="mt-0.5 text-amber-700">{selectedPreset.note}</div>}{baseUrlDiagnosis.kind === 'deviated' && <div className="mt-0.5 text-amber-700">以上按预置「{baseUrlDiagnosis.label}」填写，而地址已被改过 —— 可能不适用。</div>}</div>}

          <label className="block text-xs text-text-secondary">显示名
            <input data-testid="provider-display-name" value={draft.displayName} onChange={(event) => update({ displayName: event.target.value })} maxLength={128} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：火山引擎 · Coding Plan" autoComplete="off" />
            <span className="mt-1 block text-[10px] text-text-muted">留空则由地址域名兜底；显示名会参与生成供应商 ID。</span>
          </label>

          <div className="grid gap-3 md:grid-cols-[1fr,2fr]">
            <label className="block text-xs text-text-secondary">协议
              <select data-testid="provider-driver" value={draft.driver} onChange={(event) => update({ driver: event.target.value as Draft['driver'] })} disabled={driverLocked} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs disabled:bg-slate-50">
                {!driverOptions.some((option) => option.value === draft.driver) && <option value={draft.driver}>{draft.driver}</option>}
                {driverOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="block text-xs text-text-secondary">Base URL
              <input data-testid="provider-base-url" value={draft.baseUrl} onChange={(event) => update({ baseUrl: event.target.value })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="https://api.example.com/v1" autoComplete="url" />
            </label>
          </div>
          <span className="block text-[10px] text-text-muted">{driverLocked ? '内置供应商的协议由代码锁定，不可更改。' : '协议随预置自动选定，也可手动修改。'}</span>

          <BaseUrlAdvisor diagnosis={baseUrlDiagnosis} busy={busy} onUsePreset={onPresetChange} />

          {placeholder && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">地址里的 {'{'} {placeholder} {'}'} 是占位符，必须替换成你自己的取值才能测试。</div>}

          <label className="block text-xs text-text-secondary">API Key
            <input ref={apiKeyRef} data-testid="provider-api-key" type="password" value={draft.apiKey || ''} onChange={(event) => update({ apiKey: event.target.value || undefined, clearApiKey: false })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" autoComplete="new-password" placeholder={draft.credentialConfigured ? `已配置 ${maskSecret(draft.keyLast4, null) || '****'}，留空则保持不变` : '输入你的 API Key'} />
          </label>

          <ModelCatalogPicker
            value={draft.modelName}
            modelKind={draft.modelKind}
            disabled={busy}
            catalog={catalog}
            loading={catalogLoading}
            error={catalogError}
            open={catalogOpen}
            onToggle={setCatalogOpen}
            onLoad={() => { void loadCatalog() }}
            onChange={changeModelName}
          />
          {occupiedBy && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">模型名「{trimmedName}」已属于供应商「{occupiedBy.displayName}」。模型名全局唯一，保存会被拒绝 —— 请换个名字，或改去那个供应商下追加。</div>}

          <label className="block text-xs text-text-secondary">模型用途
            <select data-testid="provider-model-kind" value={kindLocked ? currentRegistered!.modelKind : draft.modelKind} onChange={(event) => update({ modelKind: event.target.value as ModelKind })} disabled={kindLocked} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs disabled:bg-slate-50">
            <option value="chat">文本模型（对话 / 评测 / 文档处理）</option>
            <option value="embedding">向量模型（Embedding）</option>
            <option value="rerank">重排模型（Rerank）</option>
            <option value="vision">视觉模型（Vision）</option>
            <option value="speech">语音模型（Speech）</option>
            </select>
            <span className="mt-1 block text-[10px] text-text-muted">用途决定可绑定的角色和测试接口，协议仍由供应商配置决定。</span>
          </label>
          {currentRegistered && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">「{currentRegistered.name}」已登记为{modelKindLabel(currentRegistered.modelKind)}，用途不可更改。要换用途请改用另一个模型名。</div>}

          {!isNew && registeredModels.length > 0 && <div className="rounded-lg border border-slate-100 px-3 py-2">
            <div className="text-[11px] text-text-secondary">该供应商已登记 {registeredModels.length} 个模型</div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">{registeredModels.map((model) => <span key={model.name} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-text-muted">{model.name} · {modelKindLabel(model.modelKind)}</span>)}</div>
            <div className="mt-1.5 text-[10px] text-text-muted">上面的「模型名称」填的是要新增的模型；填一个未登记过的名字就是追加，原有模型都会保留。</div>
          </div>}

          <details className="rounded-lg border border-slate-100 px-3 py-2 text-xs text-text-secondary">
            <summary className="cursor-pointer select-none">高级设置</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label>网络范围<select value={draft.networkScope} onChange={(event) => update({ networkScope: event.target.value as Draft['networkScope'] })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs"><option value="public">公网</option><option value="private">内网（显式放行）</option></select></label>
              <label>计费口径<select data-testid="provider-billing" value={draft.billing} onChange={(event) => update({ billing: event.target.value as Draft['billing'] })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs"><option value="metered">按量计费</option><option value="subscription">订阅制</option><option value="local">本地</option></select>
                <span className="mt-1 block text-[10px] text-text-muted">默认由计费计划派生（Token Plan / Coding Plan → 订阅制），可覆盖。</span>
              </label>
              <label className="md:col-span-2">测试深度<select data-testid="provider-probe-depth" value={probeDepth} onChange={(event) => setProbeDepth(event.target.value as ProbeMode)} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs"><option value="fast">快速 —— 只确认能调用（推荐）</option><option value="full">完整 —— 额外检查流式是否回传 usage</option></select>
                <span className="mt-1 block text-[10px] text-text-muted">「流式 usage」只影响记账能否拿到 token 数，与模型能不能用无关，所以完整测试更慢却不一定更有用。</span>
              </label>
            </div>
            {!isNew && draft.credentialConfigured && <label className="mt-3 flex items-center gap-2 text-[11px] text-red-700"><input type="checkbox" checked={Boolean(draft.clearApiKey)} onChange={(event) => update({ clearApiKey: event.target.checked, apiKey: undefined })} />清除托管 API Key</label>}
          </details>

          {draft.clearApiKey && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">保存后将清除当前托管密钥。</div>}
          {testingMode && <div role="status" className="rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 text-[11px] text-accent">正在{probeModeLabel(testingMode)}连接 · 已耗时 {formatLiveElapsed(testingElapsedMs)}</div>}
          {probeResult && <ProbeResultDetails result={probeResult} fixHints={fixHints} onFix={handleFix} />}
          {probeError && <ErrorNote message={probeError} />}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button disabled={busy} onClick={onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
          <button data-testid="provider-test" disabled={busy} onClick={() => onTest(probeDepth)} className="flex items-center gap-1 rounded-lg border border-accent/30 px-3 py-2 text-xs text-accent disabled:opacity-50"><FlaskConical size={13} />{testingMode ? `${probeModeLabel(testingMode)}中 ${formatLiveElapsed(testingElapsedMs)}` : probeDepth === 'full' ? '完整测试' : '测试连接'}</button>
          <button disabled={busy} onClick={onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Save size={13} />{isNew ? '测试并保存' : '保存'}</button>
        </div>
      </div>
    </div>
  )
}
