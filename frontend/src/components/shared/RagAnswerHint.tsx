'use client'

import { isLowConfidence, RAG_REJECTION_ACTIONS, toRejectionActionKey } from '@/lib/ragAnswer'

/** 知识库回答的简短后续建议，供普通聊天与企业助手共用。 */
export default function RagAnswerHint({
  answerStatus,
  confidence,
}: {
  answerStatus?: string
  confidence?: number
}) {
  const action = answerStatus ? RAG_REJECTION_ACTIONS[toRejectionActionKey(answerStatus)] : undefined
  const low = !answerStatus && isLowConfidence(confidence)
  if (!action && !low) return null
  return (
    <div role="note" aria-label={action ? '下一步建议' : '回答核实提示'}
      className="mt-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2
        text-xs leading-relaxed text-amber-800">
      {action ? (
        <><span className="font-medium">下一步：</span>{action}</>
      ) : (
        `回答置信度 ${Math.round((confidence ?? 0) * 100)}%，建议点击下方来源核实原文`
      )}
    </div>
  )
}
