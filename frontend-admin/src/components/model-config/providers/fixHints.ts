/** 探测失败后的「去修」动作（纯函数产出描述，组件负责绑定 onClick；B4 拆分迁出）。
 *
 *  一个可执行的修复动作。**刻意不带回调** —— 保持纯函数可单测。
 *
 *  地址类的动作直接带上**算好的目标地址**（`use-url`），而不是「补一段 /v1」这种指令：
 *  补与替换的差别很大（百炼要从 `/api/v1` **换成** `/compatible-mode/v1`，不是往后接），
 *  让纯函数算出最终值，组件只负责写入，也便于断言。
 *
 *  2026-09-22 拍板删除预置端点目录：原「use-preset（改用官方收录端点）」动作
 *  随之退役，只保留不依赖目录的通用修复提示。
 */
import type { ProbeReason, ProviderRow } from '@/types/modelConfig'
import { baseUrlHost, baseUrlPath } from './urlUtils'

export type FixHint =
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
 */
export function fixHintsFor(
  reason: ProbeReason | undefined,
  draft: { baseUrl: string; driver: ProviderRow['driver'] },
): FixHint[] {
  const baseUrl = draft.baseUrl.trim()
  if (reason === 'base_url') {
    const hints: FixHint[] = []
    const host = baseUrlHost(baseUrl)
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
