/**
 * 域图归属展示（管理端 /agents 页）
 *
 * 归属事实源在后端域图注册表，经 `GET /agents` 的 `domain` / `domain_label` /
 * `subflow` 三个字段下发（注册表派生，见 backend/orchestration/domain_registry.py）。
 * 本模块只把数据拼成一句话，**不复写归属关系**。
 *
 * 为什么删掉了原先手写的 `ROUTE_MODE_DOMAIN_META`：它写的是「Travel Domain」，
 * 而注册表里 travel 图的 label 是「旅游规划图执行」——那个英文业务名后端根本
 * 不存在，是展示层自己发明的，且已经和父图标题漂移。手写副本会过期，派生不会。
 */

/** 归属展示所需的最小字段集（与 api/registry.ts 的 AgentNode 兼容）。 */
export interface DomainAttributionSource {
  /** 所属顶级域的 route_mode；顶级域图自身为 null */
  domain?: string | null
  /** 顶级域展示标签（父图 label）；缺失时回退 domain */
  domain_label?: string | null
  /** 子流标识（commerce / booking）；顶级域图自身为 null */
  subflow?: string | null
}

/**
 * 子流归属后缀：`父域标签 · 子流 子流`。
 *
 * 非子流图（后端下发 null）或字段缺失（旧版本响应）一律返回 null——
 * 宁可不显示，也不臆造一条归属。
 */
export function subflowAttribution(node: DomainAttributionSource): string | null {
  if (!node.domain || !node.subflow) return null
  return `${node.domain_label ?? node.domain} · ${node.subflow} 子流`
}
