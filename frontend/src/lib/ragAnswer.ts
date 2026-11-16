/**
 * RAG 答案语义（done.answer_status / confidence）的前端消费口径。
 *
 * 稳定码由后端 evidence_gate.models.ANSWER_STATUS_BY_REASON 下发
 * （蛇形小写：rag_no_evidence / rag_retrieval_degraded / rag_permission_denied /
 * rag_hallucination），
 * 拒答后的行动建议由普通聊天和企业助手共用，避免同一种结果出现不同说法。
 */

/** 拒答状态 → 用户下一步可以做什么。 */
export const RAG_REJECTION_ACTIONS: Record<string, string> = {
  RAG_NO_EVIDENCE: "补充资料名称、业务范围或时间范围；也可以联系管理员确认内容是否已发布到知识库",
  RAG_RETRIEVAL_DEGRADED: "请稍后重试；如果多次发生，请联系管理员",
  RAG_PERMISSION_DENIED: "联系管理员申请查看权限",
  RAG_VERSION_CONFLICT: "说明要查询的资料版本或生效日期；不确定时联系资料管理员",
  RAG_HALLUCINATION: "缩小问题范围，或联系资料管理员核对原文",
}

/** 低置信阈值：与客服知识门禁 CAUTIOUS 档下界（0.60）同口径 */
export const RAG_LOW_CONFIDENCE_THRESHOLD = 0.6

/** 后端蛇形码 → ErrorCard 行动指引表的键（大写） */
export function toRejectionActionKey(answerStatus: string): string {
  return answerStatus.toUpperCase()
}

/** 置信度缺失/拒答态不提示，仅 0 ≤ c < 阈值的正常回答提示「建议核实」 */
export function isLowConfidence(confidence?: number): boolean {
  return (
    typeof confidence === 'number' &&
    confidence >= 0 &&
    confidence < RAG_LOW_CONFIDENCE_THRESHOLD
  )
}

/**
 * 页码列表 → 来源卡文案：单页 / 连续区间合并 / 离散列举（超过 3 页截断加「等」）。
 * 与后端 citation._pages_label 同口径（文本协议与结构化出口共用一套展示语义）。
 */
export function formatSourcePages(pages?: number[]): string {
  if (!pages || pages.length === 0) return ''
  const sorted = [...pages].sort((a, b) => a - b)
  if (sorted.length === 1) return `第 ${sorted[0]} 页`
  const consecutive = sorted.every((p, i) => i === 0 || p === sorted[i - 1] + 1)
  if (consecutive) return `第 ${sorted[0]}-${sorted[sorted.length - 1]} 页`
  const shown = sorted.slice(0, 3)
  const suffix = sorted.length > 3 ? ' 等' : ''
  return `第 ${shown.join('、')} 页${suffix}`
}
