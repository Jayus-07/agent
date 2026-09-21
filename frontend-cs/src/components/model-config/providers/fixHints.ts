/** 探测失败后的「去修」动作（纯函数产出描述，组件负责绑定 onClick；B4 拆分迁出）。
 *
 *  一个可执行的修复动作。**刻意不带回调** —— 保持纯函数可单测。
 *
 *  地址类的动作直接带上**算好的目标地址**（`use-url`），而不是「补一段 /v1」这种指令：
 *  补与替换的差别很大（百炼要从 `/api/v1` **换成** `/compatible-mode/v1`，不是往后接），
 *  让纯函数算出最终值，组件只负责写入，也便于断言。
 */
import type { ProbeReason, ProviderPreset, ProviderRow } from '@/types/modelConfig'
import type { PresetPlan } from '@/types/modelConfig'
import { baseUrlHost, baseUrlPath, normalizeBaseUrl, presetLabelFor } from './presets'

export type FixHint =
  | { kind: 'use-preset'; presetId: string; label: string }
  | { kind: 'use-url'; baseUrl: string; label: string }
  | { kind: 'open-catalog'; label: string }
  | { kind: 'focus-api-key'; label: string }

function baseUrlOrigin(raw: string): string {
  try {
    const url = new URL(raw.trim())
    return `${url.protocol}//${url.host}`
  } catch {
    return ''
  }
}

/** 由**失败归因代号**推出可点的修复动作。
 *
 *  只对能确定动作的归因给按钮 —— 给不出就**不给**：一个点了没用的按钮比没有按钮更糟。
 *  结论全部来自预置目录与当前地址，不硬编码厂商知识（百炼那条也是照它文档写明的路径）。
 */
export function fixHintsFor(
  reason: ProbeReason | undefined,
  draft: { baseUrl: string; driver: ProviderRow['driver'] },
  presets: ProviderPreset[],
  plans: PresetPlan[],
): FixHint[] {
  const baseUrl = draft.baseUrl.trim()
  if (reason === 'base_url') {
    const hints: FixHint[] = []
    const host = baseUrlHost(baseUrl)
    // 同域名、同协议的收录端点（最多两条）——最可能就是用户想填的那个
    for (const preset of presets) {
      if (baseUrlHost(preset.baseUrl) !== host) continue
      if (preset.driver !== draft.driver) continue
      if (normalizeBaseUrl(preset.baseUrl) === normalizeBaseUrl(baseUrl)) continue
      hints.push({
        kind: 'use-preset',
        presetId: preset.id,
        label: `改用「${presetLabelFor(preset, plans)}」`,
      })
      if (hints.length >= 2) break
    }

    const lower = baseUrl.toLowerCase()
    const path = baseUrlPath(baseUrl)
    const origin = baseUrlOrigin(baseUrl)
    if (origin && host.endsWith('maas.aliyuncs.com') && !lower.includes('/compatible-mode')) {
      // 百炼的原生前缀是 `/api/v1`，OpenAI 兼容端点是 `/compatible-mode/v1`
      // —— 是**替换**而不是追加，所以按路径判断该换成什么。
      const target = /\/api\/v\d+$/.test(path) ? `${origin}/compatible-mode/v1` : `${baseUrl.replace(/\/+$/, '')}/compatible-mode/v1`
      hints.push({ kind: 'use-url', baseUrl: target, label: '改为兼容模式地址（/compatible-mode/v1）' })
    } else if (origin && !/\/v\d+\/?$/.test(lower) && !lower.includes('/compatible-mode')) {
      hints.push({ kind: 'use-url', baseUrl: `${baseUrl.replace(/\/+$/, '')}/v1`, label: '在地址末尾补 /v1' })
    }
    return hints.slice(0, 3)
  }
  if (reason === 'model_name') {
    return [{ kind: 'open-catalog', label: '从模型清单里选一个' }]
  }
  if (reason === 'api_key') {
    return [{ kind: 'focus-api-key', label: '重新填写 API Key' }]
  }
  // unreachable / blocked / timeout / invalid_url / client_build / unknown：
  // 能给的只有「检查地址与网络」这类说明，给不出可执行的一步 —— 不造按钮。
  return []
}
