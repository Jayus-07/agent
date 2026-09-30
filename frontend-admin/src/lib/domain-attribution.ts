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

/**
 * 域图节点排序：每个父域**紧跟**它的子流，让归属关系在版面上也一眼可见。
 *
 * 排序依据是后端下发的 `domain` 字段（不是前端写死的顺序），所以新增子流域图
 * 自动归位。父域之间、同一父域的子流之间都保持后端给的相对顺序。
 *
 * **长度恒等**：任何节点都不会被丢掉——父域不在本列表里的子流（异常数据）
 * 兜底追加到末尾，宁可排得难看，也不能让节点从页面上消失。
 */
export function orderDomainGraphs<
  T extends { route_mode?: string | null; domain?: string | null },
>(nodes: readonly T[]): T[] {
  const parents = nodes.filter((n) => !n.domain)
  const children = nodes.filter((n) => n.domain)
  if (children.length === 0) return [...nodes]

  const ordered: T[] = []
  for (const parent of parents) {
    ordered.push(parent)
    ordered.push(...children.filter((c) => c.domain === parent.route_mode))
  }
  ordered.push(...children.filter((c) => !parents.some((p) => p.route_mode === c.domain)))
  return ordered
}
