'use client'

import { useEffect, useState } from 'react'
import { CheckCircle2, Edit3, FlaskConical, KeyRound, LockKeyhole, Plus, Save, X } from 'lucide-react'
import {
  addProviderModel,
  createProvider,
  saveProvider,
  verifyDraftProvider,
  verifyProvider,
  type ProbeMode,
  type ProbeResponse,
} from '@/api/modelConfig'
import {
  gradeLabel,
  modelKindLabel,
  probeFailureReason,
  probeFallbackSummary,
  probeStepSummary,
  maskSecret,
  type ModelKind,
  type ProviderRow,
} from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  providers: ProviderRow[]
  defaultModels: Record<string, string>
  source: 'db' | 'builtin'
  canAdmin: boolean
  onChanged: () => Promise<unknown>
}

type Draft = {
  id: string | null
  kind: 'custom' | 'coding' | 'provider'
  displayName: string
  driver: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind: ModelKind
  networkScope: ProviderRow['networkScope']
  billing: ProviderRow['billing']
  enabled: boolean
  apiKey?: string
  clearApiKey?: boolean
  originalBaseUrl: string
  originalModelName: string
  originalModelKind: ModelKind
  credentialConfigured: boolean
  keyLast4: string | null
}

type ProbeStatus = ProbeResponse['steps'][number]['status']

type ModelDraft = {
  provider: ProviderRow
  modelName: string
  modelKind: ModelKind
  error: string | null
}

function probeStatusLabel(status: ProbeStatus): string {
  if (status === 'pass') return '通过'
  if (status === 'fail_degraded') return '降级跳过'
  if (status === 'skip') return '跳过'
  return '失败'
}

function providerOptionLabel(row: ProviderRow): string {
  if (row.id === 'qwen_tp') return 'Token Plan（通义千问）'
  if (row.id.includes('coding')) return `Coding Plan（${row.displayName}）`
  return row.displayName
}

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatLiveElapsed(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)} 秒`
}

function probeModeLabel(mode: ProbeMode): string {
  return mode === 'full' ? '完整测试' : '快速测试'
}

function resultElapsedMs(result: ProbeResponse): number {
  return result.steps.reduce((total, step) => total + (step.elapsedMs ?? 0), 0)
}

function ProbeResultDetails({ result }: { result: ProbeResponse }) {
  const failure = probeFailureReason(result)
  const elapsedMs = resultElapsedMs(result)
  return (
    <div
      data-testid="probe-result-details"
      className={`mt-2 rounded-lg border px-2.5 py-2 text-[10px] ${result.ok ? 'border-emerald-200 bg-emerald-50/70 text-emerald-800' : 'border-red-200 bg-red-50/80 text-red-800'}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-medium">{result.ok ? '探测通过' : failure}</div>
        {elapsedMs > 0 && <span className="shrink-0 font-mono opacity-70">总耗时 {formatElapsed(elapsedMs)}</span>}
      </div>
      <details open={!result.ok} className="mt-1.5">
        <summary className="cursor-pointer select-none text-[10px] font-medium">查看探测详情</summary>
        <ol className="mt-1.5 space-y-1.5">
          {result.steps.map((step) => (
            <li key={step.grade} className="border-l border-current/20 pl-2">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{step.grade} · {gradeLabel(step.grade)} · {probeStatusLabel(step.status)}</span>
                {step.elapsedMs != null && <span className="opacity-70">{step.elapsedMs}ms</span>}
              </div>
              <div className="mt-0.5 break-words">{probeStepSummary(step)}</div>
              {step.raw && <div className="mt-0.5 break-words opacity-75">原文：{step.raw}</div>}
            </li>
          ))}
        </ol>
        {result.suggestion && <div className="mt-1.5 break-words border-t border-current/10 pt-1.5">建议：{result.suggestion}</div>}
      </details>
    </div>
  )
}

function draftFromRow(row: ProviderRow, defaultModels: Record<string, string>): Draft {
  const modelName = row.modelName || defaultModels[row.id] || ''
  const modelKind = row.modelKind || row.models?.find((item) => item.name === modelName)?.modelKind || 'chat'
  return {
    id: row.id,
    kind: 'provider',
    displayName: row.displayName,
    driver: row.driver,
    baseUrl: row.baseUrl,
    modelName,
    modelKind,
    networkScope: row.networkScope,
    billing: row.billing,
    enabled: row.enabled,
    originalBaseUrl: row.baseUrl,
    originalModelName: modelName,
    originalModelKind: modelKind,
    credentialConfigured: row.credential.configured,
    keyLast4: row.credential.last4,
  }
}

function newDraft(): Draft {
  return {
    id: null,
    kind: 'custom',
    displayName: '',
    driver: 'openai',
    baseUrl: '',
    modelName: '',
    modelKind: 'chat',
    networkScope: 'public',
    billing: 'metered',
    enabled: true,
    originalBaseUrl: '',
    originalModelName: '',
    originalModelKind: 'chat',
    credentialConfigured: false,
    keyLast4: null,
  }
}

function codingDraft(): Draft {
  return {
    ...newDraft(),
    kind: 'coding',
    displayName: 'Coding Plan',
    billing: 'subscription',
  }
}

export default function ProvidersTab({ providers, defaultModels, source, canAdmin, onChanged }: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState<Draft | null>(null)
  const [addingModel, setAddingModel] = useState<ModelDraft | null>(null)
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
    setEditing(draftFromRow(row, defaultModels))
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

  function chooseProvider(value: string) {
    if (value === '__custom') {
      setEditing(newDraft())
      resetProbe()
      return
    }
    if (value === '__coding') {
      setEditing(codingDraft())
      resetProbe()
      return
    }
    const row = providers.find((item) => item.id === value)
    if (row) {
      setEditing(draftFromRow(row, defaultModels))
      resetProbe()
    }
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
           <p className="mt-1 text-[11px] text-text-muted">文本、OCR、向量、重排、视觉和语音模型都在这里按供应商登记；同一供应商的模型会合并展示，每个模型可独立测试和追加。</p>
        </div>
        <div className="flex items-center gap-2">
          {source !== 'db' && <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">DB 未就绪</span>}
          {canAdmin && source === 'db' && <button onClick={beginNew} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-[11px] text-white hover:bg-accent/90"><Plus size={13} />新增供应商</button>}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] text-left text-xs">
          <thead>
            <tr className="border-b border-slate-100 text-[10px] text-text-muted">
              <th className="px-4 py-3">供应商</th>
              <th className="px-4 py-3">协议 / 地址</th>
              <th className="px-4 py-3">密钥</th>
              <th className="px-4 py-3">模型</th>
              <th className="px-4 py-3">探测</th>
              <th className="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((row) => {
              const liveResult = probeResults[row.id]
              const liveError = probeErrors[row.id]
              const isTesting = testingTarget === row.id
              const visibleModels = row.models?.length
                ? row.models
                : [{ name: row.modelName || defaultModels[row.id] || '—', modelKind: row.modelKind || 'chat' as ModelKind }]
              return (
                <tr key={row.id} className="border-b border-slate-50 align-top last:border-0">
                  <td className="px-4 py-3">
                    <div className="font-medium text-text-primary">{row.displayName}</div>
                    <div className="mt-0.5 font-mono text-[10px] text-text-muted">{row.id}{row.isBuiltin ? ' · 内置' : ' · 自建'}</div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-mono text-[10px] text-text-secondary">{row.driver}</div>
                    <div className="mt-1 max-w-[330px] truncate text-[11px] text-text-muted">{row.baseUrl || '跟随环境变量 / 默认地址'}</div>
                    <div className="mt-1 text-[10px] text-text-muted">{row.networkScope === 'private' ? '内网白名单' : '公网地址'} · {row.billing}</div>
                  </td>
                  <td className="px-4 py-3">
                    {row.credential.configured ? <span className="flex items-center gap-1 text-emerald-700"><LockKeyhole size={13} />已配置 <span className="font-mono text-[10px]">{maskSecret(row.credential.last4, null) || '****'}</span></span> : <span className="text-text-muted">未托管（可走环境变量）</span>}
                    {row.credential.fingerprint && <div className="mt-1 font-mono text-[10px] text-text-muted">指纹 {row.credential.fingerprint}</div>}
                  </td>
                  <td className="px-4 py-3 font-mono text-[10px] text-text-secondary">
                    <div className="mb-1 font-sans text-[10px] text-text-muted">已配置 {visibleModels.filter((model) => model.name !== '—').length} 个模型</div>
                    {visibleModels.map((model) => <div key={model.name} className="mb-1 last:mb-0"><span className="mr-1 rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-sans text-text-muted">{modelKindLabel(model.modelKind)}</span>{model.name}</div>)}
                  </td>
                  <td className="px-4 py-3">
                     {isTesting ? <div role="status" className="rounded-lg border border-accent/20 bg-accent/5 px-2.5 py-2 text-[10px] text-accent">正在{probeModeLabel(testingMode || 'fast')} · 已耗时 {formatLiveElapsed(testingElapsedMs)}</div> : liveResult ? <ProbeResultDetails result={liveResult} /> : row.lastProbe ? <>
                      <span className={row.lastProbe.ok ? 'flex items-center gap-1 text-emerald-700' : 'text-red-700'}>
                        {row.lastProbe.ok && <CheckCircle2 size={13} />}
                        {row.lastProbe.ok ? '已通过' : `卡在 ${row.lastProbe.worstGrade || '未知'}`}
                      </span>
                      {!row.lastProbe.ok && row.lastProbe.worstGrade && <div className="mt-1 max-w-[250px] text-[10px] text-red-700">建议：{probeFallbackSummary(row.lastProbe.worstGrade, 'fail')}</div>}
                    </> : <span className="text-text-muted">未验证</span>}
                    {liveError && <div className="mt-2 max-w-[280px] break-words rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-[10px] text-red-800">{liveError}</div>}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-1.5">
                      <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'fast') }} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-text-secondary disabled:opacity-40"><FlaskConical size={12} />{isTesting && testingMode === 'fast' ? `测试中 ${formatLiveElapsed(testingElapsedMs)}` : '测试'}</button>
                      <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'full') }} className="flex items-center gap-1 rounded-lg border border-accent/20 px-2.5 py-1.5 text-[11px] text-accent disabled:opacity-40">{isTesting && testingMode === 'full' ? `完整测试中 ${formatLiveElapsed(testingElapsedMs)}` : '完整测试'}</button>
                      {canAdmin && source === 'db' && <><button onClick={() => beginAddModel(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Plus size={12} />新增模型</button><button onClick={() => begin(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Edit3 size={12} />编辑</button></>}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {!providers.length && <div className="px-4 py-10 text-center text-xs text-text-muted">暂无供应商配置</div>}
      </div>

      {editing && <ProviderEditor
        draft={editing}
        providers={providers}
        busy={busy}
        setDraft={setEditing}
        onProviderChange={chooseProvider}
        probeResult={draftProbe}
        probeError={draftProbeError}
        testingMode={testingTarget === 'draft' ? testingMode : null}
        testingElapsedMs={testingElapsedMs}
        onSave={() => { void save() }}
        onTest={() => { void performDraftTest('fast') }}
        onFullTest={() => { void performDraftTest('full') }}
        onCancel={() => { setEditing(null); resetProbe() }}
      />}
      {addingModel && <ProviderModelEditor
        draft={addingModel}
        busy={modelBusy}
        setDraft={setAddingModel}
        onSave={() => { void saveAddedModel() }}
        onCancel={() => setAddingModel(null)}
      />}
      </section>
    </div>
  )
}

function ProviderModelEditor({
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

function ProviderEditor({
  draft,
  providers,
  busy,
  setDraft,
  onProviderChange,
  probeResult,
  probeError,
  testingMode,
  testingElapsedMs,
  onSave,
  onTest,
  onFullTest,
  onCancel,
}: {
  draft: Draft
  providers: ProviderRow[]
  busy: boolean
  setDraft: (value: Draft) => void
  onProviderChange: (value: string) => void
  probeResult: ProbeResponse | null
  probeError: string | null
  testingMode: ProbeMode | null
  testingElapsedMs: number
  onSave: () => void
  onTest: () => void
  onFullTest: () => void
  onCancel: () => void
}) {
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch })
  const isNew = !draft.id
  const selectedValue = draft.id || (draft.kind === 'coding' ? '__coding' : '__custom')
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={isNew ? '新增供应商' : `编辑 ${draft.displayName}`}>
      <div className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-text-primary"><KeyRound size={16} className="text-accent" />{isNew ? '新增供应商' : `编辑 ${draft.displayName}`}</div>
            <p className="mt-1 text-[11px] text-text-muted">输入 API Key 和模型名称，先快速测试连接再保存；完整测试可选。</p>
          </div>
          <button onClick={onCancel} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
        </div>

        <div className="mt-5 space-y-3">
          <label className="block text-xs text-text-secondary">供应商
            <select disabled={!isNew} value={selectedValue} onChange={(event) => onProviderChange(event.target.value)} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-sm disabled:bg-slate-50">
              <option value="__custom">自定义 API（OpenAI 兼容）</option>
              <option value="__coding">Coding Plan（OpenAI 兼容）</option>
              {providers.map((row) => <option key={row.id} value={row.id}>{providerOptionLabel(row)}</option>)}
            </select>
          </label>

          <label className="block text-xs text-text-secondary">Base URL
            <input value={draft.baseUrl} onChange={(event) => update({ baseUrl: event.target.value })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" placeholder="https://api.example.com/v1" autoComplete="url" />
          </label>

          <label className="block text-xs text-text-secondary">API Key
            <input type="password" value={draft.apiKey || ''} onChange={(event) => update({ apiKey: event.target.value || undefined, clearApiKey: false })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" autoComplete="new-password" placeholder={draft.credentialConfigured ? `已配置 ${maskSecret(draft.keyLast4, null) || '****'}，留空则保持不变` : '输入你的 API Key'} />
          </label>

          <label className="block text-xs text-text-secondary">模型名称
            <input value={draft.modelName} onChange={(event) => update({ modelName: event.target.value })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：deepseek-v4-pro、qwen3.7-plus" autoComplete="off" />
          </label>

          <label className="block text-xs text-text-secondary">模型用途
            <select value={draft.modelKind} onChange={(event) => update({ modelKind: event.target.value as ModelKind })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs">
            <option value="chat">文本模型（对话 / 评测 / 文档处理）</option>
            <option value="embedding">向量模型（Embedding）</option>
            <option value="rerank">重排模型（Rerank）</option>
            <option value="vision">视觉模型（Vision）</option>
            <option value="speech">语音模型（Speech）</option>
            </select>
            <span className="mt-1 block text-[10px] text-text-muted">用途决定可绑定的角色和测试接口，协议仍由供应商配置决定。</span>
          </label>

          <details className="rounded-lg border border-slate-100 px-3 py-2 text-xs text-text-secondary">
            <summary className="cursor-pointer select-none">高级设置</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label>网络范围<select value={draft.networkScope} onChange={(event) => update({ networkScope: event.target.value as Draft['networkScope'] })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs"><option value="public">公网</option><option value="private">内网（显式放行）</option></select></label>
              <label>计费模式<select value={draft.billing} onChange={(event) => update({ billing: event.target.value as Draft['billing'] })} className="mt-1 w-full rounded-lg border border-black/10 bg-white px-3 py-2 text-xs"><option value="metered">按量计费</option><option value="subscription">订阅制</option><option value="local">本地</option></select></label>
            </div>
            {!isNew && draft.credentialConfigured && <label className="mt-3 flex items-center gap-2 text-[11px] text-red-700"><input type="checkbox" checked={Boolean(draft.clearApiKey)} onChange={(event) => update({ clearApiKey: event.target.checked, apiKey: undefined })} />清除托管 API Key</label>}
          </details>

          {draft.clearApiKey && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">保存后将清除当前托管密钥。</div>}
          {testingMode && <div role="status" className="rounded-lg border border-accent/20 bg-accent/5 px-3 py-2 text-[11px] text-accent">正在{probeModeLabel(testingMode)}连接 · 已耗时 {formatLiveElapsed(testingElapsedMs)}</div>}
          {probeResult && <ProbeResultDetails result={probeResult} />}
          {probeError && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{probeError}</div>}
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button disabled={busy} onClick={onCancel} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
          <button disabled={busy} onClick={onTest} className="flex items-center gap-1 rounded-lg border border-accent/30 px-3 py-2 text-xs text-accent disabled:opacity-50"><FlaskConical size={13} />{testingMode === 'fast' ? `测试中 ${formatLiveElapsed(testingElapsedMs)}` : '测试连接'}</button>
          <button disabled={busy} onClick={onFullTest} className="flex items-center gap-1 rounded-lg border border-accent/20 px-3 py-2 text-xs text-accent disabled:opacity-50">{testingMode === 'full' ? `完整测试中 ${formatLiveElapsed(testingElapsedMs)}` : '完整测试'}</button>
          <button disabled={busy} onClick={onSave} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-50"><Save size={13} />{isNew ? '测试并保存' : '保存'}</button>
        </div>
      </div>
    </div>
  )
}
