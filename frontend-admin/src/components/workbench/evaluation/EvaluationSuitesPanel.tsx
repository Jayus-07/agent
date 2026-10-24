'use client'

import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, RefreshCw } from 'lucide-react'
import type { EvaluationSuite, EvaluationV2DatasetVersion, EvaluationV2EvaluatorVersion } from '@/api/evaluation'
import { evaluationService } from '@/api/evaluation'
import EmptyState from '@/components/shared/EmptyState'

interface Props {
  onNavigate: () => void
}

export default function EvaluationSuitesPanel({ onNavigate }: Props) {
  const [items, setItems] = useState<EvaluationSuite[]>([])
  const [datasetVersions, setDatasetVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [evaluators, setEvaluators] = useState<EvaluationV2EvaluatorVersion[]>([])
  const [moduleFilter, setModuleFilter] = useState('all')
  const [suiteKey, setSuiteKey] = useState('rag_answer_quality')
  const [suiteName, setSuiteName] = useState('RAG 回答质量基线')
  const [suiteModule, setSuiteModule] = useState('rag')
  const [suiteVersion, setSuiteVersion] = useState('1.0.0')
  const [minimumSamples, setMinimumSamples] = useState(1)
  const [minimumPassRate, setMinimumPassRate] = useState(0.95)
  const [hardFailureLimit, setHardFailureLimit] = useState('')
  const [datasetVersionId, setDatasetVersionId] = useState('')
  const [evaluatorId, setEvaluatorId] = useState('')
  const [evidenceEvaluatorId, setEvidenceEvaluatorId] = useState('')
  const [humanEvaluatorId, setHumanEvaluatorId] = useState('')
  const [evaluatorKey, setEvaluatorKey] = useState('answer_quality')
  const [evaluatorVersion, setEvaluatorVersion] = useState('1.0.0')
  const [evaluatorKind, setEvaluatorKind] = useState<EvaluationV2EvaluatorVersion['evaluator_kind']>('deterministic')
  const [humanRubricText, setHumanRubricText] = useState('{"criteria":[{"key":"accuracy","label":"事实准确","weight":0.5},{"key":"evidence","label":"证据充分","weight":0.5}]}')
  const [draft, setDraft] = useState<{ suite_version_id: string; snapshot_hash: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [suites, datasets, evaluatorItems] = await Promise.all([
        evaluationService.listSuites(),
        evaluationService.listV2Datasets(),
        evaluationService.listV2Evaluators(),
      ])
      const versionLists = await Promise.all(datasets.map(dataset => evaluationService.listV2DatasetVersions(dataset.dataset_id)))
      const datasetDomains = new Map(datasets.map(dataset => [dataset.dataset_id, dataset.domain]))
      const supportedModules = new Set(['rag', 'sql', 'cs', 'e2e', 'travel', 'travel-provider', 'travel-commerce'])
      const published = versionLists.flat()
        .filter(version => version.status === 'published' && supportedModules.has(datasetDomains.get(version.dataset_id) || ''))
        .map(version => ({ ...version, domain: datasetDomains.get(version.dataset_id) }))
      const publishedEvaluators = evaluatorItems.filter(item => item.status === 'published')
      const machineEvaluators = publishedEvaluators.filter(item => item.evaluator_kind === 'deterministic')
      const evidenceEvaluators = publishedEvaluators.filter(item => item.evaluator_kind === 'evidence')
      const humanEvaluators = publishedEvaluators.filter(item => item.evaluator_kind === 'human')
      setItems(suites)
      setDatasetVersions(published)
      setEvaluators(publishedEvaluators)
      const availableModules = Array.from(new Set(published.map(item => item.domain).filter((item): item is string => Boolean(item))))
      const preferredModule = availableModules.includes('rag') ? 'rag' : availableModules[0] || ''
      const nextModule = availableModules.includes(suiteModule) ? suiteModule : preferredModule
      setSuiteModule(nextModule)
      setDatasetVersionId(current => published.some(item => item.dataset_version_id === current && item.domain === nextModule)
        ? current
        : published.find(item => item.domain === nextModule)?.dataset_version_id || '')
      setEvaluatorId(current => machineEvaluators.some(item => item.evaluator_version_id === current) ? current : machineEvaluators[0]?.evaluator_version_id || '')
      setEvidenceEvaluatorId(current => evidenceEvaluators.some(item => item.evaluator_version_id === current) ? current : '')
      setHumanEvaluatorId(current => humanEvaluators.some(item => item.evaluator_version_id === current) ? current : '')
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : '读取评测方案失败') }
    finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  const modules = useMemo(() => Array.from(new Set(items.map(item => item.module))).sort(), [items])
  const visible = useMemo(() => items.filter(item => moduleFilter === 'all' || item.module === moduleFilter), [items, moduleFilter])
  const machineEvaluators = evaluators.filter(item => item.evaluator_kind === 'deterministic')
  const evidenceEvaluators = evaluators.filter(item => item.evaluator_kind === 'evidence')
  const humanEvaluators = evaluators.filter(item => item.evaluator_kind === 'human')
  const moduleDatasetVersions = datasetVersions.filter(item => item.domain === suiteModule)
  const availableModules = Array.from(new Set(datasetVersions.map(item => item.domain).filter((item): item is string => Boolean(item)))).sort()
  const usesRagEvidence = suiteModule === 'rag'

  const createAndPublishEvaluator = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      const config = evaluatorKind === 'human' ? JSON.parse(humanRubricText) as Record<string, unknown> : {}
      const created = await evaluationService.createV2Evaluator({ evaluator_key: evaluatorKey.trim(), version: evaluatorVersion, evaluator_kind: evaluatorKind, config })
      await evaluationService.publishV2Evaluator(created.evaluator_version_id)
      setMessage(`评分器 ${created.evaluator_key}@${created.version} 已发布，指纹 ${created.rubric_hash}。`)
      await load()
      if (created.evaluator_kind === 'human') setHumanEvaluatorId(created.evaluator_version_id)
      else if (created.evaluator_kind === 'evidence') setEvidenceEvaluatorId(created.evaluator_version_id)
      else setEvaluatorId(created.evaluator_version_id)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '创建评分器失败') }
    finally { setBusy(false) }
  }

  const createSuiteDraft = async () => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (!datasetVersionId || (!evaluatorId && !(usesRagEvidence && evidenceEvaluatorId))) {
        throw new Error(usesRagEvidence ? '需要已发布的数据版本和至少一个机器评分器' : '需要已发布的数据版本和确定性评分器')
      }
      const evaluatorVersions: Array<{ evaluator_version_id: string; binding: 'answer_quality' | 'evidence_quality' | 'human_review' }> = []
      if (evaluatorId) evaluatorVersions.push({ evaluator_version_id: evaluatorId, binding: 'answer_quality' })
      if (usesRagEvidence && evidenceEvaluatorId) evaluatorVersions.push({ evaluator_version_id: evidenceEvaluatorId, binding: 'evidence_quality' })
      if (humanEvaluatorId) evaluatorVersions.push({ evaluator_version_id: humanEvaluatorId, binding: 'human_review' })
      const created = await evaluationService.createV2Suite({
        suite_key: suiteKey.trim(), name: suiteName.trim(), module: suiteModule.trim(),
        dataset_version_id: datasetVersionId, version: suiteVersion,
        case_selection: {}, evaluator_versions: evaluatorVersions,
        metric_policy: { min_pass_rate: minimumPassRate },
        security_policy: hardFailureLimit.trim() ? { max_failed_cases: Number(hardFailureLimit) } : {},
        minimum_valid_samples: minimumSamples,
      })
      setDraft({ suite_version_id: created.suite_version_id, snapshot_hash: created.snapshot_hash })
      setMessage(`方案草稿已保存，快照 ${created.snapshot_hash}。确认后发布。`)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '创建方案草稿失败') }
    finally { setBusy(false) }
  }

  const publishDraft = async () => {
    if (!draft) return
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await evaluationService.publishV2Suite(draft.suite_version_id)
      setDraft(null)
      setMessage(`方案版本已发布，快照 ${result.snapshot_hash}。可在“实验运行”启动当前模块已接入的 Runner。`)
      await load()
    } catch (cause) { setError(cause instanceof Error ? cause.message : '发布方案失败') }
    finally { setBusy(false) }
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border border-border-subtle bg-surface-base p-4" aria-labelledby="v2-suite-authoring-heading">
        <div><h2 id="v2-suite-authoring-heading" className="text-base font-semibold text-text-primary">V2 方案与评分器版本</h2><p className="mt-1 text-sm text-text-secondary">只允许绑定已发布 Golden 和评分器版本。人工复核使用冻结 Rubric 留存独立评分，不回写机器分数或自动改变 Gate。</p></div>
        <details className="mt-4 rounded-md border border-border-subtle p-3">
          <summary className="cursor-pointer text-sm font-medium text-text-primary">登记并发布评分器版本</summary>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="text-xs text-text-secondary">评分器标识<input value={evaluatorKey} onChange={event => setEvaluatorKey(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
            <label className="text-xs text-text-secondary">版本<input value={evaluatorVersion} onChange={event => setEvaluatorVersion(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
            <label className="text-xs text-text-secondary">评分方式<select aria-label="评分方式" value={evaluatorKind} onChange={event => setEvaluatorKind(event.target.value as EvaluationV2EvaluatorVersion['evaluator_kind'])} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="deterministic">确定性规则</option><option value="evidence">证据覆盖</option><option value="human">人工评分 Rubric</option><option value="llm_judge" disabled>模型评判（校准与预算接入待完成）</option></select></label>
            <button type="button" onClick={() => void createAndPublishEvaluator()} disabled={busy} className="mt-4 min-h-10 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50">{busy ? '保存中…' : '创建并发布评分器'}</button>
            {evaluatorKind === 'human' && <label className="text-xs text-text-secondary sm:col-span-2 lg:col-span-4">冻结 Rubric JSON（维度权重合计必须为 1）<textarea aria-label="人工评分 Rubric JSON" value={humanRubricText} onChange={event => setHumanRubricText(event.target.value)} rows={3} className="mt-1 w-full rounded-md border border-border-subtle bg-surface-base px-3 py-2 font-mono text-xs" /></label>}
          </div>
        </details>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-text-secondary">方案标识<input value={suiteKey} onChange={event => setSuiteKey(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <label className="text-xs text-text-secondary">方案名称<input value={suiteName} onChange={event => setSuiteName(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <label className="text-xs text-text-secondary">执行模块<select aria-label="执行模块" value={suiteModule} onChange={event => { const module = event.target.value; setSuiteModule(module); setDatasetVersionId(datasetVersions.find(item => item.domain === module)?.dataset_version_id || '') }} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm">{availableModules.map(module => <option key={module} value={module}>{module}{['sql', 'cs', 'e2e'].includes(module) ? '（契约检查）' : ''}</option>)}</select></label>
          <label className="text-xs text-text-secondary">方案版本<input value={suiteVersion} onChange={event => setSuiteVersion(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <label className="text-xs text-text-secondary sm:col-span-2">Golden 数据版本<select value={datasetVersionId} onChange={event => setDatasetVersionId(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">选择已发布数据集版本</option>{moduleDatasetVersions.map(item => <option key={item.dataset_version_id} value={item.dataset_version_id}>{item.version} · {item.case_count ?? 0} 条 · {item.content_hash.slice(0, 12)}</option>)}</select></label>
          <label className="text-xs text-text-secondary sm:col-span-2">确定性评分器<select aria-label="确定性答案评分器（可选）" value={evaluatorId} onChange={event => setEvaluatorId(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">不绑定评分器</option>{machineEvaluators.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}</select></label>
          {usesRagEvidence && <label className="text-xs text-text-secondary sm:col-span-2">证据评分器（可选）<select aria-label="证据评分器" value={evidenceEvaluatorId} onChange={event => setEvidenceEvaluatorId(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">不启用单独证据评分</option>{evidenceEvaluators.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}</select></label>}
          <label className="text-xs text-text-secondary sm:col-span-2">人工复核 Rubric（可选）<select aria-label="人工复核 Rubric" value={humanEvaluatorId} onChange={event => setHumanEvaluatorId(event.target.value)} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle bg-surface-base px-3 text-sm"><option value="">不启用人工复核</option>{humanEvaluators.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}</select></label>
          <label className="text-xs text-text-secondary">最小有效样本<input type="number" min={1} max={20000} value={minimumSamples} onChange={event => setMinimumSamples(Number(event.target.value))} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <label className="text-xs text-text-secondary">最小通过率<input type="number" min={0} max={1} step={0.01} value={minimumPassRate} onChange={event => setMinimumPassRate(Number(event.target.value))} className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <label className="text-xs text-text-secondary">安全失败硬上限<input type="number" min={0} max={20000} value={hardFailureLimit} onChange={event => setHardFailureLimit(event.target.value)} placeholder="不单设硬上限" className="mt-1 min-h-10 w-full rounded-md border border-border-subtle px-3 text-sm" /></label>
          <button type="button" onClick={() => void createSuiteDraft()} disabled={busy || !datasetVersionId || (!evaluatorId && !(usesRagEvidence && evidenceEvaluatorId))} className="mt-4 min-h-10 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50">保存方案草稿</button>
        </div>
        {draft && <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 p-3"><p className="text-sm text-amber-900">待发布版本快照：<code className="break-all">{draft.snapshot_hash}</code></p><button type="button" onClick={() => void publishDraft()} disabled={busy} className="min-h-10 rounded-md bg-accent px-3 text-sm font-medium text-white disabled:opacity-50">发布此方案版本</button></div>}
        <p className="mt-3 text-xs leading-5 text-text-muted">SQL、客服和 E2E 当前只运行 Golden 契约检查，Gate 固定为 INVALID；旅游、旅游 Provider、旅游商品使用现有离线 Runner。预订模块因会改写订单账本，未开放到通用管理端运行。</p>
        {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}{message && <p role="status" className="mt-3 text-sm text-text-secondary">{message}</p>}
      </section>
      <div className="flex flex-wrap items-end justify-between gap-3 rounded-lg border border-border-subtle bg-surface-base p-4">
        <div>
          <label htmlFor="evaluation-suite-module" className="block text-sm font-medium text-text-primary">按业务域筛选</label>
          <select id="evaluation-suite-module" value={moduleFilter} onChange={event => setModuleFilter(event.target.value)} className="mt-2 min-h-11 min-w-52 rounded-md border border-border-subtle bg-surface-base px-3 text-sm text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <option value="all">全部业务域</option>
            {modules.map(module => <option key={module} value={module}>{module}</option>)}
          </select>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex min-h-11 items-center gap-2 rounded-md border border-border-subtle px-3 text-sm text-text-primary hover:bg-bg-hover disabled:opacity-50">
          <RefreshCw size={15} /> 刷新方案
        </button>
      </div>
      <p className="rounded-md border border-border-subtle bg-bg-elevated px-3 py-2 text-xs leading-5 text-text-secondary">列表优先读取 V2 已发布方案；旧静态 Suite 仅以 legacy_unverified 只读展示，不进入 Golden 发布或 Gate 决策。</p>
      {error && <div className="rounded-md border border-red-200 bg-surface-base p-3 text-sm text-red-800" role="alert">{error}</div>}
      {loading && items.length === 0 ? (
        <p className="rounded-lg border border-border-subtle bg-surface-base p-6 text-sm text-text-secondary" role="status">正在读取已登记的 Suite 版本…</p>
      ) : visible.length === 0 ? (
        <EmptyState title="评测方案" description="当前筛选下没有从服务端登记的 Suite。方案列表不使用演示数据。" actionLabel="重试读取" onAction={() => void load()} />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border-subtle bg-surface-base">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="bg-bg-elevated text-xs text-text-secondary">
              <tr><th className="px-4 py-3 font-medium">方案</th><th className="px-4 py-3 font-medium">数据版本</th><th className="px-4 py-3 font-medium">样本数</th><th className="px-4 py-3 font-medium">评测范围</th><th className="px-4 py-3 font-medium">版本</th></tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {visible.map(item => (
                <tr key={item.module + ':' + item.name + ':' + item.version} className="align-top">
                  <td className="px-4 py-3">
                    <details>
                      <summary className="cursor-pointer font-medium text-text-primary">{item.name} <span className="ml-1 rounded bg-bg-elevated px-1.5 py-0.5 text-xs text-text-secondary">{item.module}</span>{item.source === 'legacy_unverified' && <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-800">旧版未核验</span>}</summary>
                      <div className="mt-3 max-w-2xl space-y-2 text-xs text-text-secondary">
                        <p>知识库：{item.kb_id || '未绑定'} · Fixture：{item.fixture_set || '无'}</p>
                        <p>触发分类：{item.trigger_classes?.length ? item.trigger_classes.join('、') : '未登记'}</p>
                        <p>阈值快照：{Object.keys(item.thresholds || {}).length ? Object.entries(item.thresholds).map(pair => pair[0] + ' = ' + String(pair[1])).join('；') : '未登记'}</p>
                        {item.case_ids?.length > 0 && <p>固定用例：{item.case_ids.join('、')}</p>}
                        <details><summary className="cursor-pointer">查看 Prompt / Tool 映射</summary><pre className="mt-2 max-h-56 overflow-auto rounded bg-bg-elevated p-3">{JSON.stringify({ prompt_mapping: item.prompt_mapping, tool_mapping: item.tool_mapping }, null, 2)}</pre></details>
                      </div>
                    </details>
                  </td>
                  <td className="px-4 py-3 text-text-secondary">{item.dataset_version || '未固定'}</td>
                  <td className="px-4 py-3 tabular-nums text-text-primary">{item.case_count}</td>
                  <td className="px-4 py-3 text-text-secondary">{item.fixture_set || item.kb_id || '服务端定义'}</td>
                  <td className="px-4 py-3 text-text-secondary">{item.version || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle px-4 py-3">
            <p className="text-xs text-text-muted">V2 方案固定不可变 Golden 与评分器版本；发布后可在“实验运行”创建模块对应的异步 Run。</p>
            <button type="button" onClick={onNavigate} className="inline-flex min-h-10 items-center gap-1 text-sm font-medium text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
              查看既有运行 <ArrowRight size={15} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
