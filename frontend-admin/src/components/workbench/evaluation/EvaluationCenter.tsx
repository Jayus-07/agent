'use client'

import { useSearchParams } from 'next/navigation'
import { useMemo } from 'react'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DatasetGovernancePanel from './DatasetGovernancePanel'
import EvaluationV2RunsPanel from './EvaluationV2RunsPanel'
import EvaluationRunComparisonPanel from './EvaluationRunComparisonPanel'

type EvaluationTab = 'datasets' | 'runs' | 'compare'
const TABS: readonly { id: EvaluationTab; label: string }[] = [
  { id: 'datasets', label: '数据集' },
  { id: 'runs', label: '运行评测' },
  { id: 'compare', label: '结果对比' },
]
const TAB_IDS = TABS.map(item => item.id) as readonly EvaluationTab[]
// 旧入口（8 tab 时代与 V1 时代）统一收敛到三个核心功能区
const LEGACY_TABS: Record<string, EvaluationTab> = {
  overview: 'datasets',
  generate: 'datasets',
  review: 'datasets',
  suites: 'runs',
  results: 'runs',
  bindings: 'runs',
  gates: 'compare',
  feedback: 'datasets',
}

export default function EvaluationCenter() {
  const searchParams = useSearchParams()
  const { tab: fallbackTab, selectTab } = useWorkbenchTab(TAB_IDS, 'datasets')
  const requested = searchParams.get('tab') || ''
  const activeTab = LEGACY_TABS[requested] || (TAB_IDS.includes(requested as EvaluationTab) ? requested as EvaluationTab : fallbackTab)
  const label = TABS.find(item => item.id === activeTab)?.label || '数据集'

  // Prompt 治理页深链：/evaluations/center?tab=runs&prompt=<key>&pv=<version>
  const promptPrefill = useMemo(() => {
    const key = (searchParams.get('prompt') || '').trim()
    const version = (searchParams.get('pv') || '').trim()
    if (!key) return null
    return { key, version: version || '' }
  }, [searchParams])

  return <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-bg-root">
    <header className="shrink-0 border-b border-border-subtle bg-surface-base px-4 py-5 sm:px-6">
      <div className="mx-auto w-full max-w-[1500px]">
        <p className="text-xs font-medium text-text-muted">评测平台 / {label}</p>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-text-primary">评测中心</h1>
        <p className="mt-1 text-xs leading-5 text-text-secondary">数据集 → 运行评测 → 结果对比：版本不可变、配置固定、结果可归因。</p>
      </div>
    </header>
    <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 flex-col">
      <nav className="shrink-0 border-b border-border-subtle bg-surface-base px-3" role="tablist" aria-label="评测中心功能区">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map(item => {
            const selected = item.id === activeTab
            return <button key={item.id} type="button" role="tab" aria-selected={selected} aria-controls={'evaluation-panel-' + item.id} tabIndex={selected ? 0 : -1} onClick={() => selectTab(item.id)} className={'-mb-px border-b-2 px-4 py-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ' + (selected ? 'border-accent font-medium text-accent' : 'border-transparent text-text-secondary hover:text-text-primary')}>
              <span>{item.label}</span>
            </button>
          })}
        </div>
      </nav>
      <section id={'evaluation-panel-' + activeTab} role="tabpanel" aria-label={label} className="min-h-0 flex-1 overflow-y-auto">
        {activeTab === 'datasets' && <DatasetGovernancePanel />}
        {activeTab === 'runs' && <EvaluationV2RunsPanel promptPrefill={promptPrefill} />}
        {activeTab === 'compare' && <EvaluationRunComparisonPanel />}
      </section>
    </div>
  </div>
}
