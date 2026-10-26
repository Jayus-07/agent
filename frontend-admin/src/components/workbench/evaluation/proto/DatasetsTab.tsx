'use client'

/**
 * 数据集 tab —— 严格按已定稿原型实现：
 * 工具栏（搜索/模块筛选/生成/导入/新建）→ 六列表 → 生成面板 / 导入向导 / 编辑视图 / 版本历史。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Download, Pencil, Play, Plus, Search, Sparkles, Upload } from 'lucide-react'
import {
  evaluationService,
  type EvaluationV2Candidate,
  type EvaluationV2Dataset,
  type EvaluationV2DatasetVersion,
} from '@/api/evaluation'
import { evaluationAssets, type EvaluationTask, type ImportManifest } from '@/api/evaluation-assets'
import { createIdempotencyKey } from '@/api/client'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Empty, StatusBadge, TableWrap, Td, Th, timeShort } from './evalUi'
import EvidenceSearchPicker from './EvidenceSearchPicker'

type View = { kind: 'list' } | { kind: 'edit'; dataset: EvaluationV2Dataset } | { kind: 'history'; dataset: EvaluationV2Dataset }

const MODULE_FILTERS = ['all', 'rag', 'cs', 'sql', 'planner', 'travel'] as const
const MODULE_LABELS: Record<string, string> = { all: '全部模块', rag: 'RAG', cs: '客服', sql: 'SQL', planner: '路由', travel: '旅游' }

export default function DatasetsTab({ onGoRuns }: { onGoRuns: (datasetKey?: string) => void }) {
  const [datasets, setDatasets] = useState<EvaluationV2Dataset[]>([])
  const [candidates, setCandidates] = useState<EvaluationV2Candidate[]>([])
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [search, setSearch] = useState('')
  const [moduleFilter, setModuleFilter] = useState<string>('all')
  const [genOpen, setGenOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
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
        evaluationService.listV2Candidates('pending', 200).catch(() => [] as EvaluationV2Candidate[]),
        listKbAuthority().catch(() => ({ knowledge_bases: [] as KbAuthorityItem[] })),
      ])
      setDatasets(ds); setCandidates(pending)
      setKbs(kbCatalog.knowledge_bases.filter(item => item.active && !item.alias_for && !item.deprecated))
    } catch (e) { setError(e instanceof Error ? e.message : '读取数据集失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  const filtered = useMemo(() => datasets.filter(item =>
    (moduleFilter === 'all' || item.domain === moduleFilter)
    && (!search.trim() || item.name.toLowerCase().includes(search.trim().toLowerCase()) || item.dataset_key.toLowerCase().includes(search.trim().toLowerCase()))
  ), [datasets, moduleFilter, search])

  const perform = async (fn: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : '操作失败') }
    finally { setBusy(false) }
  }

  return <div className="space-y-4">
    {view.kind === 'list' && <>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 rounded-md border border-border-subtle bg-surface-base px-2.5">
          <Search size={14} className="text-text-muted" aria-hidden="true" />
          <input aria-label="搜索数据集名称" placeholder="搜索数据集名称" value={search} onChange={e => setSearch(e.target.value)} className="h-8 w-44 bg-transparent text-xs text-text-primary outline-none" />
        </label>
        <select aria-label="按模块筛选" value={moduleFilter} onChange={e => setModuleFilter(e.target.value)} className={ctl}>
          {MODULE_FILTERS.map(m => <option key={m} value={m}>{MODULE_LABELS[m]}</option>)}
        </select>
        <span className="flex-1" />
        <button type="button" className={ctl} onClick={() => setGenOpen(v => !v)}><Sparkles size={13} className="mr-1 inline" aria-hidden="true" />从知识库生成</button>
        <button type="button" className={ctl} onClick={() => setImportOpen(true)}><Upload size={13} className="mr-1 inline" aria-hidden="true" />导入数据集</button>
        <button type="button" className={ctlPrimary} onClick={() => setNewOpen(v => !v)}><Plus size={13} className="mr-1 inline" aria-hidden="true" />新建数据集</button>
      </div>

      {error && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{error}</p>}
      {message && <p role="status" className="text-xs text-text-secondary">{message}</p>}

      {genOpen && <GeneratePanel kbs={kbs} onDone={() => void load()} />}
      {newOpen && <NewDatasetForm onDone={() => { setNewOpen(false); void load() }} />}
      {importOpen && <ImportWizard onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); void load() }} />}

      <Card>
        {loading ? <Empty text="正在读取数据集…" /> : filtered.length === 0
          ? <Empty text={datasets.length ? '没有匹配的数据集。' : '还没有数据集：从知识库生成、导入或新建。'} />
          : <TableWrap minWidth={860}>
            <thead className="bg-bg-elevated"><tr><Th>数据集</Th><Th>模块</Th><Th end>用例数</Th><Th>当前版本</Th><Th>状态</Th><Th>更新时间</Th><Th end>操作</Th></tr></thead>
            <tbody className="divide-y divide-border-subtle">
              {filtered.map(item => <tr key={item.dataset_id}>
                <Td><span className="block font-medium text-text-primary">{item.name}</span><span className="block font-mono text-[11px] text-text-muted">{item.dataset_key}</span></Td>
                <Td>{MODULE_LABELS[item.domain] || item.domain}</Td>
                <Td end>{item.case_count ?? 0}</Td>
                <Td>{item.latest_version ? <>v{item.latest_version} {item.latest_status === 'draft' && <Badge text="草稿" tone="warn" />}</> : '—'}</Td>
                <Td><StatusBadge status={item.latest_status === 'draft' ? 'draft' : item.version_count ? 'published' : 'draft'} /></Td>
                <Td>{timeShort(item.updated_at)}</Td>
                <Td end>
                  <button type="button" className={`${ctlGhost} mr-1`} onClick={() => setView({ kind: 'edit', dataset: item })}><Pencil size={12} className="mr-1 inline" aria-hidden="true" />编辑</button>
                  <button type="button" className={ctl} onClick={() => onGoRuns(item.dataset_key)}><Play size={12} className="mr-1 inline" aria-hidden="true" />发起评测</button>
                </Td>
              </tr>)}
            </tbody>
          </TableWrap>}
        <p className="mt-2.5 text-xs text-text-muted">草稿可直接编辑；已发布版本被历史运行锁定，编辑将派生新草稿版本，保存即版本号 +1。</p>
      </Card>
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
  return <Card title="新建数据集" subtitle="创建后通过导入 / 生成写入草稿候选，审核批准后发布版本。">
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

function GeneratePanel({ kbs, onDone }: { kbs: KbAuthorityItem[]; onDone: () => void }) {
  const [kbId, setKbId] = useState('')
  const [count, setCount] = useState(5)
  const [task, setTask] = useState<EvaluationTask | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const datasetKey = `synthetic_${Date.now()}`
  const submit = () => { setBusy(true); setError('')
    evaluationAssets.synthesize(
      kbId,
      { dataset_key: datasetKey, name: `从知识库生成 ${new Date().toLocaleString('zh-CN')}`, module: 'rag', schema_version: 'rag-eval/1.0' } as unknown as ImportManifest,
      count, createIdempotencyKey(),
    ).then(setTask).catch((e: unknown) => setError(e instanceof Error ? e.message : '提交生成任务失败')).finally(() => setBusy(false))
  }
  return <Card title="从知识库生成评测题" subtitle="基于知识库语料（自动冻结快照）单跳合成：问题、参考答案与证据引用自动带出，产出直接进入候选，确认后发布。">
    <div className="grid gap-3 sm:grid-cols-[2fr_1fr_auto]">
      <select aria-label="知识库" value={kbId} onChange={e => setKbId(e.target.value)} className={ctl}>
        <option value="">选择知识库</option>
        {kbs.map(item => <option key={item.kb_id} value={item.kb_id}>{item.name} · {item.kb_id}</option>)}
      </select>
      <input aria-label="生成题数" type="number" min={1} max={5} value={count} onChange={e => setCount(Number(e.target.value) || 5)} className={ctl} />
      <button type="button" className={ctlPrimary} disabled={busy || !kbId} onClick={submit}>{busy ? '提交中…' : '开始生成'}</button>
    </div>
    <p className="mt-2 text-xs text-text-muted">每批 1–5 题（保留预算余量）；需扩量可连续提交多批。生成依赖知识库语料与评测模型配置。</p>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    {task && <p role="status" className="mt-2 text-xs text-text-secondary">任务 {task.task_id}：{task.status} {task.progress || ''} — 完成后候选出现在下方编辑视图，批准后即可发布。</p>}
    {task && task.status === 'SUCCESS' && <button type="button" className={`${ctlPrimary} mt-2`} onClick={onDone}>刷新数据集与候选</button>}
  </Card>
}

function ImportWizard({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<1 | 2 | 3>(1)
  const [domain, setDomain] = useState('rag')
  const [datasetId, setDatasetId] = useState('')
  const [datasets, setDatasets] = useState<EvaluationV2Dataset[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<{ valid: boolean; case_count: number; errors: Array<{ line: number; field: string; message: string }>; candidate_count?: number } | null>(null)
  const [skipErrors, setSkipErrors] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [imported, setImported] = useState<{ count: number } | null>(null)
  useEffect(() => { evaluationService.listV2Datasets(100).then(setDatasets).catch(() => setDatasets([])) }, [])
  const inspect = () => { if (!file || !datasetId) return; setBusy(true); setError('')
    evaluationService.previewV2Import(file, { schema_version: domain === 'rag' ? 'rag-eval/1.0' : 'evaluation-v2/1.0', dataset_key: datasets.find(d => d.dataset_id === datasetId)?.dataset_key || '', module: domain, name: datasets.find(d => d.dataset_id === datasetId)?.name || 'imported', version: '1.0.0' } as unknown as ImportManifest)
      .then(setPreview).catch((e: unknown) => setError(e instanceof Error ? e.message : '预检失败')).finally(() => setBusy(false))
  }
  const doImport = () => { if (!file || !datasetId || !preview?.valid) return; setBusy(true); setError('')
    evaluationService.importV2Candidates(file, { schema_version: domain === 'rag' ? 'rag-eval/1.0' : 'evaluation-v2/1.0', dataset_key: datasets.find(d => d.dataset_id === datasetId)?.dataset_key || '', module: domain, name: datasets.find(d => d.dataset_id === datasetId)?.name || 'imported', version: '1.0.0' } as unknown as ImportManifest, createIdempotencyKey())
      .then(result => { setImported({ count: result.candidate_count }); setStep(3); onDone() })
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
            <span><b className="block text-sm text-text-primary tabular-nums">{preview.valid ? preview.case_count : preview.case_count - preview.errors.length}</b>合法</span>
            <span><b className="block text-sm text-text-primary tabular-nums">{preview.errors.length}</b>错误</span>
          </div>
          {preview.errors.length > 0 && <div className="max-h-40 overflow-auto rounded-md border border-border-subtle">
            <table className="w-full text-left text-xs"><thead className="bg-bg-elevated text-text-muted"><tr><th className="px-2 py-1.5">行号</th><th className="px-2 py-1.5">字段</th><th className="px-2 py-1.5">问题</th></tr></thead>
              <tbody className="divide-y divide-border-subtle">{preview.errors.slice(0, 12).map((item, i) => <tr key={i}><td className="px-2 py-1.5 tabular-nums">{item.line}</td><td className="px-2 py-1.5">{item.field}</td><td className="px-2 py-1.5 text-red-700">{item.message}</td></tr>)}</tbody></table>
          </div>}
          {preview.errors.length > 0 && <label className="flex items-center gap-2 text-xs text-text-secondary"><input type="checkbox" checked={skipErrors} onChange={e => setSkipErrors(e.target.checked)} />跳过错误行，仅导入合法用例</label>}
          {!preview.valid && !skipErrors && <p className="text-xs text-red-700">存在错误行，导入将被拒绝。</p>}
        </>}
        {step === 3 && imported && <p className="text-xs text-text-secondary">已写入 <b className="tabular-nums">{imported.count}</b> 条候选：在编辑视图中补齐真值并批准后，发布为新版本。发布前不影响任何正式评测。</p>}
        {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
      </div>
      <div className="flex justify-end gap-2 border-t border-border-subtle px-4 py-3">
        <button type="button" className={ctlGhost} onClick={onClose}>取消</button>
        {step === 1 && <button type="button" className={ctlPrimary} disabled={!file || !datasetId || busy} onClick={() => { inspect(); setStep(2) }}>{busy ? '处理中…' : '下一步：校验预览'}</button>}
        {step === 2 && <>
          <button type="button" className={ctl} onClick={() => setStep(1)}>上一步</button>
          <button type="button" className={ctlPrimary} disabled={busy || !preview?.valid} onClick={doImport}>确认导入</button>
        </>}
        {step === 3 && <button type="button" className={ctlPrimary} onClick={onDone}>完成</button>}
      </div>
    </div>
  </div>
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
  const mine = candidates.filter(item => {
    const meta = (item.payload_json as Record<string, Record<string, string>> | null)?.metadata || {}
    return (meta.dataset_key || '') === dataset.dataset_key
  })
  const [edits, setEdits] = useState<Record<string, { question: string; reference: string; facts: string; snapshotId: string }>>({})
  const getEdit = (id: string, candidate: EvaluationV2Candidate) => edits[id] || {
    question: String((candidate.payload_json as Record<string, Record<string, string>> | null)?.input?.query || ''),
    reference: String((candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_facts?.[0]?.reference_answer || ''),
    facts: ((candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_facts?.[0]?.required_facts as string[] | undefined || []).join(', '),
    snapshotId: String((candidate.payload_json as Record<string, Record<string, string>> | null)?.metadata?.snapshot_id || ''),
  }
  const setEdit = (id: string, patch: Partial<{ question: string; reference: string; facts: string; snapshotId: string }>) =>
    setEdits(current => ({ ...current, [id]: { ...getEdit(id, candidates.find(c => c.candidate_id === id)!), ...patch } }))
  const approve = (candidate: EvaluationV2Candidate) => perform(async () => {
    const edit = getEdit(candidate.candidate_id, candidate)
    const facts = edit.facts.split(/[,，]/).map(item => item.trim()).filter(Boolean)
    const payloadEvidence = (candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_evidence || []
    const evidence = JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || JSON.stringify(payloadEvidence)) as Array<Record<string, unknown>>
    await evaluationService.reviewV2Candidate(candidate.candidate_id, {
      expected_revision: candidate.revision,
      verdict: 'approved',
      rationale: '编辑器确认：补齐真值与证据后批准',
      expected: {
        behavior: 'answer',
        reference_answer: edit.reference,
        required_facts: facts,
      },
      evidence: evidence.map(item => ({
        kb_id: String(item.kb_id || ''),
        doc_id: String(item.doc_id || ''),
        doc_version: String(item.doc_version || ''),
        quote: String(item.quote || ''),
      })),
    })
    onChanged()
  })
  const discard = (candidate: EvaluationV2Candidate) => perform(async () => {
    await evaluationService.reviewV2Candidate(candidate.candidate_id, {
      expected_revision: candidate.revision,
      verdict: 'rejected',
      rationale: '编辑器中丢弃',
    })
    onChanged()
  })
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={ctl} onClick={onBack}>← 返回列表</button>
      <span className="text-sm font-medium text-text-primary">编辑：{dataset.name} · {dataset.latest_version ? `v${dataset.latest_version}` : '尚无版本'}</span>
      <span className="flex-1" />
      <button type="button" className={ctl} onClick={onOpenHistory}>版本历史</button>
    </div>
    <Card title="待确认候选" subtitle="与导入模板同 schema：问题 / 参考答案 / 必需事实 / 证据引用；批准后可发布版本。">
      {mine.length === 0 ? <Empty text="该数据集暂无待确认候选：通过「从知识库生成」或「导入数据集」写入。" />
        : <div className="space-y-3">
          {mine.map(candidate => {
            const edit = getEdit(candidate.candidate_id, candidate)
            const complete = edit.reference.trim() !== ''
            return <div key={candidate.candidate_id} className="rounded-md border border-border-subtle p-3">
              <div className="grid gap-2 sm:grid-cols-2">
                <input aria-label="问题" placeholder="问题" value={edit.question} onChange={e => setEdit(candidate.candidate_id, { question: e.target.value })} className={ctl} />
                <input aria-label="参考答案" placeholder="参考答案 reference_answer" value={edit.reference} onChange={e => setEdit(candidate.candidate_id, { reference: e.target.value })} className={ctl} />
                <input aria-label="必需事实" placeholder="必需事实（逗号分隔）" value={edit.facts} onChange={e => setEdit(candidate.candidate_id, { facts: e.target.value })} className={ctl} />
                <div className="flex items-center gap-2">
                  <Badge text={complete ? '✓ 可批准' : '⚠ 缺参考答案'} tone={complete ? 'good' : 'warn'} />
                  <span className="font-mono text-[11px] text-text-muted">{candidate.candidate_id.slice(0, 8)}</span>
                </div>
              </div>
              <div className="mt-2">
                <EvidenceSearchPicker onInsert={(claim: { kb_id: string; doc_id: string; doc_version: string; quote: string }) => {
                  const payloadEvidence2 = (candidate.payload_json as Record<string, Array<Record<string, unknown>>> | null)?.proposed_evidence || []
                  const current = JSON.parse(sessionStorage.getItem(`evidence:${candidate.candidate_id}`) || JSON.stringify(payloadEvidence2)) as Array<Record<string, unknown>>
                  sessionStorage.setItem(`evidence:${candidate.candidate_id}`, JSON.stringify([...current, claim]))
                  setEdit(candidate.candidate_id, {})
                }} />
              </div>
              <div className="mt-2 flex justify-end gap-2">
                <button type="button" className={ctlGhost} disabled={busy} onClick={() => discard(candidate)}>丢弃</button>
                <button type="button" className={ctlPrimary} disabled={busy || !complete} onClick={() => approve(candidate)}>批准进草稿</button>
              </div>
            </div>
          })}
        </div>}
      <p className="mt-2 text-xs text-text-muted">批准 = 完成八道服务端校验（证据锚点逐字核验、四眼原则等）并生成不可变用例修订；随后在「版本历史」中发布新版本。</p>
    </Card>
  </div>
}

function HistoryView({ dataset, onBack, perform, busy, onChanged }: {
  dataset: EvaluationV2Dataset
  onBack: () => void
  perform: (fn: () => Promise<void>) => Promise<void>
  busy: boolean
  onChanged: () => void
}) {
  const [versions, setVersions] = useState<EvaluationV2DatasetVersion[]>([])
  const [rollbackVersion, setRollbackVersion] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => {
    evaluationService.listV2DatasetVersions(dataset.dataset_id).then(setVersions).catch(() => setVersions([]))
  }, [dataset.dataset_id])
  const rollback = (item: EvaluationV2DatasetVersion) => perform(async () => {
    const target = rollbackVersion.trim()
    if (!target) return
    const blob = await evaluationService.exportV2DatasetVersion(dataset.dataset_id, item.dataset_version_id)
    const lines = (await blob.text()).split('\n').filter(line => line.trim())
    const items = lines.slice(1)
      .map(line => JSON.parse(line) as { type?: string; case_id?: string; revision?: number })
      .filter(row => row.type === 'case' && row.case_id && row.revision)
      .map(row => ({ case_id: String(row.case_id), revision: Number(row.revision) }))
    if (!items.length) throw new Error('该版本没有可发布的用例')
    const result = await evaluationService.publishV2DatasetVersion(dataset.dataset_id, { version: target, items })
    setMessage(`已按 v${item.version} 内容发布新版本 v${result.version}（${items.length} 条）：历史与运行引用不变。`)
    const refreshed = await evaluationService.listV2DatasetVersions(dataset.dataset_id)
    setVersions(refreshed)
    onChanged()
  })
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className={ctl} onClick={onBack}>← 返回列表</button>
      <span className="text-sm font-medium text-text-primary">版本历史：{dataset.name}</span>
    </div>
    {message && <p role="status" className="rounded-md bg-emerald-50 p-2.5 text-xs text-emerald-700">{message}</p>}
    <Card title="版本时间线" subtitle="版本一经发布不可修改、不可删除；回退 = 以该版本内容发布新版本。">
      <label className="mb-3 flex items-center gap-2 text-xs text-text-secondary">回退后的新版本号
        <input aria-label="回退后的新版本号" value={rollbackVersion} onChange={e => setRollbackVersion(e.target.value)} placeholder="如 v9" className={ctl} />
      </label>
      {versions.length === 0 ? <Empty text="尚无已发布版本。" /> : <TableWrap minWidth={760}>
        <thead className="bg-bg-elevated"><tr><Th>版本</Th><Th>状态</Th><Th end>用例数</Th><Th>发布时间</Th><Th end>操作</Th></tr></thead>
        <tbody className="divide-y divide-border-subtle">
          {versions.map(item => <tr key={item.dataset_version_id}>
            <Td>v{item.version}</Td>
            <Td><StatusBadge status={item.status} /></Td>
            <Td end>{item.case_count ?? 0}</Td>
            <Td>{timeShort(item.published_at)}</Td>
            <Td end>
              <button type="button" className={`${ctlGhost} mr-1`} onClick={() => void perform(async () => {
                const blob = await evaluationService.exportV2DatasetVersion(dataset.dataset_id, item.dataset_version_id)
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url; a.download = `${dataset.dataset_key}-v${item.version}.jsonl`
                document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
              })}><Download size={12} className="mr-1 inline" aria-hidden="true" />导出</button>
              {item.status === 'published' && <button type="button" className={ctl} disabled={busy || !rollbackVersion.trim()} onClick={() => rollback(item)}>以此版本回退</button>}
            </Td>
          </tr>)}
        </tbody>
      </TableWrap>}
      <p className="mt-2 text-xs text-text-muted">弃用版本不能发起新评测，但引用它的历史运行照常可查。</p>
    </Card>
  </div>
}
