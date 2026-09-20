'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { CheckCircle2, Edit3, FlaskConical, KeyRound, LockKeyhole, Plus, Save, Trash2, X } from 'lucide-react'
import {
  addProviderModel,
  createProvider,
  removeProviderModel,
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
  type PlanId,
  type PresetPlan,
  type ProviderPreset,
  type ProviderRow,
} from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  providers: ProviderRow[]
  defaultModels: Record<string, string>
  source: 'db' | 'builtin'
  canAdmin: boolean
  onChanged: () => Promise<unknown>
  /** 计费计划「三选一」选项（来自后端预置目录接口）。 */
  plans: PresetPlan[]
  /** 预置端点目录。为空表示接口不可用，抽屉降级为手填 Base URL。 */
  presets: ProviderPreset[]
  presetsLoading: boolean
}

type Draft = {
  id: string | null
  displayName: string
  driver: ProviderRow['driver']
  baseUrl: string
  modelName: string
  modelKind: ModelKind
  networkScope: ProviderRow['networkScope']
  billing: ProviderRow['billing']
  enabled: boolean
  /** 计费计划（三选一）。`''` = 未套用预置（自建 / 内网 / 目录不可用）。 */
  plan: PlanId | ''
  /** 命中的预置条目 id；`null` = 自定义或未命中。 */
  presetId: string | null
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

/** 移除模型前的确认态。后端对内置/被占用的模型会回 409，理由直接展示。 */
type ModelRemoval = {
  provider: ProviderRow
  name: string
  modelKind: ModelKind
  busy: boolean
  error: string | null
}

/** 该模型能否被移除，以及不能的原因（用于就地禁用按钮并说明）。 */
function modelRemovalBlockReason(model: {
  source?: 'user' | 'builtin'
  usedByRoles?: string[]
}): string | null {
  const roles = model.usedByRoles ?? []
  if (roles.length) return `正被角色 ${roles.join('、')} 使用，需先改绑`
  if (model.source !== 'user') return '代码层内置模型，不可移除'
  return null
}

function modelKindShortLabel(kind: ModelKind): string {
  if (kind === 'embedding') return '向量'
  if (kind === 'rerank') return '重排'
  if (kind === 'vision') return '视觉'
  if (kind === 'speech') return '语音'
  return '文本'
}

function modelKindBadgeClass(kind: ModelKind): string {
  if (kind === 'embedding') return 'bg-indigo-50 text-indigo-700'
  if (kind === 'rerank') return 'bg-amber-50 text-amber-700'
  if (kind === 'vision') return 'bg-sky-50 text-sky-700'
  if (kind === 'speech') return 'bg-rose-50 text-rose-700'
  return 'bg-slate-100 text-text-muted'
}

function probeStatusLabel(status: ProbeStatus): string {
  if (status === 'pass') return '通过'
  if (status === 'fail_degraded') return '降级跳过'
  if (status === 'skip') return '跳过'
  return '失败'
}

/** 计划 → 推荐 billing。
 *
 *  与后端 `provider_presets.PLAN_BILLING` 同口径（设计文档 B.2/B.3 已拍板）：
 *  Token Plan 与 Coding Plan 都是预付/订阅制 → `subscription`，
 *  只有按量付费 → `metered`。**计划不是第 4 个 billing 值**，别在这里造新值。
 */
function billingForPlan(plans: PresetPlan[], plan: Draft['plan']): ProviderRow['billing'] | null {
  if (!plan) return null
  return plans.find((item) => item.id === plan)?.billing ?? null
}

/** 预置条目在下拉里的文案：「厂商 · 地域/版本 · 协议」。 */
function presetOptionLabel(preset: ProviderPreset): string {
  return [preset.vendor, preset.variant, preset.driverLabel].filter(Boolean).join(' · ')
}

/** 预置回填的显示名。
 *
 *  同厂同计划下常有两个协议的条目，显示名必须能区分，
 *  否则落库时 slug 会撞成 `xxx-2`（`_provider_slug` 的去重后缀）。
 */
function presetDisplayName(preset: ProviderPreset, planLabel: string): string {
  const base = [preset.vendor, preset.variant, planLabel].filter(Boolean).join(' · ')
  return preset.driver === 'anthropic' ? `${base}（Anthropic）` : base
}

/** 归一化 base URL 以便反查预置。
 *
 *  与后端 `provider_presets.normalize_base_url` 同口径：去尾斜杠、
 *  scheme/host 转小写，但**不折叠路径语义** —— `/api/v3` 与
 *  `/api/coding/v3` 归一化后必须仍然不同，否则回填会串端点。
 */
function normalizeBaseUrl(raw: string): string {
  const trimmed = (raw || '').trim()
  if (!trimmed) return ''
  const marker = trimmed.indexOf('://')
  if (marker < 0) return trimmed.replace(/\/+$/, '')
  const scheme = trimmed.slice(0, marker).toLowerCase()
  const rest = trimmed.slice(marker + 3)
  const slash = rest.indexOf('/')
  if (slash < 0) return `${scheme}://${rest.toLowerCase()}`.replace(/\/+$/, '')
  const host = rest.slice(0, slash).toLowerCase()
  const path = rest.slice(slash + 1).replace(/\/+$/, '')
  return (path ? `${scheme}://${host}/${path}` : `${scheme}://${host}`).replace(/\/+$/, '')
}

/** 按（协议, base_url）反查命中的预置。
 *
 *  编辑态没有 preset id 可读（库里不存），只能反查。`billing` 用于消歧：
 *  智谱 `open.bigmodel.cn/api/anthropic` 在 Coding Plan 与按量付费下是同一
 *  URL，靠实例自身的 billing 才能选出正确计划。**不做模糊匹配** ——
 *  匹配不到就返回 null，界面落「未套用预置」，不把自建地址误标成官方端点。
 */
function findPresetForRow(
  row: ProviderRow,
  presets: ProviderPreset[],
  plans: PresetPlan[],
): ProviderPreset | null {
  const target = normalizeBaseUrl(row.baseUrl)
  if (!target) return null
  const candidates = presets.filter(
    (preset) => preset.driver === row.driver && normalizeBaseUrl(preset.baseUrl) === target,
  )
  if (candidates.length === 0) return null
  return candidates.find((preset) => billingForPlan(plans, preset.plan) === row.billing) ?? candidates[0]
}

/** base_url 里还没被替换的占位符名（如 `WorkspaceId`）。
 *
 *  阿里云百炼按量付费用的是业务空间专属域名，照抄模板必然在 L0 就挂 ——
 *  与其让用户等一次失败的探测，不如提交前就点出来。
 */
function unresolvedPlaceholder(baseUrl: string): string | null {
  const match = baseUrl.match(/\{([^}]+)\}/)
  return match ? match[1] : null
}

/** base_url 的 host（小写）。解析不出返回 ''。 */
function baseUrlHost(raw: string): string {
  const trimmed = (raw || '').trim()
  const marker = trimmed.indexOf('://')
  if (marker < 0) return ''
  const rest = trimmed.slice(marker + 3)
  const slash = rest.indexOf('/')
  return (slash < 0 ? rest : rest.slice(0, slash)).toLowerCase()
}

/** base_url 的 path（含前导 `/`）。根路径与无路径一律返回 ''。 */
function baseUrlPath(raw: string): string {
  const trimmed = (raw || '').trim()
  const marker = trimmed.indexOf('://')
  const rest = marker < 0 ? trimmed : trimmed.slice(marker + 3)
  const slash = rest.indexOf('/')
  if (slash < 0) return ''
  return rest.slice(slash).replace(/\/+$/, '')
}

/** 预置目录里 OpenAI 兼容端点的路径样本（按出现频次取前几个）。
 *
 *  用于把「你这个路径在目录里没有先例」说成**可核对的事实**，
 *  而不是让界面瞎猜厂商规范。
 */
function openaiPathSamples(presets: ProviderPreset[]): string[] {
  const counts = new Map<string, number>()
  for (const item of presets) {
    if (item.driver !== 'openai') continue
    const path = baseUrlPath(item.baseUrl)
    if (!path) continue
    counts.set(path, (counts.get(path) ?? 0) + 1)
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([path]) => path)
}

/** base_url 诊断结论。每一种都由预置目录推导，不内置厂商知识。 */
type BaseUrlDiagnosis =
  /** 地址为空，不打扰。 */
  | { kind: 'empty' }
  /** 与某条预置逐字一致（协议也一致）。 */
  | { kind: 'matched'; preset: ProviderPreset; label: string }
  /** 地址命中预置，但那条属于**别的计费计划** —— 「按量端点用在 Coding Plan 上」会多花钱。 */
  | {
      kind: 'plan-mismatch'
      hit: ProviderPreset
      hitLabel: string
      hitPlanLabel: string
      currentPlanLabel: string
      /** 一键切回本计划端点的目标；无从确定时为 null（只警告不动手）。 */
      restore: ProviderPreset | null
    }
  /** 用户先选了预置、又手改了地址 —— 预置信息仍然挂在界面上，必须显式标出偏离。 */
  | { kind: 'deviated'; preset: ProviderPreset; label: string }
  /** 域名在预置里，但路径不是收录值：给出候选端点让用户选，而不是替他猜。 */
  | { kind: 'suggest'; host: string; candidates: Array<{ preset: ProviderPreset; label: string }> }
  /** 域名不认识，但路径形如 `/api/vN` 这种厂商原生版本号前缀 —— 只提示，不断言。 */
  | { kind: 'suspect'; path: string; samples: string[]; total: number }
  /** 查无所获：自建网关的常态，不渲染任何东西。 */
  | { kind: 'custom' }

function presetLabelFor(preset: ProviderPreset, plans: PresetPlan[]): string {
  const planLabel = plans.find((item) => item.id === preset.plan)?.label ?? ''
  return presetDisplayName(preset, planLabel)
}

/** 路径形态在预置目录里**从未出现**的疑似原生前缀（如 `/api/v1`）。
 *
 *  刻意做得很窄：只在路径命中「`/api/` + 版本号」这种厂商原生前缀形状、
 *  且目录里确实没有同写法时才提示。宁可漏报 —— 把合法的自建网关误报成
 *  错误，比不提示更糟；而这个形状恰好是本次 404 事故的输入。
 *
 *  背景：这类地址下 `GET {base}/models` 往往**照样 200**（原生路由也在），
 *  于是 L1 给绿灯，直到 L2 最小调用才 404，且错误名还被 SDK 伪装成
 *  `ModelNotFound` —— 用户会一头扎进「模型名对不对」，而问题在路径。
 */
function suspectForeignPath(url: string, presets: ProviderPreset[]): BaseUrlDiagnosis | null {
  const path = baseUrlPath(url)
  if (!/^\/api\/v\d+$/.test(path)) return null
  if (presets.some((item) => baseUrlPath(item.baseUrl) === path)) return null
  return { kind: 'suspect', path, samples: openaiPathSamples(presets), total: presets.length }
}

/** 地址诊断。
 *
 *  存在的意义是堵住一类真实事故 —— 把厂商**原生协议**前缀当成 OpenAI
 *  兼容基址填进来（见 `suspectForeignPath` 的注释），以及「先套预置、
 *  再手改地址」之后界面继续拿预置的 Key 格式与端点口径误导用户。
 */
function diagnoseBaseUrl(
  draft: Draft,
  presets: ProviderPreset[],
  plans: PresetPlan[],
): BaseUrlDiagnosis {
  const url = normalizeBaseUrl(draft.baseUrl)
  if (!url) return { kind: 'empty' }
  if (presets.length === 0) return { kind: 'custom' }

  const selected = presets.find((item) => item.id === draft.presetId) ?? null
  // 协议也算匹配条件：同一 URL 的 openai 与 anthropic 条目是两回事。
  const sameDriver = presets.filter(
    (item) => item.driver === draft.driver && normalizeBaseUrl(item.baseUrl) === url,
  )
  if (sameDriver.length > 0) {
    const hit = selected && sameDriver.some((item) => item.id === selected.id) ? selected : sameDriver[0]
    const label = presetLabelFor(hit, plans)
    // 命中预置不代表用对端点：同一域名下按量付费用 `/api/v3`、Coding Plan 用
    // `/api/coding/v3`，两条都是合法预置。若命中的那条不属于当前所选计划，
    // 说明用户正把另一个计划的端点拿来用 —— 官方口径是「用错会产生额外费用」，
    // 界面必须点名，不能只给一个「已匹配」的绿灯。
    if (draft.plan && hit.plan !== draft.plan) {
      const backToSelected = selected && selected.id !== hit.id ? selected : null
      const host = baseUrlHost(url)
      return {
        kind: 'plan-mismatch',
        hit,
        hitLabel: label,
        hitPlanLabel: plans.find((item) => item.id === hit.plan)?.label ?? hit.plan,
        currentPlanLabel: plans.find((item) => item.id === draft.plan)?.label ?? draft.plan,
        // 切回本计划端点的目标，按可靠性依次找：
        // ① 用户原本选中的那条（最常见：选了预置又手改地址）；
        // ② 同域名 + 同协议 + 本计划的条目（「先选计划、再粘贴地址」时 presetId
        //    为空，只能靠这条 —— 它必须按**域名**找，不能只在 URL 相同的预置里找，
        //    否则按钮在最需要的时候恰好消失）；
        // ③ 找不到就不给按钮，只警告 —— 不猜。
        restore: backToSelected
          ?? presets.find((item) =>
            item.plan === draft.plan
            && item.driver === draft.driver
            && baseUrlHost(item.baseUrl) === host
            && item.id !== hit.id,
          )
          ?? null,
      }
    }
    return { kind: 'matched', preset: hit, label }
  }

  // 先选预置、又改了地址：这是最需要点名的一种状态 —— 界面上其余字段
  // （Key 格式、计费口径、显示名）说的都还是那条预置。
  if (selected) {
    return { kind: 'deviated', preset: selected, label: presetLabelFor(selected, plans) }
  }

  const host = baseUrlHost(url)
  const path = baseUrlPath(url)
  const candidates = presets.filter(
    (item) => baseUrlHost(item.baseUrl) === host && baseUrlPath(item.baseUrl) !== path,
  )
  if (candidates.length > 0) {
    // 排序只为把「你已经在用的计划 / 协议」排前面，**不裁定唯一正解** ——
    // 同一域名常有两三个端点（如火山 `/api/v3` 与 `/api/coding/v3`），
    // 谁对取决于计费计划，界面不该替用户拍板，故全部列为可点候选。
    const rank = (item: ProviderPreset) =>
      (item.plan === draft.plan ? 4 : 0) + (item.driver === draft.driver ? 2 : 0)
    const ranked = [...candidates]
      .sort((a, b) => rank(b) - rank(a))
      .map((preset) => ({ preset, label: presetLabelFor(preset, plans) }))
    return { kind: 'suggest', host, candidates: ranked.slice(0, 3) }
  }

  return suspectForeignPath(url, presets) ?? { kind: 'custom' }
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

function AdvisorButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode
  onClick: () => void
  disabled: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded-md border border-amber-300 bg-white px-2 py-1 text-left font-mono text-[10px] text-amber-900 hover:bg-amber-100 disabled:opacity-50"
    >
      {children}
    </button>
  )
}

/** Base URL 旁的地址助手。
 *
 *  只做四件事：指出「已偏离预置」并给一键还原、指出「这个地址是另一个计费
 *  计划的端点」、指出「同域名下的收录端点」并给一键替换、指出「这个路径在
 *  目录里没有先例」。**不校验、不拦截、不替用户拍板** —— 自建网关可以用
 *  任意地址，把合法输入判成错误比漏报更糟。
 */
function BaseUrlAdvisor({
  diagnosis,
  busy,
  onUsePreset,
}: {
  diagnosis: BaseUrlDiagnosis
  busy: boolean
  onUsePreset: (presetId: string) => void
}) {
  if (diagnosis.kind === 'empty' || diagnosis.kind === 'custom') return null

  if (diagnosis.kind === 'matched') {
    return (
      <div data-testid="base-url-advisor" className="rounded-lg border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-[11px] text-emerald-800">
        地址与预置「{diagnosis.label}」一致。
      </div>
    )
  }

  if (diagnosis.kind === 'plan-mismatch') {
    const restore = diagnosis.restore
    return (
      <div data-testid="base-url-advisor" data-kind="plan-mismatch" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">这个地址是「{diagnosis.hitPlanLabel}」的端点</div>
        <div className="mt-1 break-all text-amber-800">
          它与预置「{diagnosis.hitLabel}」一致，但那条属于<span className="font-medium">{diagnosis.hitPlanLabel}</span>，而当前选的是「{diagnosis.currentPlanLabel}」。
        </div>
        <div className="mt-1 text-amber-700">官方口径：按量付费与 Token Plan / Coding Plan 走的是不同端点，用错会产生额外费用。</div>
        {restore && (
          <div className="mt-2">
            <AdvisorButton disabled={busy} onClick={() => onUsePreset(restore.id)}>
              改用「{diagnosis.currentPlanLabel}」端点 · {baseUrlPath(restore.baseUrl) || '/'}
            </AdvisorButton>
          </div>
        )}
      </div>
    )
  }

  if (diagnosis.kind === 'deviated') {
    return (
      <div data-testid="base-url-advisor" data-kind="deviated" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">地址已偏离预置「{diagnosis.label}」</div>
        <div className="mt-1 break-all text-amber-800">预置原值是 <span className="font-mono">{diagnosis.preset.baseUrl}</span></div>
        <div className="mt-1 text-amber-700">偏离后，上方「API Key 格式」与计费口径说的仍是那条预置，可能不再适用；探测失败时请优先怀疑这个地址。</div>
        <div className="mt-2">
          <AdvisorButton disabled={busy} onClick={() => onUsePreset(diagnosis.preset.id)}>还原为预置地址</AdvisorButton>
        </div>
      </div>
    )
  }

  if (diagnosis.kind === 'suggest') {
    return (
      <div data-testid="base-url-advisor" data-kind="suggest" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <div className="font-medium">域名 <span className="font-mono">{diagnosis.host}</span> 在预置目录里是这些端点</div>
        <div className="mt-1 text-amber-700">同一域名的不同端点往往对应不同协议或计费计划，用错端点会产生额外费用。</div>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {diagnosis.candidates.map(({ preset, label }) => (
            <AdvisorButton key={preset.id} disabled={busy} onClick={() => onUsePreset(preset.id)}>
              {label} · {baseUrlPath(preset.baseUrl) || '/'}
            </AdvisorButton>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div data-testid="base-url-advisor" data-kind="suspect" className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-text-secondary">
      <div className="font-medium text-text-primary">路径 <span className="font-mono">{diagnosis.path}</span> 在预置目录里没有先例</div>
      <div className="mt-1">
        收录的 {diagnosis.total} 条预置中，
        {diagnosis.samples.length > 0
          ? <>OpenAI 兼容的路径形如 {diagnosis.samples.map((item) => `「${item}」`).join('、')}。</>
          : <>没有任何一条用这种写法。</>}
        自建网关可以用任意路径，所以这不代表填错。
      </div>
      <div className="mt-1 text-text-muted">
        但若这是照厂商文档抄的<span className="font-medium">原生协议</span>地址，
        <span className="font-mono">GET {'{base}'}/models</span> 往往照样通过，直到最小调用才报「404 且响应体为空」—— 届时先回来检查这里的路径。
      </div>
    </div>
  )
}

function draftFromRow(
  row: ProviderRow,
  defaultModels: Record<string, string>,
  presets: ProviderPreset[],
  plans: PresetPlan[],
): Draft {
  const modelName = row.modelName || defaultModels[row.id] || ''
  const modelKind = row.modelKind || row.models?.find((item) => item.name === modelName)?.modelKind || 'chat'
  // 计划与厂商由（协议, base_url）反查回填。库未就绪或地址是自建时反查落空，
  // 此时 plan='' / presetId=null，界面显示「未套用预置」，不假装它属于某家厂商。
  const matched = findPresetForRow(row, presets, plans)
  return {
    id: row.id,
    displayName: row.displayName,
    driver: row.driver,
    baseUrl: row.baseUrl,
    modelName,
    modelKind,
    networkScope: row.networkScope,
    billing: row.billing,
    enabled: row.enabled,
    plan: matched?.plan ?? '',
    presetId: matched?.id ?? null,
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
    displayName: '',
    driver: 'openai',
    baseUrl: '',
    modelName: '',
    modelKind: 'chat',
    networkScope: 'public',
    billing: 'metered',
    enabled: true,
    plan: '',
    presetId: null,
    originalBaseUrl: '',
    originalModelName: '',
    originalModelKind: 'chat',
    credentialConfigured: false,
    keyLast4: null,
  }
}

export default function ProvidersTab({
  providers,
  defaultModels,
  source,
  canAdmin,
  onChanged,
  plans,
  presets,
  presetsLoading,
}: Props) {
  const toast = useToast()
  const [editing, setEditing] = useState<Draft | null>(null)
  const [addingModel, setAddingModel] = useState<ModelDraft | null>(null)
  const [removingModel, setRemovingModel] = useState<ModelRemoval | null>(null)
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
    setEditing(draftFromRow(row, defaultModels, presets, plans))
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

  function beginRemoveModel(row: ProviderRow, model: { name: string; modelKind: ModelKind }) {
    setRemovingModel({
      provider: row,
      name: model.name,
      modelKind: model.modelKind,
      busy: false,
      error: null,
    })
  }

  async function confirmRemoveModel() {
    if (!removingModel) return
    setRemovingModel({ ...removingModel, busy: true, error: null })
    try {
      await removeProviderModel(removingModel.provider.id, removingModel.name)
      toast.success(`已移除模型 ${removingModel.name}`)
      setRemovingModel(null)
      await onChanged()
    } catch (error) {
      // 不弹 toast：409 的原因（被哪个角色占用）就在弹窗里，用户要能对着看
      const message = error instanceof Error ? error.message : '移除模型失败'
      setRemovingModel({ ...removingModel, busy: false, error: message })
    }
  }

  /** 切换计费计划（三选一）。
   *
   *  若当前地址是「上一个计划的预置」带出来的，必须一并清掉 —— 留着它
   *  正是本功能要消灭的事故：把火山引擎按量 `/api/v3` 用在 Coding Plan 上。
   *  手填的自定义地址则保留，不丢用户输入。
   */
  function applyPlan(nextPlan: PlanId | '') {
    setEditing((current) => {
      if (!current) return current
      const cameFromPreset = current.presetId !== null
      return {
        ...current,
        plan: nextPlan,
        presetId: null,
        baseUrl: cameFromPreset ? '' : current.baseUrl,
        billing: billingForPlan(plans, nextPlan) ?? current.billing,
      }
    })
    resetProbe()
  }

  /** 选中预置条目：一次回填地址、协议、显示名与推荐计费口径。 */
  function applyPreset(presetId: string) {
    setEditing((current) => {
      if (!current) return current
      if (presetId === '__custom') return { ...current, presetId: null }
      const preset = presets.find((item) => item.id === presetId)
      if (!preset) return current
      const planLabel = plans.find((item) => item.id === preset.plan)?.label ?? ''
      return {
        ...current,
        plan: preset.plan,
        presetId: preset.id,
        driver: preset.driver,
        baseUrl: preset.baseUrl,
        displayName: presetDisplayName(preset, planLabel),
        billing: billingForPlan(plans, preset.plan) ?? current.billing,
      }
    })
    resetProbe()
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
    const placeholder = unresolvedPlaceholder(baseUrl)
    if (placeholder) {
      const message = `请先把 Base URL 里的 {${placeholder}} 替换为你的实际取值`
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
    const placeholder = unresolvedPlaceholder(editing.baseUrl.trim())
    if (placeholder) {
      const message = `请先把 Base URL 里的 {${placeholder}} 替换为你的实际取值`
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
          <p className="mt-1 text-[11px] text-text-muted">只列出已配置的供应商；每个供应商下平铺它已登记的模型。自建模型可移除，代码层内置模型与被角色占用的模型会就地标明原因。</p>
        </div>
        <div className="flex items-center gap-2">
          {source !== 'db' && <span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] text-amber-700">DB 未就绪</span>}
          {canAdmin && source === 'db' && <button onClick={beginNew} className="flex items-center gap-1 rounded-lg bg-accent px-3 py-2 text-[11px] text-white hover:bg-accent/90"><Plus size={13} />新增供应商</button>}
        </div>
      </div>
        <div className="divide-y divide-slate-100">
          {providers.map((row) => {
            const liveResult = probeResults[row.id]
            const liveError = probeErrors[row.id]
            const isTesting = testingTarget === row.id
            const visibleModels: NonNullable<ProviderRow['models']> = row.models?.length
              ? row.models
              : [{
                  name: row.modelName || defaultModels[row.id] || '—',
                  modelKind: (row.modelKind || 'chat') as ModelKind,
                  source: 'builtin' as const,
                  usedByRoles: [],
                }]
            return (
              <div key={row.id} data-testid="provider-card" className="px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-text-primary">{row.displayName}</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-muted">{row.isBuiltin ? '内置' : '自建'}</span>
                      <span className="rounded bg-accent/10 px-1.5 py-0.5 font-mono text-[10px] text-accent">{row.driver}</span>
                      <span className="text-[10px] text-text-muted">{row.networkScope === 'private' ? '内网白名单' : '公网'} · {row.billing}</span>
                    </div>
                    <div className="mt-1.5 truncate font-mono text-[10px] text-text-secondary">{row.baseUrl || '跟随环境变量 / 默认地址'}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-text-muted">
                      <span className="font-mono">{row.id}</span>
                      {row.credential.configured
                        ? <span className="flex items-center gap-1 text-emerald-700"><LockKeyhole size={11} />密钥已配置 <span className="font-mono">{maskSecret(row.credential.last4, null) || '****'}</span></span>
                        : <span>未托管密钥（可走环境变量）</span>}
                      {row.credential.fingerprint && <span className="font-mono">指纹 {row.credential.fingerprint}</span>}
                      {isTesting
                        ? <span className="text-accent">正在{probeModeLabel(testingMode || 'fast')} · 已耗时 {formatLiveElapsed(testingElapsedMs)}</span>
                        : row.lastProbe
                          ? <span className={row.lastProbe.ok ? 'flex items-center gap-1 text-emerald-700' : 'text-red-700'}>
                              {row.lastProbe.ok && <CheckCircle2 size={11} />}
                              {row.lastProbe.ok ? '已通过' : `卡在 ${row.lastProbe.worstGrade || '未知'}`}
                            </span>
                          : <span>未验证</span>}
                    </div>
                  </div>
                  <div className="flex flex-shrink-0 flex-wrap justify-end gap-1.5">
                    <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'fast') }} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-text-secondary disabled:opacity-40"><FlaskConical size={12} />{isTesting && testingMode === 'fast' ? `测试中 ${formatLiveElapsed(testingElapsedMs)}` : '测试'}</button>
                    <button disabled={!canAdmin || busy || source !== 'db'} onClick={() => { void testSaved(row, 'full') }} className="flex items-center gap-1 rounded-lg border border-accent/20 px-2.5 py-1.5 text-[11px] text-accent disabled:opacity-40">{isTesting && testingMode === 'full' ? `完整测试中 ${formatLiveElapsed(testingElapsedMs)}` : '完整测试'}</button>
                    {canAdmin && source === 'db' && <><button onClick={() => beginAddModel(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Plus size={12} />新增模型</button><button onClick={() => begin(row)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent hover:bg-accent/5"><Edit3 size={12} />编辑</button></>}
                  </div>
                </div>

                <div className="mt-3 overflow-hidden rounded-lg border border-slate-100">
                  {visibleModels.map((model) => {
                    const blockReason = modelRemovalBlockReason(model)
                    return (
                      <div key={model.name} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-50 px-3 py-2 last:border-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] ${modelKindBadgeClass(model.modelKind)}`}>{modelKindShortLabel(model.modelKind)}</span>
                          <span className="truncate font-mono text-[11px] text-text-secondary">{model.name}</span>
                          {model.source !== 'user' && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-text-muted">内置</span>}
                        </div>
                        <div className="flex flex-shrink-0 items-center gap-2">
                          {blockReason && <span className="text-[10px] text-text-muted">{blockReason}</span>}
                          {canAdmin && source === 'db' && <button disabled={Boolean(blockReason)} title={blockReason ?? undefined} aria-label={`移除模型 ${model.name}`} onClick={() => beginRemoveModel(row, model)} className="flex items-center gap-1 rounded-lg border border-black/10 px-2 py-1 text-[11px] text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"><Trash2 size={11} />移除</button>}
                        </div>
                      </div>
                    )
                  })}
                </div>

                {(liveResult || liveError || (!isTesting && row.lastProbe && !row.lastProbe.ok)) && (
                  <div className="mt-2 space-y-2">
                    {liveResult && <ProbeResultDetails result={liveResult} />}
                    {liveError && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-[10px] text-red-800">{liveError}</div>}
                    {!liveResult && !liveError && !isTesting && row.lastProbe && !row.lastProbe.ok && row.lastProbe.worstGrade && (
                      <div className="rounded-lg border border-red-100 bg-red-50/60 px-2.5 py-2 text-[10px] text-red-700">建议：{probeFallbackSummary(row.lastProbe.worstGrade, 'fail')}</div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {!providers.length && <div className="px-4 py-10 text-center text-xs text-text-muted">暂无供应商配置</div>}
        </div>

      {editing && <ProviderEditor
        draft={editing}
        providers={providers}
        plans={plans}
        presets={presets}
        presetsLoading={presetsLoading}
        busy={busy}
        setDraft={setEditing}
        onPlanChange={applyPlan}
        onPresetChange={applyPreset}
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
      {removingModel && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4 py-6" role="dialog" aria-modal="true" aria-label={`移除模型 ${removingModel.name}`}>
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-sm font-semibold text-text-primary"><Trash2 size={15} className="text-red-700" />移除模型</div>
                <p className="mt-1 text-[11px] text-text-muted">供应商：{removingModel.provider.displayName}</p>
              </div>
              <button onClick={() => setRemovingModel(null)} className="text-text-muted hover:text-text-primary" aria-label="关闭"><X size={16} /></button>
            </div>
            <div className="mt-5 space-y-3">
              <div className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                <span className={`rounded px-1.5 py-0.5 text-[10px] ${modelKindBadgeClass(removingModel.modelKind)}`}>{modelKindShortLabel(removingModel.modelKind)}</span>
                <span className="font-mono text-xs text-text-secondary">{removingModel.name}</span>
              </div>
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">移除后该模型从供应商目录消失。若仍被角色或价格表引用，后端会拒绝并说明原因，不会留下悬空引用。</div>
              {removingModel.error && <div className="break-words rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-800">{removingModel.error}</div>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button disabled={removingModel.busy} onClick={() => setRemovingModel(null)} className="rounded-lg border border-black/10 px-3 py-2 text-xs text-text-secondary">取消</button>
              <button disabled={removingModel.busy} onClick={() => { void confirmRemoveModel() }} className="flex items-center gap-1 rounded-lg bg-red-700 px-3 py-2 text-xs text-white disabled:opacity-50"><Trash2 size={13} />{removingModel.busy ? '移除中…' : '确认移除'}</button>
            </div>
          </div>
        </div>
      )}
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
  onFullTest,
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
  onTest: () => void
  onFullTest: () => void
  onCancel: () => void
}) {
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch })
  const isNew = !draft.id
  const row = draft.id ? providers.find((item) => item.id === draft.id) ?? null : null

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
            <input data-testid="provider-api-key" type="password" value={draft.apiKey || ''} onChange={(event) => update({ apiKey: event.target.value || undefined, clearApiKey: false })} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 font-mono text-xs" autoComplete="new-password" placeholder={draft.credentialConfigured ? `已配置 ${maskSecret(draft.keyLast4, null) || '****'}，留空则保持不变` : '输入你的 API Key'} />
          </label>

          <label className="block text-xs text-text-secondary">模型名称
            <input data-testid="provider-model-name" value={draft.modelName} onChange={(event) => changeModelName(event.target.value)} className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm" placeholder="例如：deepseek-v4-pro、qwen3.7-plus" autoComplete="off" />
          </label>
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
