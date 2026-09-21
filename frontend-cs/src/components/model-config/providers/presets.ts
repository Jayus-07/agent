/** 预置反查与 Base URL 诊断（纯函数）。
 *
 *  从 ProvidersTab.tsx（B4 拆分）原样迁出：计费计划 → billing、预置回填、
 *  地址归一化与六态诊断。**不依赖 React**，可独立单测。
 */
import type { ModelKind, PlanId, PresetPlan, ProviderPreset, ProviderRow } from '@/types/modelConfig'
import type { Draft } from './draft'

/** 计划 → 推荐 billing。
 *
 *  与后端 `provider_presets.PLAN_BILLING` 同口径（设计文档 B.2/B.3 已拍板）：
 *  Token Plan 与 Coding Plan 都是预付/订阅制 → `subscription`，
 *  只有按量付费 → `metered`。**计划不是第 4 个 billing 值**，别在这里造新值。
 */
export function billingForPlan(plans: PresetPlan[], plan: Draft['plan']): ProviderRow['billing'] | null {
  if (!plan) return null
  return plans.find((item) => item.id === plan)?.billing ?? null
}

/** 预置条目在下拉里的文案：「厂商 · 地域/版本 · 协议」。 */
export function presetOptionLabel(preset: ProviderPreset): string {
  return [preset.vendor, preset.variant, preset.driverLabel].filter(Boolean).join(' · ')
}

/** 预置回填的显示名。
 *
 *  同厂同计划下常有两个协议的条目，显示名必须能区分，
 *  否则落库时 slug 会撞成 `xxx-2`（`_provider_slug` 的去重后缀）。
 */
export function presetDisplayName(preset: ProviderPreset, planLabel: string): string {
  const base = [preset.vendor, preset.variant, planLabel].filter(Boolean).join(' · ')
  return preset.driver === 'anthropic' ? `${base}（Anthropic）` : base
}

/** 归一化 base URL 以便反查预置。
 *
 *  与后端 `provider_presets.normalize_base_url` 同口径：去尾斜杠、
 *  scheme/host 转小写，但**不折叠路径语义** —— `/api/v3` 与
 *  `/api/coding/v3` 归一化后必须仍然不同，否则回填会串端点。
 */
export function normalizeBaseUrl(raw: string): string {
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
export function findPresetForRow(
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
export function unresolvedPlaceholder(baseUrl: string): string | null {
  const match = baseUrl.match(/\{([^}]+)\}/)
  return match ? match[1] : null
}

/** base_url 的 host（小写）。解析不出返回 ''。 */
export function baseUrlHost(raw: string): string {
  const trimmed = (raw || '').trim()
  const marker = trimmed.indexOf('://')
  if (marker < 0) return ''
  const rest = trimmed.slice(marker + 3)
  const slash = rest.indexOf('/')
  return (slash < 0 ? rest : rest.slice(0, slash)).toLowerCase()
}

/** base_url 的 path（含前导 `/`）。根路径与无路径一律返回 ''。 */
export function baseUrlPath(raw: string): string {
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
export function openaiPathSamples(presets: ProviderPreset[]): string[] {
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
export type BaseUrlDiagnosis =
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

export function presetLabelFor(preset: ProviderPreset, plans: PresetPlan[]): string {
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
export function suspectForeignPath(url: string, presets: ProviderPreset[]): BaseUrlDiagnosis | null {
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
export function diagnoseBaseUrl(
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
