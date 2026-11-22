'use client'

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useMemo } from 'react'
import { useWorkbenchTab } from '@/components/workbench/useWorkbenchTab'
import DatasetsTab from './proto/DatasetsTab'
import FailedTracesTab from './proto/FailedTracesTab'
import RunsTab from './proto/RunsTab'
import DomainOverviewBar from './DomainOverviewBar'
import { ALL_DOMAIN, EVAL_DOMAINS, isDomainFilter, type DomainFilter } from './domains'

type EvaluationTab = 'datasets' | 'traces' | 'runs'
const TABS: readonly { id: EvaluationTab; label: string }[] = [
  { id: 'datasets', label: '数据集' },
  { id: 'traces', label: '失败 Trace' },
  { id: 'runs', label: '运行评测' },
]
const TAB_IDS = TABS.map(item => item.id) as readonly EvaluationTab[]
// 旧入口（8 tab 时代与 V1 时代）统一收敛到三个核心功能区
// （结果对比 tab 已随批次 1 隐藏：其数据源只接 V2 运行且生产恒为空，
// 同 prompt 历史对比在批次 4 以新形态回归——见改造方案 §6）
const LEGACY_TABS: Record<string, EvaluationTab> = {
  overview: 'datasets',
  generate: 'datasets',
  review: 'datasets',
  trace: 'traces',
  suites: 'runs',
  results: 'runs',
  bindings: 'runs',
  gates: 'runs',
  feedback: 'datasets',
  compare: 'runs',
}

export default function EvaluationCenter() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const { tab: fallbackTab, selectTab } = useWorkbenchTab(TAB_IDS, 'datasets')
  const requested = searchParams.get('tab') || ''
  const activeTab = LEGACY_TABS[requested] || (TAB_IDS.includes(requested as EvaluationTab) ? requested as EvaluationTab : fallbackTab)
  const label = TABS.find(item => item.id === activeTab)?.label || '数据集'

  // 域导航（批次 1）：?domain= 持久在 URL，切 tab / 刷新 / 分享均保持
  const requestedDomain = searchParams.get('domain') || ''
  const domain: DomainFilter = isDomainFilter(requestedDomain) ? requestedDomain : ALL_DOMAIN
  const selectDomain = (next: DomainFilter) => {
    const nextParams = new URLSearchParams(searchParams.toString())
    if (next === ALL_DOMAIN) nextParams.delete('domain')
    else nextParams.set('domain', next)
    const query = nextParams.toString()
    router.push(query ? pathname + '?' + query : pathname)
  }

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
        <p className="mt-1 text-xs leading-5 text-text-secondary">按域发起评测、查看历史测评记录与逐题结果；失败 Trace 回流到数据集候选。</p>
      </div>
    </header>
    <div className="mx-auto flex min-h-0 w-full max-w-[1500px] flex-1 flex-col">
      <nav className="shrink-0 border-b border-border-subtle bg-surface-base px-3 pt-2" aria-label="评测域导航">
        <div className="flex flex-wrap gap-1.5">
          <button
            key={ALL_DOMAIN}
            type="button"
            aria-pressed={domain === ALL_DOMAIN}
            onClick={() => selectDomain(ALL_DOMAIN)}
            className={'rounded-full px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ' + (domain === ALL_DOMAIN ? 'bg-accent font-medium text-white' : 'bg-bg-elevated text-text-secondary hover:text-text-primary')}
          >
            全部
          </button>
          {EVAL_DOMAINS.map(item => {
            const selected = domain === item.key
            return <button
              key={item.key}
              type="button"
              aria-pressed={selected}
              onClick={() => selectDomain(selected ? ALL_DOMAIN : item.key)}
              className={'rounded-full px-3 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent motion-reduce:transition-none ' + (selected ? 'bg-accent font-medium text-white' : 'bg-bg-elevated text-text-secondary hover:text-text-primary')}
            >
              {item.label}
            </button>
          })}
        </div>
        <DomainOverviewBar domain={domain} onSelectDomain={selectDomain} />
      </nav>
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
        {activeTab === 'datasets' && <div className="mx-auto max-w-[1200px] px-5 py-5"><DatasetsTab onGoRuns={() => selectTab('runs')} onGoTraces={() => selectTab('traces')} /></div>}
        {activeTab === 'traces' && <div className="mx-auto max-w-[1200px] px-5 py-5"><FailedTracesTab /></div>}
        {activeTab === 'runs' && <div className="mx-auto max-w-[1200px] px-5 py-5"><RunsTab promptPrefill={promptPrefill} domain={domain} /></div>}
      </section>
    </div>
  </div>
}
