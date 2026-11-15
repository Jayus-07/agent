'use client'

import { isLowConfidence, RAG_REJECTION_ACTIONS, toRejectionActionKey } from '@/lib/ragAnswer'

/** 知识库回答的简短后续建议，供普通聊天与企业助手共用。 */
export default function RagAnswerHint({
  answerStatus,
  confidence,
  hasSources = false,
}: {
  answerStatus?: string
  confidence?: number
  hasSources?: boolean
}) {
  const action = answerStatus ? RAG_REJECTION_ACTIONS[toRejectionActionKey(answerStatus)] : undefined
  const low = !action && isLowConfidence(confidence)
  if (!action && !low) return null
  return (
    <div role="note" aria-label={action ? '下一步建议' : '回答核实提示'}
      className="mt-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2
        text-xs leading-relaxed text-amber-800">
      {action ? (
        <><span className="font-medium">下一步：</span>{action}</>
      ) : hasSources ? (
        `回答置信度 ${Math.round((confidence ?? 0) * 100)}%，建议点击下方来源核实原文`
      ) : (
        '当前回答依据有限，暂时没有可查看的引用来源。请补充资料名称、业务范围或时间范围后重试。'
      )}
    </div>
  )
}
