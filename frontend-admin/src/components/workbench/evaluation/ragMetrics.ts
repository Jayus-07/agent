/**
 * RAG 核心五维黄金量化指标与联合 Ragas 可选评测指标规范
 */

export interface MetricDefinition {
  key: string
  label: string
  enLabel: string
  description: string
  category: 'retrieval' | 'generation' | 'safety' | 'efficiency'
  isRagasOptional?: boolean
  ragasLevel?: 'basic' | 'standard' | 'full'
  defaultThreshold: number // 推荐门禁达标线（0.0 ~ 1.0）
}

/** 核心五维黄金指标（默认必评体系） */
export const RAG_CORE_METRICS: readonly MetricDefinition[] = [
  {
    key: 'context_recall',
    label: '上下文召回率',
    enLabel: 'Context Recall',
    description: '衡量知识库切片是否完全覆盖回答问题所必需的事实依据。',
    category: 'retrieval',
    defaultThreshold: 0.85,
  },
  {
    key: 'context_precision',
    label: '上下文精确率',
    enLabel: 'Context Precision',
    description: '衡量检索返回的 Top-K 切片中相关内容的排名与信噪比。',
    category: 'retrieval',
    defaultThreshold: 0.80,
  },
  {
    key: 'faithfulness',
    label: '忠实度 / 无幻觉率',
    enLabel: 'Faithfulness',
    description: '衡量模型回答是否完全立足于检索上下文，杜绝无依据事实幻觉。',
    category: 'generation',
    defaultThreshold: 0.90,
  },
  {
    key: 'answer_relevance',
    label: '回答相关度',
    enLabel: 'Answer Relevance',
    description: '衡量模型回答是否直击提问核心意图，无冗余跑题。',
    category: 'generation',
    defaultThreshold: 0.85,
  },
  {
    key: 'answer_correctness',
    label: '综合真实正确性',
    enLabel: 'Answer Correctness',
    description: '加权融合语义相似度与事实事实匹配度的端到端回答正确度。',
    category: 'generation',
    defaultThreshold: 0.80,
  },
] as const

/** 联合 Ragas 进阶可选评测套件 */
export const RAGAS_OPTIONAL_METRICS: readonly MetricDefinition[] = [
  {
    key: 'context_entity_recall',
    label: '上下文实体召回率',
    enLabel: 'Context Entity Recall',
    description: '衡量人名、合同号、编号、时间等关键实体专有名词的召回覆盖率。',
    category: 'retrieval',
    isRagasOptional: true,
    ragasLevel: 'full',
    defaultThreshold: 0.75,
  },
  {
    key: 'semantic_similarity',
    label: '语义相似度',
    enLabel: 'Semantic Similarity',
    description: '回答与黄金参考答案在向量空间上的余弦相似度。',
    category: 'generation',
    isRagasOptional: true,
    ragasLevel: 'standard',
    defaultThreshold: 0.82,
  },
  {
    key: 'aspect_critic_harmfulness',
    label: '安全与无害合规',
    enLabel: 'Harmfulness & Safety',
    description: '评估回答是否包含越权、违规、泄密或有害建议。',
    category: 'safety',
    isRagasOptional: true,
    ragasLevel: 'full',
    defaultThreshold: 0.99,
  },
  {
    key: 'aspect_critic_conciseness',
    label: '表达精炼度',
    enLabel: 'Conciseness',
    description: '评估回答是否言简意赅，避免车轱辘话与低价值填充。',
    category: 'generation',
    isRagasOptional: true,
    ragasLevel: 'full',
    defaultThreshold: 0.80,
  },
  {
    key: 'context_utilization',
    label: '切片有效利用率',
    enLabel: 'Context Utilization',
    description: 'LLM 实际引用的切片信息量与召回切片总量的有效比例。',
    category: 'efficiency',
    isRagasOptional: true,
    ragasLevel: 'standard',
    defaultThreshold: 0.70,
  },
] as const

export const ALL_EVAL_METRICS = [...RAG_CORE_METRICS, ...RAGAS_OPTIONAL_METRICS]

/**
 * 将 metric key（无论是否有 ragas_ 前缀）映射到统一规范指标定义
 */
export function resolveMetricDef(rawKey: string): MetricDefinition | undefined {
  const normalized = rawKey.replace(/^ragas_/, '').toLowerCase()
  return ALL_EVAL_METRICS.find(m => {
    const k = m.key.toLowerCase()
    return k === normalized || k === rawKey.toLowerCase()
  })
}

export type ScoreLevel = 'excellent' | 'good' | 'warning' | 'poor' | 'na'

/**
 * 计算分数对应的评级与颜色语义
 */
export function getScoreLevel(score: number | null | undefined, threshold = 0.8): ScoreLevel {
  if (score === null || score === undefined || Number.isNaN(score)) return 'na'
  if (score >= 0.9) return 'excellent'
  if (score >= threshold) return 'good'
  if (score >= 0.6) return 'warning'
  return 'poor'
}

export function formatScorePercent(score: number | null | undefined, digits = 1): string {
  if (score === null || score === undefined || Number.isNaN(score)) return '—'
  return `${(score * 100).toFixed(digits)}%`
}

export function getScoreTone(level: ScoreLevel): 'good' | 'bad' | 'warn' | 'muted' {
  switch (level) {
    case 'excellent':
    case 'good':
      return 'good'
    case 'warning':
      return 'warn'
    case 'poor':
      return 'bad'
    default:
      return 'muted'
  }
}
