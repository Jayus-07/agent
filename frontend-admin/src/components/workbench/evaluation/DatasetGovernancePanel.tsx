'use client'

import { useState } from 'react'
import DatasetSynthesisPanel from './DatasetSynthesisPanel'
import EvaluationV2DatasetPanel from './EvaluationV2DatasetPanel'
import EvaluationV2ReviewPanel from './EvaluationV2ReviewPanel'
import { EvalPageHeader } from './evalUi'

export default function DatasetGovernancePanel() {
  const [showReview, setShowReview] = useState(false)
  const [showGenerator, setShowGenerator] = useState(false)
  return <div>
    <EvalPageHeader
      title="数据集"
      subtitle="导入、生成与编辑只写草稿；发布后版本不可变，被运行锁定，修改产生新版本。"
    />
    <EvaluationV2DatasetPanel onGoReview={() => setShowReview(true)} />
    <div className="mt-6 space-y-3 border-t border-border-subtle pt-4">
      <button
        type="button"
        aria-expanded={showReview}
        onClick={() => setShowReview(value => !value)}
        className="block text-sm font-medium text-accent hover:underline"
      >
        {showReview ? '收起候选审核与修订' : '展开候选审核与修订（补齐真值与证据）'}
      </button>
      {showReview && <EvaluationV2ReviewPanel />}
      <button
        type="button"
        aria-expanded={showGenerator}
        onClick={() => setShowGenerator(value => !value)}
        className="block text-sm font-medium text-accent hover:underline"
      >
        {showGenerator ? '收起从知识库生成' : '从知识库生成评测题（基于快照，自动带证据）'}
      </button>
      {showGenerator && <DatasetSynthesisPanel expanded />}
    </div>
  </div>
}
