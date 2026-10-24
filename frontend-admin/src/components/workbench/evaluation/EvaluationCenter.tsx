'use client'

import { Activity, BadgeCheck, BarChart3, Database, FileCheck2, FlaskConical, Layers3, Sparkles } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useSearchParams } from 'next/navigation'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DatasetGovernancePanel from './DatasetGovernancePanel'
import DatasetSynthesisPanel from './DatasetSynthesisPanel'
import DatasetReviewsPanel from './DatasetReviewsPanel'
import FeedbackCandidatesPanel from './FeedbackCandidatesPanel'
import EvaluationResultsPanel from './EvaluationResultsPanel'
import EvaluationBindingsPanel from './EvaluationBindingsPanel'
import EvaluationOperationsOverview from './EvaluationOperationsOverview'
import EvaluationSuitesPanel from './EvaluationSuitesPanel'
import EvaluationRunComparisonPanel from './EvaluationRunComparisonPanel'
import EvaluationReleaseQualityPanel from './EvaluationReleaseQualityPanel'
import EvaluationV2ReviewPanel from './EvaluationV2ReviewPanel'
import EvaluationV2RunsPanel from './EvaluationV2RunsPanel'

type EvaluationTab = 'overview' | 'datasets' | 'generate' | 'review' | 'suites' | 'runs' | 'compare' | 'gates'
const TABS: readonly { id: EvaluationTab; label: string; icon: LucideIcon }[] = [
  { id: 'overview', label: '评测概览', icon: Activity },
  { id: 'datasets', label: '数据集中心', icon: Database },
  { id: 'generate', label: '智能生成', icon: Sparkles },
  { id: 'review', label: '标注审核', icon: FileCheck2 },
  { id: 'suites', label: '评测方案', icon: Layers3 },
  { id: 'runs', label: '实验运行', icon: FlaskConical },
  { id: 'compare', label: '实验对比', icon: BarChart3 },
  { id: 'gates', label: '发布质量', icon: BadgeCheck },
]
const TAB_IDS = TABS.map(item => item.id) as readonly EvaluationTab[]
const LEGACY_TABS: Record<string, EvaluationTab> = { results: 'runs', bindings: 'runs', reviews: 'review', feedback: 'review' }

export default function EvaluationCenter() {
  const searchParams = useSearchParams()
  const { tab: fallbackTab, selectTab } = useWorkbenchTab(TAB_IDS, 'overview')
  const requested = searchParams.get('tab') || ''
  const activeTab = LEGACY_TABS[requested] || (TAB_IDS.includes(requested as EvaluationTab) ? requested as EvaluationTab : fallbackTab)
  const label = TABS.find(item => item.id === activeTab)?.label || '评测概览'

  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg-root">
    <header className="shrink-0 border-b border-border-subtle bg-surface-base px-4 py-5 sm:px-6">
      <div className="mx-auto w-full max-w-[1500px]">
        <p className="text-xs font-medium text-text-muted">评测平台 / {label}</p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-text-primary">评测中心</h1>
        <p className="mt-1 text-xs leading-5 text-text-secondary">以可追溯的数据版本、审核、固定配置和真实运行结果完成质量闭环。</p>
      </div>
    </header>
    <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 flex-col lg:flex-row">
      <nav className="shrink-0 overflow-x-auto border-b border-border-subtle bg-surface-base px-3 lg:w-56 lg:overflow-y-auto lg:border-b-0 lg:border-r lg:px-2 lg:py-4" role="tablist" aria-label="评测中心功能区">
        <div className="flex min-w-max gap-1 lg:min-w-0 lg:flex-col">
          {TABS.map(item => {
            const Icon = item.icon
            const selected = item.id === activeTab
            return <button key={item.id} type="button" role="tab" aria-selected={selected} aria-controls={'evaluation-panel-' + item.id} tabIndex={selected ? 0 : -1} onClick={() => selectTab(item.id)} className={'flex min-h-11 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ' + (selected ? 'bg-accent-soft font-medium text-accent' : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary')}>
              <Icon size={16} aria-hidden="true" /><span>{item.label}</span>
            </button>
          })}
        </div>
      </nav>
      <section id={'evaluation-panel-' + activeTab} role="tabpanel" aria-label={label} className="min-h-0 flex-1 overflow-y-auto">
        {activeTab === 'overview' && <EvaluationOperationsOverview onNavigate={selectTab} />}
        {activeTab === 'datasets' && <DatasetGovernancePanel onGoReview={() => selectTab('review')} />}
        {activeTab === 'generate' && <div className="mx-auto max-w-7xl space-y-4 px-6 py-6"><div><h2 className="text-base font-semibold text-text-primary">从知识文档生成候选题</h2><p className="mt-1 text-sm leading-6 text-text-secondary">生成结果只进入 V2 候选审核队列；模型输出不会自动成为 Golden。</p></div><DatasetSynthesisPanel expanded onGoReview={() => selectTab('review')} /></div>}
        {activeTab === 'review' && <div className="mx-auto max-w-7xl space-y-6 px-6 py-6"><EvaluationV2ReviewPanel /><section><h2 className="mb-3 text-base font-semibold text-text-primary">旧版资产审核</h2><DatasetReviewsPanel /></section><section className="border-t border-border-subtle pt-5"><h2 className="mb-3 text-base font-semibold text-text-primary">线上反馈候选</h2><FeedbackCandidatesPanel /></section></div>}
        {activeTab === 'suites' && <EvaluationSuitesPanel onNavigate={() => selectTab('runs')} />}
        {activeTab === 'runs' && <div className="space-y-2"><EvaluationV2RunsPanel /><details className="mx-6 my-5 rounded-lg border border-border-subtle bg-surface-base" open={requested === 'bindings'}><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text-primary">旧版题集关联知识库运行（兼容入口）</summary><div className="border-t border-border-subtle"><EvaluationBindingsPanel /></div></details><details className="mx-6 my-5 rounded-lg border border-border-subtle bg-surface-base"><summary className="cursor-pointer px-4 py-3 text-sm font-medium text-text-primary">查看历史旧版运行报告</summary><div className="border-t border-border-subtle"><EvaluationResultsPanel /></div></details></div>}
        {activeTab === 'compare' && <EvaluationRunComparisonPanel />}
        {activeTab === 'gates' && <EvaluationReleaseQualityPanel />}
      </section>
    </div>
  </div>
}
