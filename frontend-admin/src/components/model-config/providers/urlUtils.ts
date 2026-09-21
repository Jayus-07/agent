/** 通用 Base URL 纯函数（从原 presets.ts 瘦身而来 —— 2026-09-22 拍板
 *  删除预置端点目录后，这里只剩与「预置」无关的 URL 解析/校验工具）。
 *
 *  **不依赖 React**，可独立单测。
 */

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
