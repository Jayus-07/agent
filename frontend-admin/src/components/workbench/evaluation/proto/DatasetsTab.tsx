'use client'

/**
 * 数据集 tab —— 按已定稿原型实现：
 * 工具栏（搜索/模块筛选/生成/导入/新建）→ 六列表（版本号可点开历史）→
 * 编辑视图（待确认候选审核 + 已发布用例表格编辑，保存即派生新版本）→
 * 版本历史（发布已批准用例 / 对比 / 回退 / 导出）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, Pencil, Play, Plus, Search, Sparkles, Upload } from 'lucide-react'
import {
  evaluationService,
  type EvaluationV2Candidate,
  type EvaluationV2CandidateReviewRecord,
  type EvaluationV2Dataset,
  type EvaluationV2DatasetVersion,
  type EvaluationV2EvaluatorVersion,
} from '@/api/evaluation'
import { evaluationAssets, type EvaluationTask, type ImportManifest, type RagasQueryCounts, type RagasQueryType } from '@/api/evaluation-assets'
import { createIdempotencyKey } from '@/api/client'
import {
  canPromoteCandidate,
  listFeedbackCandidates,
  promoteFeedbackCandidate,
  reviewFeedbackCandidate,
  type CandidateStatus,
  type FeedbackCandidate,
} from '@/api/feedbackCandidates'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Drawer, Empty, Pagination, StatusBadge, TableWrap, Td, Th, timeShort } from './evalUi'
import EvidenceSearchPicker from './EvidenceSearchPicker'

type View = { kind: 'list' } | { kind: 'edit'; dataset: EvaluationV2Dataset } | { kind: 'history'; dataset: EvaluationV2Dataset }

const MODULE_FILTERS = ['all', 'rag', 'cs', 'sql', 'planner', 'travel'] as const
const MODULE_LABELS: Record<string, string> = { all: '全部模块', rag: 'RAG', cs: '客服', sql: 'SQL', planner: '路由', travel: '旅游' }
const V2_RUNNER_MODULES = new Set(['rag', 'cs', 'sql', 'e2e', 'travel', 'travel-provider'])
const CONTRACT_ONLY_MODULES = new Set(['sql', 'cs', 'e2e'])
const RUNNER_SCOPE_WARNINGS: Record<string, string> = {
  travel: '当前 V2 旅游 Runner 按离线模式执行，不调用真实地图或预订供应商；通过只说明离线评测通过。',
  'travel-provider': '当前仅执行 fake Provider 探针，不触发真实第三方请求；通过不代表真实供应商链路已验收。',
}
const DATASET_PAGE_SIZE = 10
const CANDIDATE_PAGE_SIZE = 10

interface EvidenceClaimLike { kb_id?: string; doc_id?: string; doc_version?: string; quote?: string }

/** 原型中的「数字校验未过」：参考答案含证据原文中不存在的数字 */
function numericMismatch(reference: string, quotes: string[]): boolean {
  const pool = quotes.join(' ')
  const numbers = reference.match(/\d+(?:\.\d+)?/g) || []
  return numbers.length > 0 && numbers.some(item => !pool.includes(item))
}

/** semver 次版本号 +1：1.2.3 → 1.3.0（发布版本不可变，新内容只进新版本号） */
function nextMinorVersion(version: string | undefined): string {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(version || '')
  if (!match) return '1.0.0'
  return `${match[1]}.${Number(match[2]) + 1}.0`
}

function candidateDatasetKey(candidate: EvaluationV2Candidate): string {
  const payload = candidate.payload_json as Record<string, Record<string, unknown>> | null
  const metadata = (payload?.metadata || {}) as Record<string, unknown>
  const sourceRef = (payload?.source_ref || {}) as Record<string, unknown>
  return String(metadata.dataset_key || sourceRef.dataset_key || '')
}

function candidateQuery(candidate: EvaluationV2Candidate): string {
  const payload = candidate.payload_json as Record<string, Record<string, unknown>> | null
  const input = (payload?.input || {}) as Record<string, unknown>
  return String(input.query || '')
}

async function listAllDatasetCandidates(status: 'approved' | 'rejected', datasetKey: string): Promise<EvaluationV2Candidate[]> {
  const items: EvaluationV2Candidate[] = []
  let offset = 0
  while (true) {
    const page = offset === 0
      ? await evaluationService.listV2Candidates(status, 200, datasetKey)
      : await evaluationService.listV2Candidates(status, 200, datasetKey, offset)
    items.push(...page)
    if (page.length < 200) return items
    offset += page.length
  }
}

function promotedCaseRevision(candidate: EvaluationV2Candidate): number | null {
  const payload = candidate.payload_json as Record<string, unknown> | null
  const revision = candidate.promoted_case_revision ?? payload?.verified_case_revision
  const value = Number(revision)
  return Number.isInteger(value) && value > 0 ? value : null
}

function isTraceCandidate(candidate: EvaluationV2Candidate): boolean {
  const payload = candidate.payload_json as Record<string, Record<string, unknown>> | null
  const metadata = (payload?.metadata || {}) as Record<string, unknown>
  const sourceRef = (payload?.source_ref || {}) as Record<string, unknown>
  return candidate.source_kind === 'trace' || metadata.source === 'production_trace' || Boolean(sourceRef.trace_id)
}

export default function DatasetsTab({ onGoRuns, onGoTraces }: { onGoRuns: (datasetKey?: string) => void; onGoTraces?: () => void }) {
  const [datasets, setDatasets] = useState<EvaluationV2Dataset[]>([])
  const [candidates, setCandidates] = useState<EvaluationV2Candidate[]>([])
  const [feedbackCandidates, setFeedbackCandidates] = useState<FeedbackCandidate[]>([])
  const [feedbackStatusFilter, setFeedbackStatusFilter] = useState<CandidateStatus | 'all'>('pending')
  const [datasetPage, setDatasetPage] = useState(0)
  const [feedbackPage, setFeedbackPage] = useState(0)
  const [traceCandidatePage, setTraceCandidatePage] = useState(0)
  const [feedbackNotes, setFeedbackNotes] = useState<Record<string, string>>({})
  const [feedbackLoading, setFeedbackLoading] = useState(true)
  const [feedbackBusyId, setFeedbackBusyId] = useState('')
  const [feedbackError, setFeedbackError] = useState('')
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [search, setSearch] = useState('')
  const [moduleFilter, setModuleFilter] = useState<string>('all')
  const [genOpen, setGenOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [configureDataset, setConfigureDataset] = useState<EvaluationV2Dataset | null>(null)
  const [view, setView] = useState<View>({ kind: 'list' })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [ds, pending, kbCatalog] = await Promise.all([
        evaluationService.listV2Datasets(100),
        evaluationService.listV2Candidates('in_review', 200).catch(() => [] as EvaluationV2Candidate[]),
        listKbAuthority().catch(() => ({ knowledge_bases: [] as KbAuthorityItem[] })),
      ])
      setDatasets(ds); setCandidates(pending)
      setKbs(kbCatalog.knowledge_bases.filter(item => item.active && item.audience === 'test' && !item.alias_for && !item.deprecated))
    } catch (e) { setError(e instanceof Error ? e.message : '读取数据集失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const loadFeedbackCandidates = useCallback(async () => {
    setFeedbackLoading(true); setFeedbackError('')
    try { setFeedbackCandidates((await listFeedbackCandidates()).items) }
    catch (cause) { setFeedbackError(cause instanceof Error ? cause.message : '读取反馈候选失败') }
    finally { setFeedbackLoading(false) }
  }, [])
  useEffect(() => { void loadFeedbackCandidates() }, [loadFeedbackCandidates])

  const filtered = useMemo(() => datasets.filter(item =>
    (moduleFilter === 'all' || item.domain === moduleFilter)
    && (!search.trim() || item.name.toLowerCase().includes(search.trim().toLowerCase()) || item.dataset_key.toLowerCase().includes(search.trim().toLowerCase()))
  ), [datasets, moduleFilter, search])

  const perform = async (fn: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(false) }
  }

  const visibleFeedbackCandidates = useMemo(() => feedbackCandidates.filter(item => feedbackStatusFilter === 'all' || item.status === feedbackStatusFilter), [feedbackCandidates, feedbackStatusFilter])
  const traceCandidates = useMemo(() => candidates.filter(isTraceCandidate), [candidates])
  const currentDatasets = filtered.slice(datasetPage * DATASET_PAGE_SIZE, (datasetPage + 1) * DATASET_PAGE_SIZE)
  const currentFeedback = visibleFeedbackCandidates.slice(feedbackPage * CANDIDATE_PAGE_SIZE, (feedbackPage + 1) * CANDIDATE_PAGE_SIZE)
  const currentTraceCandidates = traceCandidates.slice(traceCandidatePage * CANDIDATE_PAGE_SIZE, (traceCandidatePage + 1) * CANDIDATE_PAGE_SIZE)

  const reviewFeedback = async (candidate: FeedbackCandidate, decision: 'approve' | 'reject') => {
    setFeedbackBusyId(candidate.candidate_id); setFeedbackError(''); setFeedbackMessage('')
    try {
      const reviewed = await reviewFeedbackCandidate(candidate.candidate_id, decision, feedbackNotes[candidate.candidate_id]?.trim() || '')
      setFeedbackCandidates(current => current.map(item => item.candidate_id === reviewed.candidate_id
        ? { ...item, status: reviewed.status, reviewed_at: reviewed.reviewed_at, reviewer: reviewed.reviewer }
        : item))
      setFeedbackMessage(`反馈候选已${decision === 'approve' ? '批准' : '拒绝'}：${candidate.candidate_id.slice(0, 8)}`)
    } catch (cause) { setFeedbackError(cause instanceof Error ? cause.message : '审核反馈候选失败') }
    finally { setFeedbackBusyId('') }
  }

  const promoteFeedback = async (candidate: FeedbackCandidate) => {
    setFeedbackBusyId(candidate.candidate_id); setFeedbackError(''); setFeedbackMessage('')
    try {
      const result = await promoteFeedbackCandidate(candidate.candidate_id)
      setFeedbackCandidates(current => current.map(item => item.candidate_id === candidate.candidate_id ? { ...item, status: result.status } : item))
      setFeedbackMessage(result.appended
        ? `反馈候选已追加为评测用例：${result.case_id || candidate.candidate_id}`
        : `反馈候选已标记为已晋升，评测用例未重复追加：${result.case_id || candidate.candidate_id}`)
    } catch (cause) { setFeedbackError(cause instanceof Error ? cause.message : '晋升反馈候选失败') }
    finally { setFeedbackBusyId('') }
  }

  return <div className="space-y-4">
    {view.kind === 'list' && <>
      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border-subtle bg-surface-base p-2.5">
        <label className="flex items-center gap-1.5 rounded-md border border-border-subtle bg-surface-base px-2.5">
          <Search size={14} className="text-text-muted" aria-hidden="true" />
          <input aria-label="搜索数据集名称" placeholder="搜索数据集名称或标识" value={search} onChange={e => { setSearch(e.target.value); setDatasetPage(0) }} className="h-8 w-52 bg-transparent text-xs text-text-primary outline-none" />
        </label>
        <select aria-label="按模块筛选" value={moduleFilter} onChange={e => { setModuleFilter(e.target.value); setDatasetPage(0) }} className={ctl}>
          {MODULE_FILTERS.map(m => <option key={m} value={m}>{MODULE_LABELS[m]}</option>)}
        </select>
        <span className="flex-1" />
        <button type="button" className={ctl} onClick={() => setGenOpen(true)}><Sparkles size={13} className="mr-1 inline" aria-hidden="true" />从知识库生成</button>
        <button type="button" className={ctl} onClick={() => setImportOpen(true)}><Upload size={13} className="mr-1 inline" aria-hidden="true" />导入数据集</button>
        <button type="button" className={ctl} onClick={onGoTraces}><Plus size={13} className="mr-1 inline" aria-hidden="true" />失败 Trace 回流</button>
        <button type="button" className={ctlPrimary} onClick={() => setNewOpen(true)}><Plus size={13} className="mr-1 inline" aria-hidden="true" />新建数据集</button>
      </div>

      {error && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}
      {message && <p role="status" className="text-xs text-text-secondary">{message}</p>}

      <Card title="评测数据集" subtitle="发布后用于配置评测方案；已发布版本不可变，编辑会生成新版本。">
        {loading ? <Empty text="正在读取数据集…" /> : filtered.length === 0
          ? <Empty text={datasets.length ? '没有匹配的数据集。' : '还没有数据集：从知识库生成、导入或新建。'} />
          : <TableWrap minWidth={860}>
            <thead className="bg-bg-elevated"><tr><Th>数据集</Th><Th>模块</Th><Th end>用例数</Th><Th>当前版本</Th><Th>状态</Th><Th>更新时间</Th><Th end>操作</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {currentDatasets.map(item => <tr key={item.dataset_id}>
                <Td><span className="block font-medium text-text-primary">{item.name}</span><span className="block font-mono text-[11px] text-text-muted">{item.dataset_key}</span></Td>
                <Td>{MODULE_LABELS[item.domain] || item.domain}</Td>
                <Td end>{item.case_count ?? 0}</Td>
                <Td>{item.latest_version
                  ? <button type="button" title="查看版本历史" onClick={() => setView({ kind: 'history', dataset: item })} className="font-medium text-accent underline decoration-dotted underline-offset-4 hover:decoration-solid">v{item.latest_version}</button>
                  : '—'}{item.latest_status === 'draft' && <Badge text="草稿" tone="warn" />}</Td>
                <Td><StatusBadge status={item.latest_status === 'draft' ? 'draft' : item.version_count ? 'published' : 'draft'} /></Td>
                <Td>{timeShort(item.updated_at)}</Td>
                <Td end>
                  <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setView({ kind: 'edit', dataset: item })}><Pencil size={12} className="mr-1 inline" aria-hidden="true" />编辑</button>
                  <button type="button" className={ctl} onClick={() => setConfigureDataset(item)}><Play size={12} className="mr-1 inline" aria-hidden="true" />配置评测</button>
                </Td>
              </tr>)}
            </tbody>
          </TableWrap>}
        <Pagination page={datasetPage} pageSize={DATASET_PAGE_SIZE} total={filtered.length} onPageChange={setDatasetPage} />
      </Card>

      <Card title="反馈候选" subtitle="审核通过后才能晋升为评测用例；重复晋升由服务端幂等处理。">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Badge text="source=feedback" tone="accent" />
          <label className="flex items-center gap-2 text-xs text-text-secondary">状态筛选
            <select aria-label="反馈候选状态筛选" value={feedbackStatusFilter} onChange={event => { setFeedbackStatusFilter(event.target.value as CandidateStatus | 'all'); setFeedbackPage(0) }} className={ctl}>
              <option value="pending">待审核</option><option value="approved">已批准</option><option value="rejected">已拒绝</option><option value="promoted">已晋升</option><option value="all">全部状态</option>
            </select>
          </label>
          <button type="button" className={ctlGhost} disabled={feedbackLoading} onClick={() => void loadFeedbackCandidates()}>刷新</button>
        </div>
        {feedbackError && <p role="alert" className="mb-2 rounded-md border border-red-200 p-2.5 text-xs text-red-700">{feedbackError}</p>}
        {feedbackMessage && <p role="status" className="mb-2 text-xs text-text-secondary">{feedbackMessage}</p>}
        {feedbackLoading ? <Empty text="正在读取反馈候选…" /> : visibleFeedbackCandidates.length === 0
          ? <Empty text={feedbackCandidates.length ? '当前状态下没有反馈候选。' : '暂无来源为 feedback 的候选。'} />
          : <div className="space-y-2">
            {currentFeedback.map(candidate => <article key={candidate.candidate_id} className="rounded-md border border-border-subtle p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge text={candidate.status} tone={candidate.status === 'approved' ? 'good' : candidate.status === 'rejected' ? 'bad' : candidate.status === 'pending' ? 'warn' : 'gray'} />
                <Badge text={candidate.module || '未知模块'} tone="gray" />
                <span className="font-mono text-[11px] text-text-muted">{candidate.candidate_id.slice(0, 8)}</span>
                {candidate.trace_id && <a className="text-xs text-accent hover:underline" href={`/observability/monitoring?tab=traces&q=${encodeURIComponent(candidate.trace_id)}`}>查看 Trace</a>}
              </div>
              <p className="mt-2 text-sm text-text-primary">{candidate.summary || candidate.expected_answer || candidate.correction_text || '（候选未提供摘要）'}</p>
              {candidate.expected_answer && <p className="mt-1 text-xs text-text-secondary">期望答案：{candidate.expected_answer}</p>}
              {candidate.correction_text && <p className="mt-1 text-xs text-text-secondary">纠正内容：{candidate.correction_text}</p>}
              {candidate.reason && <p className="mt-1 text-xs text-text-muted">反馈原因：{candidate.reason}</p>}
              {candidate.status === 'pending' && <>
                <label className="mt-2 block text-xs text-text-secondary">审核备注<input aria-label={`反馈候选 ${candidate.candidate_id} 审核备注`} value={feedbackNotes[candidate.candidate_id] || ''} onChange={event => setFeedbackNotes(current => ({ ...current, [candidate.candidate_id]: event.target.value }))} maxLength={1000} className={`${ctl} mt-1 w-full`} /></label>
                <div className="mt-2 flex justify-end gap-2">
                  <button type="button" className={ctlGhost} disabled={feedbackBusyId === candidate.candidate_id} onClick={() => void reviewFeedback(candidate, 'reject')}>{feedbackBusyId === candidate.candidate_id ? '提交中…' : '拒绝'}</button>
                  <button type="button" className={ctlPrimary} disabled={feedbackBusyId === candidate.candidate_id} onClick={() => void reviewFeedback(candidate, 'approve')}>{feedbackBusyId === candidate.candidate_id ? '提交中…' : '批准'}</button>
                </div>
              </>}
              {canPromoteCandidate(candidate.status) && <div className="mt-2 flex justify-end"><button type="button" className={ctlPrimary} disabled={feedbackBusyId === candidate.candidate_id} onClick={() => void promoteFeedback(candidate)}>{feedbackBusyId === candidate.candidate_id ? '晋升中…' : '晋升为评测用例'}</button></div>}
              {candidate.status === 'promoted' && <p className="mt-2 text-xs text-text-muted">已晋升至评测用例，不会重复追加。</p>}
            </article>)}
          </div>}
        <Pagination page={feedbackPage} pageSize={CANDIDATE_PAGE_SIZE} total={visibleFeedbackCandidates.length} onPageChange={setFeedbackPage} />
      </Card>

      <Card title="Trace 回流候选" subtitle="Trace 原始回答只是问题线索；人工核验参考答案与证据后，才能进入数据集新版本。">
        {candidates.filter(isTraceCandidate).length === 0 ? <Empty text="暂无 Trace 回流候选；可从“失败 Trace”页选择一条服务端异常记录收录。" />
          : <TableWrap minWidth={780}>
            <thead className="bg-bg-elevated"><tr><Th>Trace / 候选</Th><Th>用户问题</Th><Th>目标数据集</Th><Th>状态</Th><Th end>操作</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {currentTraceCandidates.map(candidate => {
                const key = candidateDatasetKey(candidate)
                const target = datasets.find(item => item.dataset_key === key)
                const payload = candidate.payload_json as Record<string, Record<string, unknown>> | null
                const sourceRef = (payload?.source_ref || {}) as Record<string, unknown>
                return <tr key={candidate.candidate_id}>
                  <Td><span className="block font-mono text-[11px] text-text-primary">{String(sourceRef.trace_id || candidate.candidate_id).slice(0, 20)}</span><span className="text-[11px] text-text-muted">{candidate.candidate_id.slice(0, 8)}</span></Td>
                  <Td className="max-w-[300px]"><span className="line-clamp-2 text-text-primary">{candidateQuery(candidate) || '未记录问题'}</span></Td>
                  <Td>{target ? `${target.name} · ${MODULE_LABELS[target.domain] || target.domain}` : key || '未关联'}</Td>
                  <Td><Badge text={candidate.review_status === 'in_review' ? '待核验' : candidate.review_status} tone="warn" /></Td>
                  <Td end>{target ? <button type="button" className={ctlGhost} onClick={() => setView({ kind: 'edit', dataset: target })}>去核验</button> : <span className="text-[11px] text-amber-700">未关联数据集</span>}</Td>
                </tr>
              })}
            </tbody>
          </TableWrap>}
        <Pagination page={traceCandidatePage} pageSize={CANDIDATE_PAGE_SIZE} total={traceCandidates.length} onPageChange={setTraceCandidatePage} />
        <p className="mt-2 text-[11px] text-text-muted">待审核候选最多读取最近 200 条；候选仍保存在服务端，已批准/已拒绝记录不计入此列表。</p>
      </Card>

      {genOpen && <Drawer open title="从知识库生成评测题" subtitle="自动冻结知识库快照，生成结果先进入候选池待核验。" onClose={() => setGenOpen(false)} width="max-w-4xl"><GeneratePanel kbs={kbs} onDone={() => void load()} /></Drawer>}
      {configureDataset && <Drawer open title="配置评测方案" subtitle="绑定一个已发布数据集版本和当前 Runner 支持的评分器；发布后可从运行页启动。" onClose={() => setConfigureDataset(null)} width="max-w-3xl"><ConfigureSuitePanel dataset={configureDataset} onDone={suiteVersionId => { setConfigureDataset(null); onGoRuns(suiteVersionId) }} /></Drawer>}
      {newOpen && <Drawer open title="新建数据集" subtitle="创建数据集后，可导入或生成候选，再审核并发布版本。" onClose={() => setNewOpen(false)} width="max-w-2xl"><NewDatasetForm onDone={() => { setNewOpen(false); void load() }} /></Drawer>}
      {importOpen && <ImportWizard onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); void load() }} />}
    </>}

    {view.kind === 'edit' && <EditView
      dataset={view.dataset}
      candidates={candidates}
      onBack={() => setView({ kind: 'list' })}
      onChanged={() => void load()}
      onOpenHistory={() => setView({ kind: 'history', dataset: view.dataset })}
      perform={perform}
      busy={busy}
    />}
    {view.kind === 'history' && <HistoryView
      dataset={view.dataset}
      onBack={() => setView({ kind: 'list' })}
      perform={perform}
      busy={busy}
      error={error}
      onChanged={() => void load()}
    />}
  </div>
}

function NewDatasetForm({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [domain, setDomain] = useState('rag')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <Card className="border-0">
    <div className="grid gap-3 sm:grid-cols-4">
      <input aria-label="数据集标识" placeholder="标识（如 rag-baseline）" value={key} onChange={e => setKey(e.target.value)} className={ctl} />
      <input aria-label="数据集名称" placeholder="名称" value={name} onChange={e => setName(e.target.value)} className={ctl} />
      <select aria-label="模块" value={domain} onChange={e => setDomain(e.target.value)} className={ctl}>
        {Object.entries(MODULE_LABELS).filter(([k]) => k !== 'all').map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <input aria-label="描述" placeholder="描述（可选）" value={description} onChange={e => setDescription(e.target.value)} className={ctl} />
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    <div className="mt-3 flex gap-2">
      <button type="button" disabled={busy || !key.trim() || !name.trim()} className={ctlPrimary}
        onClick={() => { setBusy(true); setError('')
          evaluationService.createV2Dataset({ dataset_key: key.trim(), name: name.trim(), domain, description: description.trim() })
            .then(onDone).catch((e: unknown) => setError(e instanceof Error ? e.message : '创建失败')).finally(() => setBusy(false)) }}>
        创建
      </button>
    </div>
  </Card>
}

function compatibleEvaluator(
  item: EvaluationV2EvaluatorVersion,
  binding: 'answer_quality' | 'evidence_quality',
  module: string,
): boolean {
  if (item.status !== 'published') return false
  if (binding === 'answer_quality' && item.evaluator_kind !== 'deterministic') return false
  if (binding === 'evidence_quality' && item.evaluator_kind !== 'evidence') return false
  const adapter = String(item.config_json.adapter || '')
  const expected = module === 'rag'
    ? binding === 'answer_quality' ? 'legacy-rag-answer-status-v1' : 'legacy-rag-evidence-metrics-v1'
    : `legacy-${module}-result-status-v1`
  return !adapter || adapter === expected
}

function ConfigureSuitePanel({ dataset, onDone }: { dataset: EvaluationV2Dataset; onDone: (suiteVersionId: string) => void }) {
  const [versions, setVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [evaluators, setEvaluators] = useState<EvaluationV2EvaluatorVersion[]>([])
  const [versionId, setVersionId] = useState('')
  const [answerEvaluatorId, setAnswerEvaluatorId] = useState('')
  const [evidenceEvaluatorId, setEvidenceEvaluatorId] = useState('')
  const [suiteName, setSuiteName] = useState(`${dataset.name}评测方案`)
  const [suiteVersion, setSuiteVersion] = useState('1.0.0')
  const [minimumValidSamples, setMinimumValidSamples] = useState(1)
  const [minimumPassRate, setMinimumPassRate] = useState('0.95')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [draftSuiteVersionId, setDraftSuiteVersionId] = useState('')

  useEffect(() => {
    let active = true
    Promise.all([
      evaluationService.listV2DatasetVersions(dataset.dataset_id),
      evaluationService.listV2Evaluators(),
    ]).then(([items, evaluatorItems]) => {
      if (!active) return
      const publishedVersions = items.filter(item => item.status === 'published')
      setVersions(publishedVersions)
      setEvaluators(evaluatorItems)
      setVersionId(publishedVersions[0]?.dataset_version_id || '')
      const answers = evaluatorItems.filter(item => compatibleEvaluator(item, 'answer_quality', dataset.domain))
      const evidence = evaluatorItems.filter(item => compatibleEvaluator(item, 'evidence_quality', dataset.domain))
      setAnswerEvaluatorId(answers[0]?.evaluator_version_id || '')
      setEvidenceEvaluatorId(evidence[0]?.evaluator_version_id || '')
      setMinimumValidSamples(publishedVersions[0]?.case_count || 1)
    }).catch((cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : '读取发布版本或评分器失败')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [dataset.dataset_id, dataset.domain])

  const selectedVersion = versions.find(item => item.dataset_version_id === versionId)
  const answerOptions = evaluators.filter(item => compatibleEvaluator(item, 'answer_quality', dataset.domain))
  const evidenceOptions = evaluators.filter(item => compatibleEvaluator(item, 'evidence_quality', dataset.domain))
  const moduleSupported = V2_RUNNER_MODULES.has(dataset.domain)
  const isRag = dataset.domain === 'rag'
  const minimumPassRateNumber = Number(minimumPassRate)
  const ready = moduleSupported && Boolean(selectedVersion) && Boolean(answerEvaluatorId)
    && (!isRag || Boolean(evidenceEvaluatorId))
    && suiteName.trim().length > 0 && /^\d+\.\d+\.\d+$/.test(suiteVersion)
    && Number.isInteger(minimumValidSamples) && minimumValidSamples >= 1
    && minimumValidSamples <= (selectedVersion?.case_count || 0)
    && Number.isFinite(minimumPassRateNumber) && minimumPassRateNumber >= 0 && minimumPassRateNumber <= 1

  const publishDraft = async (suiteVersionId: string) => {
    await evaluationService.publishV2Suite(suiteVersionId)
    onDone(suiteVersionId)
  }

  const createAndPublish = async () => {
    if (!selectedVersion || !ready) return
    setBusy(true); setError('')
    try {
      const safeKey = dataset.dataset_key.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^[^a-z]+/, '').slice(0, 36) || 'dataset'
      const suiteKey = `eval_${safeKey}_${Date.now().toString(36)}`.slice(0, 64)
      const evaluatorVersions = [
        { evaluator_version_id: answerEvaluatorId, binding: 'answer_quality' },
        ...(isRag ? [{ evaluator_version_id: evidenceEvaluatorId, binding: 'evidence_quality' }] : []),
      ]
      const created = await evaluationService.createV2Suite({
        suite_key: suiteKey,
        name: suiteName.trim(),
        module: dataset.domain,
        dataset_version_id: selectedVersion.dataset_version_id,
        version: suiteVersion,
        case_selection: {},
        evaluator_versions: evaluatorVersions,
        metric_policy: { min_pass_rate: minimumPassRateNumber },
        security_policy: {},
        minimum_valid_samples: minimumValidSamples,
      })
      setDraftSuiteVersionId(created.suite_version_id)
      await publishDraft(created.suite_version_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '创建或发布方案失败；若草稿已创建，可重试发布。')
    } finally { setBusy(false) }
  }

  const retryPublish = async () => {
    if (!draftSuiteVersionId) return
    setBusy(true); setError('')
    try { await publishDraft(draftSuiteVersionId) }
    catch (cause) { setError(cause instanceof Error ? cause.message : '重试发布失败') }
    finally { setBusy(false) }
  }

  return <div className="space-y-4">
    {loading ? <Empty text="正在读取数据集发布版本和兼容评分器…" /> : <>
      {!moduleSupported && <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{dataset.domain === 'planner' ? 'Planner 尚未接入 V2 Runner，当前不能创建可运行方案。' : `模块 ${dataset.domain} 尚未接入 V2 Runner。`}</p>}
      {CONTRACT_ONLY_MODULES.has(dataset.domain) && <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">当前模块只能做契约检查；运行 Gate 会是 INVALID，不可作为业务质量基线。</p>}
      {RUNNER_SCOPE_WARNINGS[dataset.domain] && <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">{RUNNER_SCOPE_WARNINGS[dataset.domain]}</p>}
      {versions.length === 0
        ? <Empty text="该数据集还没有已发布版本。先完成人工核验并发布 DatasetVersion，再配置方案。" />
        : <>
          <label className="block text-xs text-text-secondary">数据集版本
            <select aria-label="方案数据集版本" value={versionId} onChange={event => {
              const next = versions.find(item => item.dataset_version_id === event.target.value)
              setVersionId(event.target.value)
              setMinimumValidSamples(next?.case_count || 1)
            }} className={`${ctl} mt-1 w-full`}>
              {versions.map(item => <option key={item.dataset_version_id} value={item.dataset_version_id}>v{item.version} · {item.case_count || 0} 条 · {item.content_hash.slice(0, 12)}</option>)}
            </select>
          </label>
          {selectedVersion && <div className="rounded-md bg-bg-elevated p-3 text-xs text-text-secondary">
            <p><b className="text-text-primary">固定范围：</b>{dataset.name} · v{selectedVersion.version} · {selectedVersion.case_count || 0} 条</p>
            <p className="mt-1 break-all font-mono text-[11px] text-text-muted">DatasetVersion {selectedVersion.dataset_version_id} · SHA256 {selectedVersion.content_hash}</p>
          </div>}
          <label className="block text-xs text-text-secondary">方案名称
            <input aria-label="方案名称" value={suiteName} maxLength={160} onChange={event => setSuiteName(event.target.value)} className={`${ctl} mt-1 w-full`} />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="block text-xs text-text-secondary">方案版本
              <input aria-label="方案版本" value={suiteVersion} onChange={event => setSuiteVersion(event.target.value)} placeholder="1.0.0" className={`${ctl} mt-1 w-full`} />
            </label>
            <label className="block text-xs text-text-secondary">最少有效样本
              <input aria-label="最少有效样本" type="number" min={1} max={selectedVersion?.case_count || 1} value={minimumValidSamples} onChange={event => setMinimumValidSamples(Number(event.target.value))} className={`${ctl} mt-1 w-full`} />
            </label>
            <label className="block text-xs text-text-secondary">最低通过率
              <input aria-label="最低通过率" type="number" min={0} max={1} step={0.01} value={minimumPassRate} onChange={event => setMinimumPassRate(event.target.value)} className={`${ctl} mt-1 w-full`} />
            </label>
          </div>
          <div className={`grid gap-3 ${isRag ? 'sm:grid-cols-2' : 'sm:grid-cols-1'}`}>
            <label className="block text-xs text-text-secondary">答案质量评分器
              <select aria-label="答案质量评分器" value={answerEvaluatorId} onChange={event => setAnswerEvaluatorId(event.target.value)} className={`${ctl} mt-1 w-full`}>
                <option value="">选择已发布的确定性评分器</option>
                {answerOptions.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}
              </select>
            </label>
            {isRag && <label className="block text-xs text-text-secondary">检索证据评分器
              <select aria-label="检索证据评分器" value={evidenceEvaluatorId} onChange={event => setEvidenceEvaluatorId(event.target.value)} className={`${ctl} mt-1 w-full`}>
                <option value="">选择已发布的证据评分器</option>
                {evidenceOptions.map(item => <option key={item.evaluator_version_id} value={item.evaluator_version_id}>{item.evaluator_key}@{item.version}</option>)}
              </select>
            </label>}
          </div>
          {moduleSupported && !answerOptions.length && <p role="alert" className="text-xs text-amber-800">没有兼容的已发布答案评分器，需先在 V2 评分器管理中发布 deterministic 版本。</p>}
          {isRag && !evidenceOptions.length && <p role="alert" className="text-xs text-amber-800">RAG 还需要已发布的 evidence 评分器，确保检索证据质量单独计分。</p>}
          <p className="text-[11px] leading-5 text-text-muted">发布会固定数据集哈希、全部用例修订、评分器版本和门槛。RAG 的检索证据评分与答案质量评分分别绑定；发布后不可编辑，调整配置需创建新方案版本。</p>
        </>}
    </>}
    {error && <p role="alert" className="rounded-md border border-red-200 p-3 text-xs text-red-800">{error}</p>}
    {draftSuiteVersionId && error && <button type="button" className={ctl} disabled={busy} onClick={() => void retryPublish()}>{busy ? '发布中…' : '重试发布草稿方案'}</button>}
    {versions.length > 0 && <div className="flex justify-end">
      <button type="button" className={ctlPrimary} disabled={loading || busy || !ready || Boolean(draftSuiteVersionId)} onClick={() => void createAndPublish()}>{busy ? '正在固定并发布…' : '创建并发布方案，进入运行页'}</button>
    </div>}
  </div>
}

const TASK_STATUS_LABELS: Record<string, string> = { PENDING: '排队中', RUNNING: '正在生成', RETRYING: '等待自动重试', SUCCESS: '生成完成', FAILED: '生成失败', CANCELLED: '已取消', PAUSED: '已暂停' }

interface GenerationBatch {
  key: string
  kbId: string
  kbName: string
  datasetKey: string
  batchIndex: number
  batchCount: number
  count: number
  queryCounts: RagasQueryCounts
  datasetQueryCounts: RagasQueryCounts
  status: string
  taskId?: string
  progress?: string
  error?: string
}

const RAGAS_QUERY_TYPES: RagasQueryType[] = [
  'single_hop_specific',
  'multi_hop_abstract',
  'multi_hop_specific',
]
const MULTI_HOP_QUERY_TYPES = new Set<RagasQueryType>(['multi_hop_abstract', 'multi_hop_specific'])
const RAGAS_QUERY_LABELS: Record<RagasQueryType, string> = {
  single_hop_specific: '单跳具体',
  multi_hop_abstract: '多跳抽象',
  multi_hop_specific: '多跳具体',
}
const RAGAS_QUERY_DESCRIPTIONS: Record<RagasQueryType, string> = {
  single_hop_specific: '答案可由一段证据直接支持',
  multi_hop_abstract: '跨多段内容归纳主题后回答',
  multi_hop_specific: '组合多段事实回答具体问题',
}
const RAGAS_REVIEW_CHECKS = [
  { key: 'question_clear_and_non_duplicate', label: '问题清晰、无歧义且与现有题不重复' },
  { key: 'answer_facts_supported', label: '参考答案中的事实都能从原文推出' },
  { key: 'required_facts_complete', label: '必需事实已补齐，能据此判定答案质量' },
  { key: 'evidence_covers_answer', label: '证据覆盖答案的全部关键事实' },
  { key: 'query_type_matches_reasoning', label: '题型与实际推理步骤一致' },
] as const
type RagasReviewCheck = typeof RAGAS_REVIEW_CHECKS[number]['key']
type FactEvidenceSelection = Record<number, number[]>

function splitRequiredFacts(value: string): string[] {
  return value.split(/[,，]/).map(item => item.trim()).filter(Boolean)
}

function hasFactEvidenceCoverage(
  facts: string[],
  selection: FactEvidenceSelection,
  evidenceCount: number,
  isMultiHop: boolean,
): boolean {
  if (!facts.length || facts.some((_fact, index) => !selection[index]?.length)) return false
  const mappedEvidence = new Set(facts.flatMap((_fact, index) => selection[index] || []))
  if ([...mappedEvidence].some(index => index < 0 || index >= evidenceCount)) return false
  if (!isMultiHop) return mappedEvidence.size > 0
  const distinctFacts = new Set(facts.map(fact => fact.replace(/\s+/g, '').toLowerCase()))
  return distinctFacts.size >= 2 && mappedEvidence.size >= 2
}

function presetRagasQueryCounts(total: number): RagasQueryCounts {
  if (total === 10) return { single_hop_specific: 5, multi_hop_abstract: 3, multi_hop_specific: 2 }
  if (total === 20) return { single_hop_specific: 10, multi_hop_abstract: 5, multi_hop_specific: 5 }
  return { single_hop_specific: 3, multi_hop_abstract: 1, multi_hop_specific: 1 }
}

function sumRagasQueryCounts(counts: RagasQueryCounts): number {
  return RAGAS_QUERY_TYPES.reduce((sum, type) => sum + counts[type], 0)
}

function allocateRagasQueryCounts(kbIds: string[], counts: RagasQueryCounts): Map<string, RagasQueryCounts> {
  const allocations = new Map(kbIds.map(id => [id, {
    single_hop_specific: 0,
    multi_hop_abstract: 0,
    multi_hop_specific: 0,
  }]))
  if (!kbIds.length) return allocations

  let remainderCursor = 0
  for (const type of RAGAS_QUERY_TYPES) {
    const base = Math.floor(counts[type] / kbIds.length)
    let remainder = counts[type] % kbIds.length
    kbIds.forEach(id => { allocations.get(id)![type] = base })
    while (remainder > 0) {
      const id = kbIds[remainderCursor % kbIds.length]
      allocations.get(id)![type] += 1
      remainderCursor += 1
      remainder -= 1
    }
  }
  return allocations
}

function splitRagasQueryCounts(counts: RagasQueryCounts): RagasQueryCounts[] {
  const remaining = { ...counts }
  const batches: RagasQueryCounts[] = []
  while (sumRagasQueryCounts(remaining) > 0) {
    const batch: RagasQueryCounts = {
      single_hop_specific: 0,
      multi_hop_abstract: 0,
      multi_hop_specific: 0,
    }
    while (sumRagasQueryCounts(batch) < 5 && sumRagasQueryCounts(remaining) > 0) {
      for (const type of RAGAS_QUERY_TYPES) {
        if (sumRagasQueryCounts(batch) >= 5) break
        if (remaining[type] > 0) {
          batch[type] += 1
          remaining[type] -= 1
        }
      }
    }
    batches.push(batch)
  }
  return batches
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

function GeneratePanel({ kbs, onDone }: { kbs: KbAuthorityItem[]; onDone: () => void }) {
  const [search, setSearch] = useState('')
  const [selectedKbIds, setSelectedKbIds] = useState<Set<string>>(new Set())
  const [queryCounts, setQueryCounts] = useState<RagasQueryCounts>(() => presetRagasQueryCounts(5))
  const [enabledQueryTypes, setEnabledQueryTypes] = useState<Set<RagasQueryType>>(() => new Set(RAGAS_QUERY_TYPES))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [finished, setFinished] = useState(false)
  const [batches, setBatches] = useState<GenerationBatch[]>([])
  const [preview, setPreview] = useState<EvaluationV2Candidate[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [adopting, setAdopting] = useState(false)
  const visibleKbs = useMemo(() => kbs.filter(item => `${item.name} ${item.kb_id}`.toLowerCase().includes(search.trim().toLowerCase())), [kbs, search])
  const selectedKbs = useMemo(() => kbs.filter(item => selectedKbIds.has(item.kb_id)), [kbs, selectedKbIds])
  const activeQueryCounts = useMemo(() => Object.fromEntries(
    RAGAS_QUERY_TYPES.map(type => [type, enabledQueryTypes.has(type) ? queryCounts[type] : 0]),
  ) as RagasQueryCounts, [enabledQueryTypes, queryCounts])
  const count = sumRagasQueryCounts(activeQueryCounts)
  const queryAllocations = useMemo(
    () => allocateRagasQueryCounts(selectedKbs.map(item => item.kb_id), activeQueryCounts),
    [activeQueryCounts, selectedKbs],
  )
  const allocations = useMemo(() => new Map(
    Array.from(queryAllocations.entries()).map(([kbId, counts]) => [kbId, sumRagasQueryCounts(counts)]),
  ), [queryAllocations])
  const multiHopCount = activeQueryCounts.multi_hop_abstract + activeQueryCounts.multi_hop_specific
  const allocationCanCoverEachKb = selectedKbs.length === 0 || (
    selectedKbs.length <= multiHopCount &&
    (activeQueryCounts.single_hop_specific === 0 || selectedKbs.length <= activeQueryCounts.single_hop_specific)
  )
  const plannedBatches = useMemo(() => Array.from(allocations.entries()).filter(([, amount]) => amount > 0).reduce((sum, [, amount]) => sum + Math.ceil(amount / 5), 0), [allocations])

  const patchBatch = (key: string, patch: Partial<GenerationBatch>) => {
    setBatches(current => current.map(item => item.key === key ? { ...item, ...patch } : item))
  }

  const submit = async () => {
    if (!selectedKbs.length || busy || !allocationCanCoverEachKb || count <= 0) return
    const timestamp = Date.now().toString(36)
    const plan: GenerationBatch[] = selectedKbs.flatMap(kb => {
      const allocatedCounts = queryAllocations.get(kb.kb_id)
      if (!allocatedCounts || !sumRagasQueryCounts(allocatedCounts)) return []
      const datasetSlug = kb.kb_id.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').slice(0, 32) || 'kb'
      const datasetKey = `synthetic_${datasetSlug}_${timestamp}`
      const chunkCounts = splitRagasQueryCounts(allocatedCounts)
      return chunkCounts.map((batchQueryCounts, index) => ({
        key: `${kb.kb_id}:${index + 1}`,
        kbId: kb.kb_id,
        kbName: kb.name,
        datasetKey,
        batchIndex: index + 1,
        batchCount: chunkCounts.length,
        count: sumRagasQueryCounts(batchQueryCounts),
        queryCounts: batchQueryCounts,
        datasetQueryCounts: allocatedCounts,
        status: '待提交',
      }))
    })
    if (!plan.length) return
    setBatches(plan); setBusy(true); setError(''); setFinished(false); setPreview(null)
    const datasetKeys = new Set(plan.map(item => item.datasetKey))
    const startedAt = new Date().toLocaleString('zh-CN')

    for (const kb of selectedKbs.filter(item => (allocations.get(item.kb_id) || 0) > 0)) {
      const kbBatches = plan.filter(item => item.kbId === kb.kb_id)
      let snapshotId = ''
      try {
        kbBatches.forEach(item => patchBatch(item.key, { status: '冻结 KB 快照' }))
        const snapshot = await evaluationAssets.freeze(kb.kb_id)
        snapshotId = snapshot.snapshot_id
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : '冻结知识库快照失败'
        kbBatches.forEach(item => patchBatch(item.key, { status: '失败', error: message }))
        continue
      }

      for (const batch of kbBatches) {
        try {
          patchBatch(batch.key, { status: '提交中', progress: `快照 ${snapshotId.slice(0, 8)}` })
          let task: EvaluationTask = await evaluationAssets.synthesize(
            snapshotId,
            {
              dataset_key: batch.datasetKey,
              name: `从知识库生成 · ${kb.name} · ${startedAt}`,
              module: 'rag',
              schema_version: 'rag-eval/1.0',
              version: `1.0.${batch.batchIndex - 1}`,
              description: `来源知识库 ${kb.kb_id}，快照 ${snapshotId}`,
            } as unknown as ImportManifest,
            batch.count,
            batch.queryCounts,
            batch.datasetQueryCounts,
            createIdempotencyKey(),
          )
          patchBatch(batch.key, { status: task.status, taskId: task.task_id, progress: task.progress })
          while (!['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status)) {
            await sleep(2500)
            task = await evaluationAssets.task(task.task_id)
            patchBatch(batch.key, { status: task.status, progress: task.progress })
          }
          patchBatch(batch.key, { status: task.status, progress: task.progress, ...(task.status === 'FAILED' ? { error: task.error || task.progress || '生成任务失败' } : {}) })
        } catch (cause) {
          patchBatch(batch.key, { status: 'FAILED', error: cause instanceof Error ? cause.message : '生成任务失败' })
        }
      }
    }

    setFinished(true)
    onDone()
    try {
      let candidates: EvaluationV2Candidate[] = []
      for (let attempt = 0; attempt < 12; attempt += 1) {
        const items = await evaluationService.listV2Candidates('in_review', 200)
        candidates = items.filter(item => datasetKeys.has(candidateDatasetKey(item)))
        if (candidates.length || attempt === 11) break
        await sleep(1500)
      }
      setPreview(candidates)
      setPicked(new Set(candidates.map(item => item.candidate_id)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取生成候选失败；候选仍保存在待核验队列。')
    } finally { setBusy(false) }
  }

  const adopt = () => {
    const drop = (preview || []).filter(item => !picked.has(item.candidate_id))
    setAdopting(true)
    Promise.all(drop.map(item =>
      evaluationService.reviewV2Candidate(item.candidate_id, { expected_revision: item.revision, verdict: 'rejected', rationale: '生成预览中未勾选采纳' }).catch(() => undefined)
    )).then(() => {
      setPreview(null)
      onDone()
    }).finally(() => setAdopting(false))
  }

  return <Card className="border-0">
    <p className="text-xs leading-5 text-text-muted">仅显示 `audience=test` 评测知识库，生成模型会读取所选库的冻结语料。每个知识库独立形成数据集；总量超过单任务 5 题时按同一 KB 快照自动拆批。</p>
    <div className="mt-3 grid gap-4 lg:grid-cols-[1fr_1fr]">
      <section className="rounded-md border border-border-subtle p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <h4 className="text-xs font-medium text-text-primary">选择知识库</h4>
          <input aria-label="搜索知识库" placeholder="搜索名称或标识" value={search} onChange={event => setSearch(event.target.value)} className={`${ctl} w-48`} disabled={busy} />
        </div>
        <div className="max-h-52 space-y-1 overflow-y-auto">
          {visibleKbs.map(item => <label key={item.kb_id} className={`flex items-center gap-2 rounded px-2 py-2 text-xs ${item.doc_count ? 'text-text-secondary hover:bg-bg-elevated' : 'text-text-muted'}`}>
            <input type="checkbox" aria-label={`选择知识库 ${item.kb_id}`} checked={selectedKbIds.has(item.kb_id)} disabled={busy || item.doc_count === 0} onChange={event => setSelectedKbIds(current => {
              const next = new Set(current)
              if (event.target.checked) next.add(item.kb_id); else next.delete(item.kb_id)
              return next
            })} />
            <span className="min-w-0 flex-1 truncate">{item.name} · {item.kb_id}</span>
            <span className="shrink-0 text-[11px] text-text-muted">{item.doc_count} 篇</span>
          </label>)}
          {visibleKbs.length === 0 && <Empty text={kbs.length ? '没有匹配的测试知识库。' : '当前账号无可用的 test audience 知识库。'} />}
        </div>
      </section>
      <section className="rounded-md border border-border-subtle p-3">
        <h4 className="text-xs font-medium text-text-primary">题型与规模</h4>
        <div className="mt-2 flex gap-2" role="radiogroup" aria-label="生成题数预设">
          {[5, 10, 20].map(value => {
            const preset = presetRagasQueryCounts(value)
            const selected = count === value && RAGAS_QUERY_TYPES.every(type => activeQueryCounts[type] === preset[type])
            return <button key={value} type="button" role="radio" aria-checked={selected} className={selected ? ctlPrimary : ctl} disabled={busy} onClick={() => {
              setQueryCounts(preset)
              setEnabledQueryTypes(new Set(RAGAS_QUERY_TYPES))
              setError('')
            }}>{value} 题</button>
          })}
        </div>
        <p className="mt-3 text-[11px] text-text-muted">Ragas 原生混合题型 · 默认约一半单跳、一半多跳，可调整每类题数</p>
        <div className="mt-2 space-y-2">
          {RAGAS_QUERY_TYPES.map(type => {
            const activeMultiTypes = RAGAS_QUERY_TYPES.filter(item => MULTI_HOP_QUERY_TYPES.has(item) && enabledQueryTypes.has(item))
            const isLastMultiType = MULTI_HOP_QUERY_TYPES.has(type) && enabledQueryTypes.has(type) && activeMultiTypes.length === 1
            return <div key={type} className="flex items-center gap-2 rounded bg-bg-elevated px-2 py-2">
              <input type="checkbox" aria-label={`启用${RAGAS_QUERY_LABELS[type]}`} checked={enabledQueryTypes.has(type)} disabled={busy || isLastMultiType} onChange={event => {
                if (event.target.checked && count + queryCounts[type] > 20) { setError('启用该题型后将超过 20 题上限'); return }
                setError('')
                setEnabledQueryTypes(current => {
                  const next = new Set(current)
                  if (event.target.checked) next.add(type); else next.delete(type)
                  return next
                })
              }} />
              <span className="min-w-0 flex-1">
                <span className="block text-xs font-medium text-text-primary">{RAGAS_QUERY_LABELS[type]}</span>
                <span className="block text-[10px] text-text-muted">{RAGAS_QUERY_DESCRIPTIONS[type]}</span>
              </span>
              <input type="number" aria-label={`${RAGAS_QUERY_LABELS[type]}题数`} min={1} max={20} value={queryCounts[type]} disabled={busy || !enabledQueryTypes.has(type)} onChange={event => {
                const parsed = Number.parseInt(event.target.value || '1', 10)
                const amount = Math.max(1, Math.min(20, Number.isNaN(parsed) ? 1 : parsed))
                const otherCount = count - activeQueryCounts[type]
                if (otherCount + amount > 20) { setError('单次最多配置 20 题'); return }
                setError('')
                setQueryCounts(current => ({ ...current, [type]: amount }))
              }} className={`${ctl} w-16 text-right`} />
              <span className="text-[11px] text-text-muted">题</span>
            </div>
          })}
        </div>
        <p className="mt-2 text-right text-xs font-medium text-text-primary">本次合计 {count} 题</p>
        <p className="mt-3 text-[11px] text-text-muted">提交前分配预览（按题型分到各库，每个任务最多 5 题）</p>
        {selectedKbs.length === 0 ? <Empty text="先选择一个或多个有文档的测试知识库。" /> : <TableWrap minWidth={600}>
          <thead className="bg-bg-elevated"><tr><Th>知识库来源</Th>{RAGAS_QUERY_TYPES.map(type => <Th key={type} end>{RAGAS_QUERY_LABELS[type]}</Th>)}<Th end>合计</Th><Th end>拆分任务</Th></tr></thead>
          <tbody className="divide-y divide-border-subtle">{selectedKbs.map(item => {
            const amount = allocations.get(item.kb_id) || 0
            const perType = queryAllocations.get(item.kb_id)
            return <tr key={item.kb_id}><Td>{item.name}</Td>{RAGAS_QUERY_TYPES.map(type => <Td key={type} end>{perType?.[type] || 0}</Td>)}<Td end>{amount}</Td><Td end>{amount ? Math.ceil(amount / 5) : '—'}</Td></tr>
          })}</tbody>
        </TableWrap>}
        {!allocationCanCoverEachKb && <p role="alert" className="mt-2 text-[11px] text-amber-700">当前题量不足以让每个知识库至少获得一题多跳题；启用单跳时也需给每库分到一题单跳。请增加题量或减少知识库。</p>}
        {selectedKbs.length > 0 && <p className="mt-2 text-[11px] text-text-muted">计划提交 {plannedBatches} 个任务；产出 {selectedKbs.filter(item => (allocations.get(item.kb_id) || 0) > 0).length} 个独立数据集。</p>}
      </section>
    </div>
    <div className="mt-3 flex items-center justify-between gap-3">
      <p className="text-[11px] text-text-muted">候选只进入人工待核验池；不跨 KB 混合集，也不会自动发布。</p>
      <button type="button" className={ctlPrimary} disabled={busy || !selectedKbs.length || !allocationCanCoverEachKb || plannedBatches === 0} onClick={() => void submit()}>{busy ? '按知识库逐批生成…' : '开始生成'}</button>
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    {batches.length > 0 && <section className="mt-3 space-y-2" aria-label="生成任务进度">
      <h4 className="text-xs font-medium text-text-primary">按知识库的批次进度</h4>
      {batches.map(batch => <div key={batch.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md bg-bg-elevated px-3 py-2 text-[11px] text-text-secondary">
        <span className="font-medium text-text-primary">{batch.kbName}</span>
        <span>批次 {batch.batchIndex}/{batch.batchCount} · {batch.count} 题</span>
        <Badge text={TASK_STATUS_LABELS[batch.status] || batch.status} tone={batch.status === 'SUCCESS' ? 'good' : batch.status === 'FAILED' ? 'bad' : ['待提交', '冻结 KB 快照', '提交中'].includes(batch.status) ? 'blue' : 'gray'} />
        {batch.taskId && <span className="font-mono text-text-muted">Task {batch.taskId.slice(0, 8)}</span>}
        {batch.progress && <span className="text-text-muted">{batch.progress}</span>}
        {batch.error && <span role="alert" className="text-red-700">{batch.error}</span>}
      </div>)}
    </section>}
    {preview && preview.length > 0 && <div className="mt-3 space-y-2">
      <p className="text-xs font-medium text-text-primary">生成候选预览<span className="ml-2 font-normal text-text-muted">（共 {preview.length} 条 · 按来源 KB 分开保留 · 勾选后继续人工核验）</span></p>
      <TableWrap minWidth={860}>
        <thead className="bg-bg-elevated"><tr><Th>选</Th><Th>知识库来源</Th><Th>题型</Th><Th>问题</Th><Th>参考答案</Th><Th>证据引用</Th><Th>校验</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {preview.map(item => {
            const payload = item.payload_json as Record<string, Record<string, unknown> | Array<Record<string, unknown>>> | null
            const metadata = (payload?.metadata || {}) as Record<string, unknown>
            const facts = ((payload?.proposed_facts as Array<Record<string, unknown>> | undefined)?.[0] || {}) as { reference_answer?: string; required_facts?: string[] }
            const evidence = (payload?.proposed_evidence as Array<Record<string, unknown>> | undefined) || []
            const queryType = String(metadata.query_type || '') as RagasQueryType
            const datasetKey = candidateDatasetKey(item)
            const sourceKb = batches.find(batch => batch.datasetKey === datasetKey)?.kbName || datasetKey
            const reference = String(facts.reference_answer || '')
            const quotes = evidence.map(entry => String(entry.quote || ''))
            return <tr key={item.candidate_id}>
              <Td><input type="checkbox" aria-label="选择该条" checked={picked.has(item.candidate_id)} onChange={e => {
                const next = new Set(picked)
                if (e.target.checked) next.add(item.candidate_id); else next.delete(item.candidate_id)
                setPicked(next)
              }} /></Td>
              <Td>{sourceKb}</Td>
              <Td><Badge text={RAGAS_QUERY_LABELS[queryType] || '未知题型'} tone={MULTI_HOP_QUERY_TYPES.has(queryType) ? 'blue' : 'gray'} /></Td>
              <Td className="max-w-[240px]">{candidateQuery(item)}</Td>
              <Td className="max-w-[240px]"><span className="block truncate">{reference || '—'}</span></Td>
              <Td className="max-w-[220px]">{evidence.length
                ? <span className="font-mono text-[11px] text-text-muted">{String(evidence[0].doc_id || '')}{evidence.length > 1 ? ` 等 ${evidence.length} 条` : ''}</span>
                : <Badge text="⚠ 无证据" tone="warn" />}</Td>
              <Td>{numericMismatch(reference, quotes)
                ? <Badge text="数字校验未过" tone="bad" />
                : (facts.required_facts || []).length
                  ? <Badge text="✓ 完整" tone="good" />
                  : <Badge text="必需事实待补充" tone="warn" />}</Td>
            </tr>
          })}
        </tbody>
      </TableWrap>
      <div className="flex items-center justify-end gap-2">
        <span className="flex-1 text-xs text-text-muted">已勾选 <b className="tabular-nums">{picked.size}</b> / {preview.length} 条；「数字校验未过」表示参考答案含证据原文中不存在的数字，建议人工修改后再批准。</span>
        <button type="button" className={ctlPrimary} disabled={adopting} onClick={adopt}>采纳进草稿</button>
      </div>
    </div>}
    {finished && preview?.length === 0 && <div className="mt-2 space-y-2">
      <p role="status" className="rounded-md bg-emerald-50 p-2.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
        本批任务已结束。候选已进入待确认列表；请在对应数据集的编辑视图中核对来源、参考答案与证据后批准。
      </p>
      <button type="button" className={ctlPrimary} onClick={onDone}>查看待确认候选</button>
    </div>}
    {finished && batches.some(batch => batch.status === 'FAILED') && <p role="alert" className="mt-2 text-xs text-red-700">部分批次失败；已成功批次的候选仍可核验，失败项可在上方按知识库与 Task ID 追查。</p>}
  </Card>
}

function ImportWizard({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [domain, setDomain] = useState('rag')
  const [datasetId, setDatasetId] = useState('')
  const [datasets, setDatasets] = useState<EvaluationV2Dataset[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<{ valid: boolean; case_count: number; valid_count?: number; duplicates?: number; errors: Array<{ line: number; field: string; message: string }> } | null>(null)
  const [skipErrors, setSkipErrors] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [imported, setImported] = useState<{ count: number; skipped: number } | null>(null)
  useEffect(() => { evaluationService.listV2Datasets(100).then(setDatasets).catch(() => setDatasets([])) }, [])
  const inspect = () => { if (!file || !datasetId) return; setBusy(true); setError('')
    evaluationService.previewV2Import(file, { schema_version: domain === 'rag' ? 'rag-eval/1.0' : 'evaluation-v2/1.0', dataset_key: datasets.find(d => d.dataset_id === datasetId)?.dataset_key || '', module: domain, name: datasets.find(d => d.dataset_id === datasetId)?.name || 'imported', version: '1.0.0' } as unknown as ImportManifest)
      .then(setPreview).catch((e: unknown) => setError(e instanceof Error ? e.message : '预检失败')).finally(() => setBusy(false))
  }
  const validCount = preview ? (preview.valid_count ?? Math.max(0, preview.case_count - preview.errors.length)) : 0
  const doImport = () => { if (!file || !datasetId || !preview) return
    if (!preview.valid && !(skipErrors && validCount > 0)) return
    setBusy(true); setError('')
    evaluationService.importV2Candidates(file, { schema_version: domain === 'rag' ? 'rag-eval/1.0' : 'evaluation-v2/1.0', dataset_key: datasets.find(d => d.dataset_id === datasetId)?.dataset_key || '', module: domain, name: datasets.find(d => d.dataset_id === datasetId)?.name || 'imported', version: '1.0.0' } as unknown as ImportManifest, createIdempotencyKey(), { skipErrors: !preview.valid && skipErrors })
      .then(result => { setImported({ count: result.candidate_count, skipped: result.skipped ?? 0 }); setStep(3); onDone() })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '导入失败')).finally(() => setBusy(false))
  }
  return <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-6" role="dialog" aria-modal="true" aria-label="导入评测数据集">
    <div className="w-full max-w-xl rounded-xl border border-border-subtle bg-surface-base shadow-xl">
      <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
        <h3 className="text-sm font-semibold text-text-primary">导入评测数据集</h3>
        <button type="button" aria-label="关闭导入向导" className={ctlGhost} onClick={onClose}>✕</button>
      </div>
      <div className="space-y-3 px-4 py-4">
        <div className="flex gap-1.5 text-xs">
          {['1 · 下载模板并上传', '2 · 校验预览', '3 · 完成'].map((label, i) => <span key={label} className={`rounded-full px-2.5 py-1 ${step === i + 1 ? 'bg-accent-soft font-medium text-accent' : 'bg-bg-elevated text-text-muted'}`}>{label}</span>)}
        </div>
        {step === 1 && <>
          <div className="grid gap-3 sm:grid-cols-2">
            <select aria-label="导入目标数据集" value={datasetId} onChange={e => setDatasetId(e.target.value)} className={ctl}>
              <option value="">选择目标数据集</option>
              {datasets.map(item => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.dataset_key}</option>)}
            </select>
            <select aria-label="模块" value={domain} onChange={e => setDomain(e.target.value)} className={ctl}>
              {Object.entries(MODULE_LABELS).filter(([k]) => k !== 'all').map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={ctl} onClick={() => void evaluationService.downloadImportTemplate(domain, domain === 'rag' ? 'jsonl' : 'csv')}><Download size={13} className="mr-1 inline" aria-hidden="true" />{domain === 'rag' ? 'JSONL 模板（RAG）' : `CSV 模板（${domain}）`}</button>
            <button type="button" className={ctlGhost} onClick={() => void evaluationService.downloadImportTemplate(domain, 'fields')}><Download size={13} className="mr-1 inline" aria-hidden="true" />字段说明</button>
          </div>
          <label className="block rounded-lg border border-dashed border-border-subtle p-5 text-center text-xs text-text-secondary">
            <input type="file" accept=".json,.jsonl,.csv" className="mx-auto block text-xs" onChange={e => setFile(e.target.files?.[0] || null)} aria-label="评测文件" />
            <span className="mt-1 block text-text-muted">.jsonl / .json / .csv · ≤ 5 MB · UTF-8（CSV 请用 Excel 另存为「CSV UTF-8」）</span>
          </label>
        </>}
        {step === 2 && preview && <>
          <div className="flex gap-6 rounded-md bg-bg-elevated p-3 text-xs text-text-secondary">
            <span><b className="block text-sm text-text-primary tabular-nums">{preview.case_count}</b>解析行数</span>
            <span><b className="block text-sm text-text-primary tabular-nums">{validCount}</b>合法</span>
            <span><b className="block text-sm text-text-primary tabular-nums">{preview.errors.length}</b>错误</span>
            <span><b className="block text-sm text-text-primary tabular-nums">{preview.duplicates ?? 0}</b>与现有重复</span>
          </div>
          {preview.errors.length > 0 && <div className="max-h-40 overflow-auto rounded-md border border-border-subtle">
            <table className="w-full text-left text-xs"><thead className="bg-bg-elevated text-text-muted"><tr><th className="px-2 py-1.5">行号</th><th className="px-2 py-1.5">字段</th><th className="px-2 py-1.5">问题</th></tr></thead>
              <tbody className="divide-y divide-border-subtle">{preview.errors.slice(0, 12).map((item, i) => <tr key={i}><td className="px-2 py-1.5 tabular-nums">{item.line}</td><td className="px-2 py-1.5">{item.field}</td><td className="px-2 py-1.5 text-red-700">{item.message}</td></tr>)}</tbody></table>
          </div>}
          {preview.errors.length > 0 && <label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={skipErrors} onChange={e => setSkipErrors(e.target.checked)} />跳过 {preview.errors.length} 条错误行，仅导入合法的 {validCount} 条</label>}
          {!preview.valid && !skipErrors && <p className="text-xs text-red-700">存在错误行，导入将被拒绝。</p>}
        </>}
        {step === 3 && imported && <p className="text-xs text-text-secondary">已写入 <b className="tabular-nums">{imported.count}</b> 条候选{imported.skipped > 0 ? `，跳过 ${imported.skipped} 条错误行` : ''}：在编辑视图中补齐真值并批准后，发布为新版本。发布前不影响任何正式评测。</p>}
        {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      </div>
      <div className="flex justify-end gap-2 border-t border-border-subtle px-4 py-3">
        <button type="button" className={ctlGhost} onClick={onClose}>取消</button>
        {step === 1 && <button type="button" className={ctlPrimary} disabled={!file || !datasetId || busy} onClick={() => { inspect(); setStep(2) }}>{busy ? '处理中…' : '下一步：校验预览'}</button>}
        {step === 2 && <>
          <button type="button" className={ctl} onClick={() => setStep(1)}>上一步</button>
          <button type="button" className={ctlPrimary} disabled={busy || !preview || (!preview.valid && !(skipErrors && validCount > 0))} onClick={doImport}>确认导入</button>
        </>}
        {step === 3 && <button type="button" className={ctlPrimary} onClick={onDone}>完成</button>}
      </div>
    </div>
  </div>
}

interface VersionRow {
  case_id: string
  revision: number
  question: string
  reference: string
  facts: string
  behavior: 'answer' | 'refuse'
  reason?: string
  evidence: EvidenceClaimLike[]
  /** 原始内容快照：保存时用它判断该行是否需要派生修订 */
  orig: string
  deleted: boolean
}

function rowSnapshot(row: Omit<VersionRow, 'orig' | 'deleted'>): string {
  return JSON.stringify([row.question, row.reference, row.facts, row.behavior, row.reason || '', row.evidence])
}

function parseExportRows(blobText: string): VersionRow[] {
  const lines = blobText.split('\n').filter(line => line.trim())
  const rows: VersionRow[] = []
  for (const line of lines.slice(1)) {
    let item: Record<string, unknown>
    try { item = JSON.parse(line) as Record<string, unknown> } catch { continue }
    if (item.type !== 'case') continue
    const input = (item.input || {}) as Record<string, unknown>
    const expected = (item.expected || {}) as Record<string, unknown>
    const behavior = String(expected.behavior || 'answer') === 'refuse' ? 'refuse' as const : 'answer' as const
    const evidence = (Array.isArray(item.evidence) ? item.evidence : []) as EvidenceClaimLike[]
    const base = {
      case_id: String(item.case_id || ''),
      revision: Number(item.revision || 0),
      question: String(input.query || item.question || ''),
      reference: String(expected.reference_answer || ''),
      facts: ((expected.required_facts as string[] | undefined) || []).join(', '),
      behavior,
      reason: String(expected.reason || '') || undefined,
      evidence,
    }
    rows.push({ ...base, orig: rowSnapshot(base), deleted: false })
  }
  return rows
}

function buildExpected(row: Omit<VersionRow, 'orig' | 'deleted'>): Record<string, unknown> {
  const facts = row.facts.split(/[,，]/).map(item => item.trim()).filter(Boolean)
  if (row.behavior === 'refuse') return { behavior: 'refuse', reason: row.reason || 'no_evidence' }
  return { behavior: 'answer', reference_answer: row.reference.trim(), required_facts: facts }
}

function buildEvidence(row: Omit<VersionRow, 'orig' | 'deleted'>, isRag: boolean): Array<{ kb_id: string; doc_id: string; doc_version: string; quote: string }> {
  if (!isRag || row.behavior === 'refuse') return []
  return row.evidence
    .filter(item => item.kb_id && item.doc_id && item.quote)
    .map(item => ({
      kb_id: String(item.kb_id || ''),
      doc_id: String(item.doc_id || ''),
      doc_version: String(item.doc_version || ''),
      quote: String(item.quote || ''),
    }))
}

function EditView({ dataset, candidates, onBack, onChanged, onOpenHistory, perform, busy }: {
  dataset: EvaluationV2Dataset
  candidates: EvaluationV2Candidate[]
  onBack: () => void
  onChanged: () => void
  onOpenHistory: () => void
  perform: (fn: () => Promise<void>) => Promise<void>
  busy: boolean
}) {
  // 只展示明确关联到当前数据集的候选，避免把其它业务域的答案误发布进来。
  const [sourceFilter, setSourceFilter] = useState<'all' | 'feedback' | 'trace'>('all')
  const [candidatePage, setCandidatePage] = useState(0)
  const scopedCandidates = candidates.filter(candidate => candidateDatasetKey(candidate) === dataset.dataset_key)
  const mine = sourceFilter === 'feedback'
    ? scopedCandidates.filter(candidate => candidate.source_kind === 'feedback')
    : sourceFilter === 'trace'
      ? scopedCandidates.filter(isTraceCandidate)
      : scopedCandidates
  const visibleCandidates = mine.slice(candidatePage * CANDIDATE_PAGE_SIZE, (candidatePage + 1) * CANDIDATE_PAGE_SIZE)
  const [edits, setEdits] = useState<Record<string, { question: string; reference: string; facts: string; snapshotId: string }>>({})
  const [qualityChecks, setQualityChecks] = useState<Record<string, Partial<Record<RagasReviewCheck, boolean>>>>({})
  const [reviewRationales, setReviewRationales] = useState<Record<string, string>>({})
  const [factEvidenceBindings, setFactEvidenceBindings] = useState<Record<string, FactEvidenceSelection>>({})
  const [reviewedCandidates, setReviewedCandidates] = useState<EvaluationV2Candidate[]>([])
  const [reviewedCandidatesError, setReviewedCandidatesError] = useState('')
  const [manualOpen, setManualOpen] = useState(false)
  const [manual, setManual] = useState({ question: '', reference: '', facts: '' })
  const [manualMessage, setManualMessage] = useState('')
  useEffect(() => {
    let active = true
    setReviewedCandidates([])
    setReviewedCandidatesError('')
    Promise.all([
      listAllDatasetCandidates('approved', dataset.dataset_key),
      listAllDatasetCandidates('rejected', dataset.dataset_key),
    ]).then(([approved, rejected]) => {
      if (active) {
        setReviewedCandidates([...approved, ...rejected]
          .filter(candidate => candidateDatasetKey(candidate) === dataset.dataset_key)
          .sort((left, right) => right.created_at.localeCompare(left.created_at)))
      }
    }).catch(cause => {
      if (active) {
        setReviewedCandidates([])
        setReviewedCandidatesError(cause instanceof Error ? cause.message : '审核候选读取失败')
      }
    })
    return () => { active = false }
  }, [dataset.dataset_key])
  const recordReviewedCandidate = (candidate: EvaluationV2Candidate, reviewStatus: 'approved' | 'rejected') => {
    const reviewed = { ...candidate, review_status: reviewStatus }
    setReviewedCandidates(current => [reviewed, ...current.filter(item => item.candidate_id !== candidate.candidate_id)])
  }
  const addManual = () => perform(async () => {
    if (!manual.question.trim() || !manual.reference.trim()) throw new Error('问题和参考答案为必填')
    const facts = manual.facts.split(/[,，]/).map(item => item.trim()).filter(Boolean)
    await evaluationService.createV2Candidate({
      source_kind: 'manual',
      payload: {
        input: { query: manual.question.trim() },
        proposed_facts: [{ behavior: 'answer', reference_answer: manual.reference.trim(), required_facts: facts }],
        proposed_evidence: [],
        metadata: { dataset_key: dataset.dataset_key, dataset_name: dataset.name, tags: ['manual'] },
      },
      idempotency_key: createIdempotencyKey(),
    })
    setManual({ question: '', reference: '', facts: '' })
    setManualOpen(false)
    setManualMessage('已创建手工候选，批准后可发布。')
    onChanged()
  })
  const getEdit = (id: string, candidate: EvaluationV2Candidate) => edits[id] || {
    question: candidateQuery(candidate),
    reference: String((candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_facts?.[0]?.reference_answer || ''),
    facts: ((candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_facts?.[0]?.required_facts as string[] | undefined || []).join(', '),
    snapshotId: String((candidate.payload_json as Record<string, Record<string, string>> | null)?.metadata?.snapshot_id || ''),
  }
  const setEdit = (id: string, patch: Partial<{ question: string; reference: string; facts: string; snapshotId: string }>) =>
    setEdits(current => ({ ...current, [id]: { ...getEdit(id, candidates.find(c => c.candidate_id === id)!), ...patch } }))
  const setCandidateFacts = (id: string, value: string) => {
    setEdit(id, { facts: value })
    setFactEvidenceBindings(current => ({ ...current, [id]: {} }))
  }
  const approve = (candidate: EvaluationV2Candidate) => perform(async () => {
    const edit = getEdit(candidate.candidate_id, candidate)
    const facts = splitRequiredFacts(edit.facts)
    const payload = candidate.payload_json as Record<string, unknown>
    const metadata = (payload.metadata || {}) as Record<string, unknown>
    const isRagasGenerated = metadata.generation_method === 'ragas_mixed'
    // 与展示同源：优先用户经选择器追加的锚点，否则用生成/导入自动带出的证据
    const payloadEvidence = (payload.proposed_evidence as Array<Record<string, unknown>> | undefined) || []
    const evidence = (JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || 'null') || payloadEvidence) as Array<Record<string, unknown>>
    await evaluationService.reviewV2Candidate(candidate.candidate_id, {
      expected_revision: candidate.revision,
      verdict: 'approved',
      rationale: isRagasGenerated
        ? String(reviewRationales[candidate.candidate_id] || '').trim()
        : '编辑器确认：补齐真值与证据后批准',
      expected: {
        behavior: 'answer',
        reference_answer: edit.reference,
        required_facts: facts,
      },
      ...(isRagasGenerated ? {
        quality_checks: Object.fromEntries(
          RAGAS_REVIEW_CHECKS.map(check => [check.key, qualityChecks[candidate.candidate_id]?.[check.key] === true]),
        ),
        fact_evidence_bindings: facts.map((_fact, fact_index) => ({
          fact_index,
          evidence_indices: factEvidenceBindings[candidate.candidate_id]?.[fact_index] || [],
        })),
      } : {}),
      evidence: evidence.map(item => ({
        kb_id: String(item.kb_id || ''),
        doc_id: String(item.doc_id || ''),
        doc_version: String(item.doc_version || ''),
        quote: String(item.quote || ''),
      })),
    })
    recordReviewedCandidate(candidate, 'approved')
    sessionStorage.removeItem(`evidence:${candidate.candidate_id}`)
    onChanged()
  })
  const discard = (candidate: EvaluationV2Candidate) => perform(async () => {
    const payload = candidate.payload_json as Record<string, unknown>
    const metadata = (payload.metadata || {}) as Record<string, unknown>
    const isRagasGenerated = metadata.generation_method === 'ragas_mixed'
    const rationale = isRagasGenerated
      ? String(reviewRationales[candidate.candidate_id] || '').trim()
      : '编辑器中丢弃'
    if (isRagasGenerated && rationale.length < 20) {
      throw new Error('Ragas 候选拒绝理由至少填写 20 个字符')
    }
    const payloadEvidence = (payload.proposed_evidence as Array<Record<string, unknown>> | undefined) || []
    const evidence = (JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || 'null') || payloadEvidence) as Array<Record<string, unknown>>
    await evaluationService.reviewV2Candidate(candidate.candidate_id, {
      expected_revision: candidate.revision,
      verdict: 'rejected',
      rationale,
      evidence: evidence.map(item => ({
        kb_id: String(item.kb_id || ''),
        doc_id: String(item.doc_id || ''),
        doc_version: String(item.doc_version || ''),
        quote: String(item.quote || ''),
      })),
    })
    recordReviewedCandidate(candidate, 'rejected')
    sessionStorage.removeItem(`evidence:${candidate.candidate_id}`)
    onChanged()
  })
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={ctl} onClick={onBack}>← 返回列表</button>
      <span className="text-sm font-medium text-text-primary">编辑：{dataset.name} · {dataset.latest_version ? `v${dataset.latest_version}` : '尚无版本'}</span>
      <span className="flex-1" />
      <button type="button" className={ctl} onClick={onOpenHistory}>版本历史</button>
      <button type="button" className={ctlPrimary} onClick={() => setManualOpen(v => !v)}><Plus size={13} className="mr-1 inline" aria-hidden="true" />添加用例</button>
    </div>
    {manualOpen && <Card title="手工新增用例" subtitle="提交后进入待确认候选，与其他来源候选一并审核批准。">
      <div className="grid gap-2 sm:grid-cols-2">
        <input aria-label="新用例问题" placeholder="问题（必填）" value={manual.question} onChange={e => setManual(m => ({ ...m, question: e.target.value }))} className={ctl} />
        <input aria-label="新用例参考答案" placeholder="参考答案（必填）" value={manual.reference} onChange={e => setManual(m => ({ ...m, reference: e.target.value }))} className={ctl} />
        <input aria-label="新用例必需事实" placeholder="必需事实（逗号分隔）" value={manual.facts} onChange={e => setManual(m => ({ ...m, facts: e.target.value }))} className={ctl} />
        <div className="flex items-center gap-2">
          <button type="button" className={ctlPrimary} disabled={busy || !manual.question.trim() || !manual.reference.trim()} onClick={addManual}>创建候选</button>
          <button type="button" className={ctlGhost} onClick={() => setManualOpen(false)}>收起</button>
          {manualMessage && <span role="status" className="text-xs text-text-secondary">{manualMessage}</span>}
        </div>
      </div>
    </Card>}
    <Card title="待确认候选" subtitle="与导入模板同 schema：问题 / 参考答案 / 必需事实 / 证据引用；批准后到「版本历史」发布新版本。">
      <label className="mb-3 flex items-center gap-2 text-xs text-text-secondary">候选来源
        <select aria-label="按候选来源筛选" value={sourceFilter} onChange={event => { setSourceFilter(event.target.value as 'all' | 'feedback' | 'trace'); setCandidatePage(0) }} className={ctl}>
          <option value="all">全部来源</option><option value="feedback">反馈</option><option value="trace">Trace 回流</option>
        </select>
      </label>
      {mine.length === 0 ? <Empty text={sourceFilter === 'feedback' ? '该数据集暂无反馈候选。' : sourceFilter === 'trace' ? '该数据集暂无 Trace 回流候选。' : '该数据集暂无待确认候选：可从知识库生成、导入或收录 Trace。'} />
        : <div className="space-y-3">
          {visibleCandidates.map(candidate => {
            const edit = getEdit(candidate.candidate_id, candidate)
            const complete = edit.reference.trim() !== ''
            // 证据 = 生成/导入自动带出的锚点 + 用户经证据选择器追加的锚点（sessionStorage 暂存）
            const baseEvidence = (candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_evidence || []
            const sessionEvidence = JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || 'null') as Array<Record<string, unknown>> | null
            const evidenceList = (sessionEvidence || baseEvidence) as Array<{ doc_id?: string; doc_version?: string; quote?: string }>
            const candidatePayload = candidate.payload_json as Record<string, unknown>
            const candidateMetadata = (candidatePayload.metadata || {}) as Record<string, unknown>
            const candidateSourceRef = (candidatePayload.source_ref || {}) as Record<string, unknown>
            const traceId = String(candidateSourceRef.trace_id || candidateMetadata.trace_id || '')
            const isRagasGenerated = candidateMetadata.generation_method === 'ragas_mixed'
            const queryType = String(candidateMetadata.query_type || '')
            const isMultiHop = MULTI_HOP_QUERY_TYPES.has(queryType as RagasQueryType)
            const reviewChecks = qualityChecks[candidate.candidate_id] || {}
            const reviewRationale = reviewRationales[candidate.candidate_id] || ''
            const requiredFacts = splitRequiredFacts(edit.facts)
            const factEvidenceSelection = factEvidenceBindings[candidate.candidate_id] || {}
            const uniqueEvidenceCount = new Set(evidenceList.map(item => String(item.quote || '').trim()).filter(Boolean)).size
            const qualityChecksComplete = RAGAS_REVIEW_CHECKS.every(check => reviewChecks[check.key] === true)
            const qualityReady = !isRagasGenerated || (
              qualityChecksComplete && reviewRationale.trim().length >= 20 && Boolean(edit.facts.trim()) &&
              uniqueEvidenceCount >= (isMultiHop ? 2 : 1) &&
              hasFactEvidenceCoverage(requiredFacts, factEvidenceSelection, evidenceList.length, isMultiHop)
            )
            return <div key={candidate.candidate_id} className="rounded-md border border-border-subtle p-3">
              {isTraceCandidate(candidate) && <div className="mb-3 rounded-md border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-900 dark:bg-amber-950/30">
                <div className="flex flex-wrap items-center gap-2"><Badge text="Trace 回流" tone="accent" /><Badge text="参考答案未核验" tone="warn" />{traceId && <a className="text-xs text-accent hover:underline" href={`/observability/traces/${encodeURIComponent(traceId)}`} target="_blank" rel="noreferrer">打开 Trace</a>}</div>
                <p className="mt-2 text-[11px] font-medium text-text-secondary">Trace 实际回答（只供核对，不作为真值）</p>
                <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-text-secondary">{String(candidatePayload.observed_answer || 'Trace 未记录回答')}</p>
                {Boolean(candidateMetadata.trace_error) && <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">异常摘要：{String(candidateMetadata.trace_error)}</p>}
              </div>}
              <div className="grid gap-2 sm:grid-cols-2">
                <input aria-label="问题" placeholder="问题" value={edit.question} onChange={e => setEdit(candidate.candidate_id, { question: e.target.value })} className={ctl} />
                <input aria-label="参考答案" placeholder="参考答案 reference_answer" value={edit.reference} onChange={e => setEdit(candidate.candidate_id, { reference: e.target.value })} className={ctl} />
                <input aria-label="必需事实" placeholder="必需事实（逗号分隔）" value={edit.facts} onChange={e => setCandidateFacts(candidate.candidate_id, e.target.value)} className={ctl} />
                <div className="flex items-center gap-2">
                  <Badge text={complete ? '✓ 可批准' : '⚠ 缺参考答案'} tone={complete ? 'good' : 'warn'} />
                  <Badge text={isTraceCandidate(candidate) ? 'Trace 回流' : candidate.source_kind || '未知来源'} tone={isTraceCandidate(candidate) || candidate.source_kind === 'feedback' ? 'accent' : 'gray'} />
                  {!edit.facts.trim() && <Badge text="必需事实待补充" tone="warn" />}
                  <Badge text={evidenceList.length ? `证据 ${evidenceList.length} 条` : '⚠ 无证据'} tone={evidenceList.length ? 'blue' : 'warn'} />
                  <span className="font-mono text-[11px] text-text-muted">{candidate.candidate_id.slice(0, 8)}</span>
                </div>
              </div>
              <div className="mt-2 rounded-md bg-bg-elevated p-2.5">
                <p className="text-[11px] font-medium text-text-muted">自动带出的证据引用</p>
                {evidenceList.length === 0
                  ? <p className="mt-1 text-xs text-amber-600">无证据引用：生成的候选应自动带出证据；若持续为空，可用下方证据选择器检索快照切片补齐。</p>
                  : <ul className="mt-1 space-y-1">
                    {evidenceList.map((item, i) => <li key={i} className="text-xs text-text-secondary">
                      <span className="font-mono text-[11px] text-text-muted">{item.doc_id || '（未知文档）'}{item.doc_version ? ` · ${item.doc_version}` : ''}</span>
                      <span className="ml-1.5">{String(item.quote || '').slice(0, 90)}{String(item.quote || '').length > 90 ? '…' : ''}</span>
                    </li>)}
                  </ul>}
              </div>
              {isRagasGenerated && <section aria-label="必需事实证据关联" className="mt-2 rounded-md border border-border-subtle p-2.5">
                <p className="text-xs font-medium text-text-primary">按事实关联原文证据</p>
                <p className="mt-1 text-[11px] text-text-muted">每条必需事实都要选择能支持它的原文；提交时由服务端核对锚点。多跳题至少要有两条不同事实和两条不同锚点。</p>
                {!requiredFacts.length ? <p className="mt-2 text-xs text-amber-700">先填写必需事实，再逐条选择对应原文。</p>
                  : !evidenceList.length ? <p className="mt-2 text-xs text-amber-700">当前没有证据引用，不能完成事实核验。</p>
                    : <div className="mt-2 space-y-2">
                      {requiredFacts.map((fact, factIndex) => <fieldset key={`${factIndex}-${fact}`} className="rounded-md bg-bg-elevated p-2">
                        <legend className="px-1 text-xs text-text-secondary">事实 {factIndex + 1}：{fact}</legend>
                        <div className="mt-1 grid gap-1 sm:grid-cols-2">
                          {evidenceList.map((item, evidenceIndex) => {
                            const selected = factEvidenceSelection[factIndex] || []
                            const checked = selected.includes(evidenceIndex)
                            const quote = String(item.quote || '')
                            return <label key={`${evidenceIndex}-${item.doc_id || ''}`} className="flex items-start gap-2 text-[11px] text-text-secondary">
                              <input
                                type="checkbox"
                                aria-label={`事实 ${factIndex + 1} 的证据引用 ${evidenceIndex + 1}（${item.doc_id || '未知文档'}）`}
                                checked={checked}
                                onChange={event => setFactEvidenceBindings(current => {
                                  const currentForCandidate = current[candidate.candidate_id] || {}
                                  const next = new Set(currentForCandidate[factIndex] || [])
                                  if (event.target.checked) next.add(evidenceIndex)
                                  else next.delete(evidenceIndex)
                                  return {
                                    ...current,
                                    [candidate.candidate_id]: {
                                      ...currentForCandidate,
                                      [factIndex]: [...next].sort((left, right) => left - right),
                                    },
                                  }
                                })}
                              />
                              <span title={quote}>{evidenceIndex + 1}. {item.doc_id || '未知文档'} · {quote.slice(0, 100)}{quote.length > 100 ? '…' : ''}</span>
                            </label>
                          })}
                        </div>
                      </fieldset>)}
                    </div>}
              </section>}
              {isRagasGenerated && <section aria-label="Ragas 生成题质量核验" className="mt-2 rounded-md border border-border-subtle p-2.5">
                <p className="text-xs font-medium text-text-primary">人工质量核验</p>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  {RAGAS_REVIEW_CHECKS.map(check => <label key={check.key} className="flex items-start gap-2 text-xs text-text-secondary">
                    <input
                      type="checkbox"
                      aria-label={`质量核验：${check.label}`}
                      checked={reviewChecks[check.key] === true}
                      onChange={event => setQualityChecks(current => ({
                        ...current,
                        [candidate.candidate_id]: {
                          ...current[candidate.candidate_id],
                          [check.key]: event.target.checked,
                        },
                      }))}
                    />
                    <span>{check.label}</span>
                  </label>)}
                </div>
                <textarea
                  aria-label="人工审核理由（Ragas候选）"
                  rows={2}
                  maxLength={2000}
                  value={reviewRationale}
                  onChange={event => setReviewRationales(current => ({ ...current, [candidate.candidate_id]: event.target.value }))}
                  placeholder="批准时说明可判分依据；拒绝时说明题型、重复或证据问题（至少 20 字）"
                  className={`${ctl} mt-2 w-full`}
                />
                <p className="mt-1 text-[11px] text-text-muted">
                  批准时每条必需事实都要关联原文；多跳题至少拆成两条不同事实并覆盖不同锚点。批准或拒绝理由、当次引用都会写入核验记录。
                </p>
              </section>}
              <div className="mt-2">
                <EvidenceSearchPicker defaultSnapshotId={edit.snapshotId} question={edit.question} onInsert={(claim: { kb_id: string; doc_id: string; doc_version: string; quote: string }) => {
                  const payloadEvidence2 = (candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_evidence || []
                  const current = JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || JSON.stringify(payloadEvidence2)) as Array<Record<string, unknown>>
                  sessionStorage.setItem(`evidence:${candidate.candidate_id}`, JSON.stringify([...current, claim]))
                  setEdit(candidate.candidate_id, {})
                }} />
              </div>
              <div className="mt-2 flex justify-end gap-2">
                <button
                  type="button"
                  className={ctlGhost}
                  disabled={busy || (isRagasGenerated && reviewRationale.trim().length < 20)}
                  onClick={() => discard(candidate)}
                >{isRagasGenerated ? '拒绝候选' : '丢弃'}</button>
                <button type="button" className={ctlPrimary} disabled={busy || !complete || evidenceList.length === 0 || !qualityReady} onClick={() => approve(candidate)}>批准进草稿</button>
              </div>
            </div>
          })}
        </div>}
      <Pagination page={candidatePage} pageSize={CANDIDATE_PAGE_SIZE} total={mine.length} onPageChange={setCandidatePage} />
      <p className="mt-2 text-xs text-text-muted">批准 = 完成八道服务端校验（证据锚点逐字核验、四眼原则等）并生成不可变用例修订；随后在「版本历史」中发布新版本。</p>
    </Card>
    <CandidateReviewHistory candidates={reviewedCandidates} loadError={reviewedCandidatesError} />
    <VersionCaseEditor dataset={dataset} perform={perform} busy={busy} onChanged={onChanged} onOpenHistory={onOpenHistory} />
  </div>
}

function CandidateReviewHistory({ candidates, loadError }: { candidates: EvaluationV2Candidate[]; loadError: string }) {
  const [selectedId, setSelectedId] = useState('')
  const [records, setRecords] = useState<Record<string, EvaluationV2CandidateReviewRecord[]>>({})
  const [loadingId, setLoadingId] = useState('')
  const [error, setError] = useState('')

  const toggleHistory = async (candidateId: string) => {
    if (selectedId === candidateId) {
      setSelectedId('')
      setError('')
      return
    }
    setSelectedId(candidateId)
    setError('')
    if (records[candidateId]) return
    setLoadingId(candidateId)
    try {
      const history = await evaluationService.listV2CandidateReviews(candidateId)
      setRecords(current => ({ ...current, [candidateId]: history }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '审核记录读取失败')
    } finally {
      setLoadingId('')
    }
  }

  return <Card title="审核记录" subtitle="逐次保留审核结论、理由、证据锚点和核验项；多跳审核还记录每条事实对应的锚点。批准只表示进入可发布候选。">
    {loadError ? <p role="alert" className="text-xs text-red-700">读取已完成审核候选失败：{loadError}</p>
      : candidates.length === 0 ? <Empty text="该数据集暂无已完成审核的候选。" /> : <div className="space-y-2">
      {candidates.map(candidate => {
        const history = records[candidate.candidate_id]
        const metadata = (candidate.payload_json.metadata || {}) as Record<string, unknown>
        return <article key={candidate.candidate_id} className="rounded-md border border-border-subtle p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 text-sm text-text-primary">{candidateQuery(candidate) || '（问题为空）'}</span>
            <Badge text={candidate.review_status === 'approved' ? '已批准' : '已拒绝'} tone={candidate.review_status === 'approved' ? 'good' : 'warn'} />
            {metadata.generation_method === 'ragas_mixed' && <Badge text="Ragas 混合生成" tone="accent" />}
            <button type="button" className={ctl} aria-label={`查看审核记录 ${candidate.candidate_id}`} onClick={() => void toggleHistory(candidate.candidate_id)}>
              {selectedId === candidate.candidate_id ? '收起记录' : '查看记录'}
            </button>
          </div>
          {selectedId === candidate.candidate_id && <div className="mt-3 border-t border-border-subtle pt-3">
            {loadingId === candidate.candidate_id ? <Empty text="正在读取审核记录…" />
              : error ? <p role="alert" className="text-xs text-red-700">{error}</p>
                : !history?.length ? <Empty text="没有可显示的审核记录。" />
                  : <div className="space-y-3">
                    {history.map(record => {
                      const checks = record.changed_fields.quality_checks as Record<string, boolean> | undefined
                      const factBindings = record.changed_fields.fact_evidence_bindings as Array<{
                        fact_index: number
                        fact: string
                        anchor_ids: string[]
                      }> | undefined
                      return <section key={record.annotation_id} className="rounded-md bg-bg-elevated p-3">
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
                          <span>审核人：{record.reviewer_id}</span>
                          <span>结论：{record.verdict === 'approved' ? '批准' : record.verdict === 'rejected' ? '拒绝' : '要求修改'}</span>
                          <span>候选修订：r{record.candidate_revision}</span>
                          <span>时间：{timeShort(record.created_at)}</span>
                        </div>
                        <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-text-secondary">审核理由：{record.rationale}</p>
                        {checks && <div className="mt-2">
                          <p className="text-[11px] font-medium text-text-muted">Ragas 质量核验</p>
                          <ul className="mt-1 grid gap-1 sm:grid-cols-2">
                            {RAGAS_REVIEW_CHECKS.map(check => <li key={check.key} className="text-xs text-text-secondary">
                              {checks[check.key] === true ? '✓' : '✗'} {check.label}
                            </li>)}
                          </ul>
                        </div>}
                        {factBindings?.length ? <div className="mt-2">
                          <p className="text-[11px] font-medium text-text-muted">事实与已核验锚点对应</p>
                          <ul className="mt-1 space-y-1">
                            {factBindings.map(binding => <li key={binding.fact_index} className="text-xs text-text-secondary">
                              事实 {binding.fact_index + 1}：{binding.fact} → {binding.anchor_ids.join('、')}
                            </li>)}
                          </ul>
                        </div> : null}
                        {record.evidence_json.length > 0 && <div className="mt-2">
                          <p className="text-[11px] font-medium text-text-muted">
                            {record.verdict === 'approved'
                              ? record.evidence_json.some(item => String(item.quote || '').trim())
                                ? '已核验的原文引用'
                                : '已核验锚点；本记录未保存原文引用文本'
                              : '拒绝时的证据引用（不表示证据已通过核验）'}
                          </p>
                          <ul className="mt-1 space-y-1">
                            {record.evidence_json.map((evidence, evidenceIndex) => {
                              const quote = String(evidence.quote || '')
                              return <li key={evidenceIndex} className="text-xs text-text-secondary">
                                <span className="font-mono text-[11px] text-text-muted">
                                  {String(evidence.doc_id || '文档')} · {String(evidence.doc_version || '未知版本')}
                                  {evidence.anchor_id ? ` · 锚点 ${String(evidence.anchor_id)}` : ''}
                                </span>
                                {quote
                                  ? <span className="ml-1.5" title={quote}>{quote.slice(0, 500)}{quote.length > 500 ? '…' : ''}</span>
                                  : record.verdict === 'approved' && <span className="ml-1.5 text-text-muted">审核时未保存引用文本。</span>}
                              </li>
                            })}
                          </ul>
                        </div>}
                      </section>
                    })}
                  </div>}
          </div>}
        </article>
      })}
    </div>}
  </Card>
}

/** 已发布版本用例表格：编辑 = 派生修订 + 发布新版本（copy-on-write，历史永不改动） */
function VersionCaseEditor({ dataset, perform, busy, onChanged, onOpenHistory }: {
  dataset: EvaluationV2Dataset
  perform: (fn: () => Promise<void>) => Promise<void>
  busy: boolean
  onChanged: () => void
  onOpenHistory: () => void
}) {
  const [versions, setVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [versionId, setVersionId] = useState('')
  const [rows, setRows] = useState<VersionRow[]>([])
  const [draftVersion, setDraftVersion] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const isRag = dataset.domain === 'rag'
  const publishedVersions = versions.filter(item => item.status === 'published')

  useEffect(() => {
    evaluationService.listV2DatasetVersions(dataset.dataset_id).then(items => {
      setVersions(items)
      const latestPublished = items.filter(item => item.status === 'published')[0]
      if (latestPublished) setVersionId(current => current || latestPublished.dataset_version_id)
      setDraftVersion(nextMinorVersion(items[0]?.version))
    }).catch(() => setVersions([]))
  }, [dataset.dataset_id])

  const loadVersion = useCallback((targetId: string) => {
    if (!targetId) { setRows([]); return }
    setLoading(true); setError(''); setMessage('')
    evaluationService.exportV2DatasetVersion(dataset.dataset_id, targetId)
      .then(blob => blob.text())
      .then(text => setRows(parseExportRows(text)))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '读取版本用例失败'))
      .finally(() => setLoading(false))
  }, [dataset.dataset_id])
  useEffect(() => { if (versionId) loadVersion(versionId) }, [versionId, loadVersion])

  const patchRow = (index: number, patch: Partial<VersionRow>) =>
    setRows(current => current.map((row, i) => (i === index ? { ...row, ...patch } : row)))

  const dirtyRow = (row: VersionRow): Omit<VersionRow, 'orig' | 'deleted'> => ({
    case_id: row.case_id, revision: row.revision, question: row.question, reference: row.reference,
    facts: row.facts, behavior: row.behavior, reason: row.reason, evidence: row.evidence,
  })
  const changedRows = rows.filter(row => !row.deleted && row.case_id && rowSnapshot(dirtyRow(row)) !== row.orig)
  const deletedCount = rows.filter(row => row.deleted).length
  const addedRows = rows.filter(row => !row.case_id && !row.deleted)

  const save = () => perform(async () => {
    const version = draftVersion.trim()
    if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('新版本号必须是三段式（如 1.3.0）')
    if (addedRows.some(row => !row.question.trim())) throw new Error('新增用例的问题不能为空')
    const items: Array<{ case_id: string; revision: number }> = []
    for (const row of rows) {
      if (row.deleted) continue
      const current = dirtyRow(row)
      const changed = row.case_id && rowSnapshot(current) !== row.orig
      if (row.case_id && !changed) { items.push({ case_id: row.case_id, revision: row.revision }); continue }
      if (row.case_id) {
        // 已有用例：创建修订候选并批准，得到新的不可变修订
        const candidate = await evaluationService.createV2CaseRevisionCandidate(row.case_id, {
          query: current.question.trim(),
          observed_answer: current.reference.trim(),
          idempotency_key: createIdempotencyKey(),
        })
        const reviewed = await evaluationService.reviewV2Candidate(candidate.candidate_id, {
          expected_revision: candidate.revision,
          verdict: 'approved',
          rationale: '版本编辑器派生修订',
          expected: buildExpected(current),
          evidence: buildEvidence(current, isRag),
        })
        items.push({ case_id: String(reviewed.promoted_case_id || row.case_id), revision: Number(reviewed.promoted_case_revision || row.revision) })
      } else {
        const candidate = await evaluationService.createV2Candidate({
          source_kind: 'manual',
          payload: {
            input: { query: current.question.trim() },
            proposed_facts: [buildExpected(current) as { behavior: string; reference_answer: string; required_facts: string[] }],
            proposed_evidence: isRag ? buildEvidence(current, true) : [],
            metadata: { dataset_key: dataset.dataset_key, dataset_name: dataset.name, tags: ['manual'] },
          },
          idempotency_key: createIdempotencyKey(),
        })
        const reviewed = await evaluationService.reviewV2Candidate(candidate.candidate_id, {
          expected_revision: candidate.revision,
          verdict: 'approved',
          rationale: '版本编辑器新增用例',
          expected: buildExpected(current),
          evidence: buildEvidence(current, isRag),
        })
        items.push({ case_id: String(reviewed.promoted_case_id), revision: Number(reviewed.promoted_case_revision) })
      }
    }
    if (!items.length) throw new Error('没有可发布的用例（全部被删除）')
    await evaluationService.publishV2DatasetVersion(dataset.dataset_id, { version, items })
    setMessage(`已保存并发布新版本 v${version}（${items.length} 条）：历史版本与运行引用不变。`)
    onChanged()
    const refreshed = await evaluationService.listV2DatasetVersions(dataset.dataset_id)
    setVersions(refreshed)
    setDraftVersion(nextMinorVersion(refreshed[0]?.version))
  })

  return <Card
    title="编辑版本用例"
    subtitle={`已发布版本不可修改：保存时未改动的行原样带入，改动/新增的行走候选审核生成新修订，删除的行不带入新版本${isRag ? '' : '（非 RAG 数据集无证据锚点）'}。`}
    actions={<button type="button" className={ctl} onClick={onOpenHistory}>版本历史</button>}
  >
    {publishedVersions.length === 0
      ? <Empty text="还没有已发布版本：先在「待确认候选」中批准用例，再到「版本历史」发布第一个版本。" />
      : <>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select aria-label="选择要编辑的版本" value={versionId} onChange={e => setVersionId(e.target.value)} className={ctl}>
            {publishedVersions.map(item => <option key={item.dataset_version_id} value={item.dataset_version_id}>v{item.version}（{item.case_count ?? 0} 条）</option>)}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-text-secondary">新版本号
            <input aria-label="新版本号" value={draftVersion} onChange={e => setDraftVersion(e.target.value)} className={`${ctl} w-24`} placeholder="1.3.0" />
          </label>
          <span className="flex-1" />
          <button type="button" className={ctl} disabled={busy || loading} onClick={() => setRows(current => [...current, { case_id: '', revision: 0, question: '', reference: '', facts: '', behavior: 'answer', evidence: [], orig: '', deleted: false }])}><Plus size={13} className="mr-1 inline" aria-hidden="true" />添加用例</button>
          <button type="button" className={ctlPrimary} disabled={busy || loading || rows.length === 0} onClick={() => void save()}>保存（派生新版本）</button>
        </div>
        {message && <p role="status" className="mb-2 rounded-md bg-emerald-50 p-2.5 text-xs text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">{message}</p>}
        {error && <p role="alert" className="mb-2 rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}
        {loading ? <Empty text="正在读取版本用例…" /> : rows.length === 0 ? <Empty text="该版本没有用例。" /> : <TableWrap minWidth={980}>
          <thead className="bg-bg-elevated"><tr>
            <Th>用例</Th><Th>问题</Th><Th>参考答案</Th><Th>必需事实（逗号分隔）</Th><Th>证据（doc · 版本）</Th><Th>校验</Th><Th end>操作</Th>
          </tr></thead>
          <tbody className="divide-y divide-border-subtle">
            {rows.map((row, index) => {
              const changed = Boolean(row.case_id) && !row.deleted && rowSnapshot(dirtyRow(row)) !== row.orig
              return <tr key={`${row.case_id || 'new'}-${index}`} className={row.deleted ? 'opacity-40' : changed ? 'bg-amber-50/50 dark:bg-amber-950/20' : ''}>
                <Td className="whitespace-nowrap"><span className="font-mono text-[11px] text-text-muted">{row.case_id ? `${row.case_id.slice(0, 8)} · r${row.revision}` : '（新增）'}</span></Td>
                <Td><input aria-label="问题" value={row.question} onChange={e => patchRow(index, { question: e.target.value })} className={`${ctl} min-w-[200px]`} disabled={row.deleted} /></Td>
                <Td><input aria-label="参考答案" value={row.reference} onChange={e => patchRow(index, { reference: e.target.value })} className={`${ctl} min-w-[180px]`} disabled={row.deleted} /></Td>
                <Td><input aria-label="必需事实" value={row.facts} onChange={e => patchRow(index, { facts: e.target.value })} className={`${ctl} min-w-[140px]`} disabled={row.deleted} /></Td>
                <Td>{isRag
                  ? <span className="block max-w-[220px] truncate font-mono text-[11px] text-text-muted" title={row.evidence.map(item => `${item.doc_id || ''} · ${item.doc_version || ''}`).join('；')}>
                    {row.evidence.length ? row.evidence.map(item => `${item.doc_id || ''} · ${item.doc_version || ''}`).join('；') : '（无证据）'}
                  </span>
                  : <span className="text-[11px] text-text-muted">—</span>}</Td>
                <Td>{row.deleted
                  ? <Badge text="将删除" tone="gray" />
                  : row.behavior === 'refuse'
                    ? <Badge text={`拒答样本${row.reason ? ` · ${row.reason}` : ''}`} tone="warn" />
                    : !row.question.trim() || !row.reference.trim()
                      ? <Badge text={row.case_id ? '⚠ 缺问题/答案' : '⚠ 待填写'} tone="warn" />
                      : changed ? <Badge text="已修改" tone="warn" /> : <Badge text="✓ 原样保留" tone="good" />}</Td>
                <Td end>
                  {row.case_id
                    ? <button type="button" className={row.deleted ? ctl : ctlGhost} disabled={busy} onClick={() => patchRow(index, { deleted: !row.deleted })}>{row.deleted ? '恢复' : '删除'}</button>
                    : <button type="button" className={ctlGhost} disabled={busy} onClick={() => setRows(current => current.filter((_, i) => i !== index))}>移除</button>}
                </Td>
              </tr>
            })}
          </tbody>
        </TableWrap>}
        <p className="mt-2 text-xs text-text-muted">
          保存将创建版本 {draftVersion || '（未填写）'}：原样 {rows.length - changedRows.length - deletedCount - addedRows.length} 行 · 修改 {changedRows.length} 行 · 新增 {addedRows.length} 行 · 删除 {deletedCount} 行。修改与新增需通过服务端证据核验；证据已过期时会明确报错，不会静默通过。
        </p>
      </>}
  </Card>
}

function HistoryView({ dataset, onBack, perform, busy, error, onChanged }: {
  dataset: EvaluationV2Dataset
  onBack: () => void
  perform: (fn: () => Promise<void>) => Promise<void>
  busy: boolean
  error?: string
  onChanged: () => void
}) {
  const [versions, setVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [publishVersion, setPublishVersion] = useState('')
  const [message, setMessage] = useState('')
  const [diff, setDiff] = useState<{ base: string; target: string; added: number; removed: number; changed: number } | null>(null)
  useEffect(() => {
    evaluationService.listV2DatasetVersions(dataset.dataset_id).then(items => {
      setVersions(items)
      setPublishVersion(nextMinorVersion(items[0]?.version))
    }).catch(() => setVersions([]))
  }, [dataset.dataset_id])

  // 发布已批准用例：批准候选已带 promoted_case_id/revision，直接组装清单发布新版本
  const publishApproved = () => perform(async () => {
    const version = publishVersion.trim()
    if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error('新版本号必须是三段式（如 1.1.0）')
    const approved = await evaluationService.listV2Candidates('approved', 200, dataset.dataset_key)
    const mine = approved
      .filter(item => item.promoted_case_id)
      .map(item => ({
        case_id: String(item.promoted_case_id),
        revision: promotedCaseRevision(item),
      }))
      .filter((item): item is { case_id: string; revision: number } => item.revision !== null)
    if (!mine.length) throw new Error('没有已批准的用例：先在编辑视图中批准候选。')
    await evaluationService.publishV2DatasetVersion(dataset.dataset_id, { version, items: mine })
    setMessage(`已发布新版本 v${version}（${mine.length} 条）：此后不可修改，可用于正式评测。`)
    const refreshed = await evaluationService.listV2DatasetVersions(dataset.dataset_id)
    setVersions(refreshed)
    setPublishVersion(nextMinorVersion(refreshed[0]?.version))
    onChanged()
  })

  const rollback = (item: EvaluationV2DatasetVersion, version: string) => perform(async () => {
    const blob = await evaluationService.exportV2DatasetVersion(dataset.dataset_id, item.dataset_version_id)
    const lines = (await blob.text()).split('\n').filter(line => line.trim())
    const items = lines.slice(1)
      .map(line => JSON.parse(line) as { type?: string; case_id?: string; revision?: number })
      .filter(row => row.type === 'case' && row.case_id && row.revision)
      .map(row => ({ case_id: String(row.case_id), revision: Number(row.revision) }))
    if (!items.length) throw new Error('该版本没有可发布的用例')
    const result = await evaluationService.publishV2DatasetVersion(dataset.dataset_id, { version, items })
    setMessage(`已按 v${item.version} 内容发布新版本 v${result.version}（${items.length} 条）：历史与运行引用不变。`)
    const refreshed = await evaluationService.listV2DatasetVersions(dataset.dataset_id)
    setVersions(refreshed)
    setPublishVersion(nextMinorVersion(refreshed[0]?.version))
    onChanged()
  })

  // 版本对比：与相邻上一版本 diff（新增/移除/内容变化）
  const compareWithPrevious = (item: EvaluationV2DatasetVersion, index: number) => perform(async () => {
    const previous = versions[index + 1]
    if (!previous) throw new Error('没有更早的版本可对比。')
    const result = await evaluationService.compareV2DatasetVersions(dataset.dataset_id, previous.dataset_version_id, item.dataset_version_id)
    setDiff({ base: result.base_version, target: result.target_version, added: result.added.length, removed: result.removed.length, changed: result.changed.length })
  })

  const rollbackTarget = versions[0] ? nextMinorVersion(versions[0].version) : ''
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={ctl} onClick={onBack}>← 返回列表</button>
      <span className="text-sm font-medium text-text-primary">版本历史：{dataset.name}</span>
      <span className="flex-1" />
      <label className="flex items-center gap-1.5 text-xs text-text-secondary">新版本号
        <input aria-label="发布用的新版本号" value={publishVersion} onChange={e => setPublishVersion(e.target.value)} className={`${ctl} w-24`} placeholder="1.1.0" />
      </label>
      <button type="button" className={ctlPrimary} disabled={busy} onClick={() => void publishApproved()}>发布已批准用例</button>
    </div>
    {error && <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-2.5 text-xs text-red-700">{error}</p>}
    {message && <p role="status" className="rounded-md bg-emerald-50 p-2.5 text-xs text-emerald-700">{message}</p>}
    {diff && <Card title={`版本对比：v${diff.base} → v${diff.target}`} subtitle="与相邻上一版本的差异（按样本 ID、修订与内容哈希计算）。">
      <div className="flex gap-6 text-xs text-text-secondary">
        <span><b className="text-sm text-text-primary tabular-nums">{diff.added}</b> 新增</span>
        <span><b className="text-sm text-text-primary tabular-nums">{diff.changed}</b> 修改</span>
        <span><b className="text-sm text-text-primary tabular-nums">{diff.removed}</b> 删除</span>
      </div>
    </Card>}
    <Card title="版本时间线" subtitle="版本一经发布不可修改、不可删除；回退 = 以该版本内容发布新版本。">
      {versions.length === 0 ? <Empty text="尚无版本：先在编辑视图中批准候选，再用上方「发布已批准用例」创建第一个版本。" /> : <TableWrap minWidth={760}>
        <thead className="bg-bg-elevated"><tr><Th>版本</Th><Th>状态</Th><Th end>用例数</Th><Th>发布时间</Th><Th end>被运行引用</Th><Th end>操作</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {versions.map((item, index) => <tr key={item.dataset_version_id}>
            <Td>v{item.version}</Td>
            <Td><StatusBadge status={item.status} /></Td>
            <Td end>{item.case_count ?? 0}</Td>
            <Td>{timeShort(item.published_at)}</Td>
            <Td end>{item.run_refs ?? 0}</Td>
            <Td end>
              <button type="button" className={`${ctlGhost} mr-1`} onClick={() => void perform(async () => {
                const blob = await evaluationService.exportV2DatasetVersion(dataset.dataset_id, item.dataset_version_id)
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url; a.download = `${dataset.dataset_key}-v${item.version}.jsonl`
                document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
              })}><Download size={12} className="mr-1 inline" aria-hidden="true" />导出</button>
              {item.status === 'published' && index + 1 < versions.length && <button type="button" className={`${ctlGhost} mr-1`} disabled={busy} onClick={() => void compareWithPrevious(item, index)}>对比</button>}
              {item.status === 'published' && <button type="button" className={ctl} disabled={busy || !rollbackTarget} onClick={() => void rollback(item, rollbackTarget)}>以此版本回退</button>}
            </Td>
          </tr>)}
        </tbody>
      </TableWrap>}
      <p className="mt-2 text-xs text-text-muted">回退将按 v{rollbackTarget || '—'}（最新版本号 +1）发布新版本，可在顶部输入框修改；弃用版本不能发起新评测，但引用它的历史运行照常可查。</p>
    </Card>
  </div>
}
