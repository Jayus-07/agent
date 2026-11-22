/**
 * 评测域定义与归堆规则（唯一事实源）。
 *
 * 评测中心按域组织发起与历史记录；域映射不做数据库迁移，前端按
 * prompt_key 前缀 / 记录 module 字段归堆（评测中心改造批次 1）。
 * evaluation.* 等评测器 Prompt 是工具类 key，与其余未登记业务 key
 * 一起归编排智能体（agent）域兜底。
 */

export type EvalDomain = 'rag' | 'sql' | 'cs' | 'travel' | 'agent'
export type DomainFilter = EvalDomain | 'all'
export const ALL_DOMAIN: DomainFilter = 'all'

export const EVAL_DOMAINS: ReadonlyArray<{ key: EvalDomain; label: string }> = [
  { key: 'rag', label: 'RAG' },
  { key: 'sql', label: 'SQL' },
  { key: 'cs', label: '客服' },
  { key: 'travel', label: '旅游' },
  { key: 'agent', label: '编排智能体' },
]

const DOMAIN_LABELS = Object.fromEntries(
  EVAL_DOMAINS.map(item => [item.key, item.label]),
) as Record<EvalDomain, string>

export function domainLabel(domain: EvalDomain): string {
  return DOMAIN_LABELS[domain]
}

const PREFIX_RULES: ReadonlyArray<{ prefix: string; domain: EvalDomain }> = [
  { prefix: 'rag.', domain: 'rag' },
  { prefix: 'sql.', domain: 'sql' },
  { prefix: 'customer_service.', domain: 'cs' },
  { prefix: 'travel.', domain: 'travel' },
]

/** 被测 prompt_key → 域；评测器与其余业务 key 归编排智能体域兜底。 */
export function promptKeyToDomain(key: string | null | undefined): EvalDomain {
  const value = (key || '').trim()
  if (!value) return 'agent'
  const hit = PREFIX_RULES.find(rule => value.startsWith(rule.prefix))
  return hit ? hit.domain : 'agent'
}

const MODULE_RULES: ReadonlyArray<{ module: string; domain: EvalDomain }> = [
  { module: 'rag', domain: 'rag' },
  { module: 'sql', domain: 'sql' },
  { module: 'cs', domain: 'cs' },
  { module: 'customer_service', domain: 'cs' },
  { module: 'travel', domain: 'travel' },
]

/** 记录 module（CLI 台账 / V2 方案）→ 域；未知值归编排智能体域兜底。 */
export function moduleToDomain(module: string | null | undefined): EvalDomain {
  const value = (module || '').trim()
  if (!value) return 'agent'
  const hit = MODULE_RULES.find(rule => value === rule.module)
  return hit ? hit.domain : 'agent'
}

/** URL ?domain= 参数合法性校验。 */
export function isDomainFilter(value: string | null | undefined): value is DomainFilter {
  if (value === ALL_DOMAIN) return true
  return EVAL_DOMAINS.some(item => item.key === value)
}
