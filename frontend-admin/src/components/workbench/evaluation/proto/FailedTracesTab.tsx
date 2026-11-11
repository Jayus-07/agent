'use client'

import { useDeferredValue, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ExternalLink, Search } from 'lucide-react'
import { listTracesPage } from '@/api/observability'
import type { TraceRecord } from '@/types/trace'
import { errMessage } from '@/api/client'
import { evaluationService, type EvaluationV2Dataset } from '@/api/evaluation'
import { atLeast } from '@/lib/auth'
import { Badge, Card, ctl, ctlGhost, ctlPrimary, Drawer, Empty, Pagination, TableWrap, Td, Th, timeShort } from './evalUi'

const TRACE_PAGE_SIZE = 10
const TRACE_STATUS_OPTIONS = [
  { value: 'error', label: '执行错误' },
  { value: 'failed', label: '业务失败' },
  { value: 'timeout', label: '超时' },
  { value: 'rejected', label: '安全拒绝' },
  { value: 'degraded', label: '降级' },
  { value: 'partial', label: '部分成功' },
] as const
const SOURCE_OPTIONS = [
  { value: 'ai_assistant', label: '企业助手' },
  { value: 'cs', label: '客服工作台' },
  { value: 'travel', label: '旅游服务' },
] as const
const SOURCE_LABELS: Record<string, string> = Object.fromEntries(SOURCE_OPTIONS.map(item => [item.value, item.label]))
const STATUS_LABELS: Record<string, string> = Object.fromEntries(TRACE_STATUS_OPTIONS.map(item => [item.value, item.label]))

function traceErrorText(trace: TraceRecord): string {
  const error = trace.error
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const item = error as Record<string, unknown>
    const message = String(item.message || item.detail || item.code || '')
    if (message) return message
  }
  const failed = trace.spans?.find(span => ['error', 'timeout', 'rejected'].includes(span.status))
  return failed?.errors?.[0] || failed?.name || ''
}

function isIndexingTrace(trace: TraceRecord): boolean {
  return trace.workflow_kind === 'knowledge_index'
    || trace.workflow_name === 'knowledge_index'
    || trace.metadata?.workflow_kind === 'knowledge_index'
}

function stableTraceCandidateKey(traceId: string, datasetId: string): string {
  const safe = `${traceId}-${datasetId}`.replace(/[^A-Za-z0-9_-]/g, '-')
  return `trace-${safe}`.slice(0, 128)
}

export default function FailedTracesTab() {
  const allowed = atLeast('editor')
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<(typeof TRACE_STATUS_OPTIONS)[number]['value']>('error')
  const [source, setSource] = useState('')
  const [keyword, setKeyword] = useState('')
  const deferredKeyword = useDeferredValue(keyword.trim())
  const [page, setPage] = useState(0)
  const [selectedTrace, setSelectedTrace] = useState<TraceRecord | null>(null)
  const [datasetId, setDatasetId] = useState('')
  const [saveError, setSaveError] = useState('')
  const [saveMessage, setSaveMessage] = useState('')

  const tracesQuery = useQuery({
    queryKey: ['admin', 'evaluation-failed-traces', { status, source, query: deferredKeyword, page }],
    queryFn: () => listTracesPage({
      limit: TRACE_PAGE_SIZE,
      offset: page * TRACE_PAGE_SIZE,
      status,
      source: source || undefined,
      query: deferredKeyword || undefined,
      excludeRunning: true,
    }),
    enabled: allowed,
  })
  const datasetsQuery = useQuery({
    queryKey: ['admin', 'evaluation-v2-datasets'],
    queryFn: () => evaluationService.listV2Datasets(100),
    enabled: allowed,
  })
  // 当前 Trace 回流编辑链路只支持 RAG 的「回答 + 证据」核验契约。
  const datasets: EvaluationV2Dataset[] = (datasetsQuery.data || []).filter(item => item.domain === 'rag')
  const traces = tracesQuery.data?.traces || []
  const total = tracesQuery.data?.total || 0
  const selectedDataset = datasets.find(item => item.dataset_id === datasetId)
  const selectedTraceCanCreate = Boolean(selectedTrace && selectedTrace.question.trim() && !isIndexingTrace(selectedTrace) && selectedDataset)

  useEffect(() => { setPage(0) }, [status, source, deferredKeyword])
  useEffect(() => {
    if (!selectedTrace || !datasets.length) return
    setDatasetId(current => datasets.some(item => item.dataset_id === current) ? current : datasets[0].dataset_id)
  }, [selectedTrace, datasets])

  const createCandidate = async () => {
    if (!selectedTrace || !selectedDataset) return
    setSaveError('')
    setSaveMessage('')
    try {
      const traceId = selectedTrace.id
      const module = selectedDataset.domain
      const candidate = await evaluationService.createV2Candidate({
        source_kind: 'manual',
        payload: {
          input: { query: selectedTrace.question.trim() },
          observed_answer: selectedTrace.answer_preview || '',
          proposed_facts: [],
          proposed_evidence: [],
          metadata: {
            source: 'production_trace',
            trace_status: selectedTrace.status || status,
            trace_error: traceErrorText(selectedTrace).slice(0, 1000),
            ground_truth_verified: false,
            dataset_key: selectedDataset.dataset_key,
            dataset_name: selectedDataset.name,
            module,
          },
          source_ref: {
            trace_id: traceId,
            request_id: selectedTrace.request_id,
            workflow_name: selectedTrace.workflow_name || '',
            dataset_key: selectedDataset.dataset_key,
            module,
          },
        },
        idempotency_key: stableTraceCandidateKey(traceId, selectedDataset.dataset_id),
      })
      setSaveMessage(`已创建待核验候选 ${candidate.candidate_id.slice(0, 8)}。实际回答来自 Trace，不是标准答案；请在数据集页补充并核验证据。`)
      await queryClient.invalidateQueries({ queryKey: ['admin', 'evaluation-v2-candidates'] })
      setSelectedTrace(null)
    } catch (cause) {
      setSaveError(errMessage(cause))
    }
  }

  if (!allowed) return <p className="p-5 text-sm text-text-secondary">失败 Trace 收录沿用候选创建权限，需要编辑者及以上角色。</p>

  return <div className="space-y-4">
    <div className="rounded-lg border border-amber-200 bg-amber-50/70 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
      <div className="flex items-start gap-2"><AlertTriangle size={15} className="mt-0.5 shrink-0" aria-hidden="true" /><p>这里只显示后端 Trace 中已记录的异常状态。当前没有人工标记回答质量问题或前端 JS 异常的采集入口。收录只创建待审核候选；Trace 实际回答不能当作标准答案。知识索引内部 Trace 只供查看；此回流表单目前只支持 RAG 的答案与证据核验。</p></div>
    </div>

    {saveMessage && <p role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-2.5 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">{saveMessage} <a className="ml-1 underline underline-offset-2" href="/evaluations/center?tab=datasets">去数据集页核验</a></p>}
    {saveError && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{saveError}</p>}

    <Card title="失败 Trace" subtitle="筛选真实运行记录，先核对上下文，再收录为待审核候选。">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="flex min-w-48 flex-1 items-center gap-1.5 rounded-md border border-border-subtle bg-surface-base px-2.5 sm:max-w-sm">
          <Search size={14} className="text-text-muted" aria-hidden="true" />
          <input aria-label="搜索 Trace、请求编号或工作流" placeholder="搜索 Trace、请求编号或工作流" value={keyword} onChange={event => setKeyword(event.target.value)} className="h-8 w-full bg-transparent text-xs text-text-primary outline-none" />
        </label>
        <label className="flex items-center gap-2 text-xs text-text-secondary">异常类型
          <select aria-label="异常类型筛选" value={status} onChange={event => setStatus(event.target.value as typeof status)} className={ctl}>
            {TRACE_STATUS_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-text-secondary">来源
          <select aria-label="Trace 来源筛选" value={source} onChange={event => setSource(event.target.value)} className={ctl}>
            <option value="">全部前端</option>
            {SOURCE_OPTIONS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        <button type="button" className={ctlGhost} disabled={tracesQuery.isFetching} onClick={() => void tracesQuery.refetch()}>刷新</button>
      </div>

      {tracesQuery.isError ? <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-red-200 p-3 text-xs text-red-700"><span role="alert">{errMessage(tracesQuery.error)}</span><button type="button" className={ctlGhost} onClick={() => void tracesQuery.refetch()}>重试</button></div>
        : tracesQuery.isPending ? <Empty text="正在读取 Trace…" />
          : traces.length === 0 ? <Empty text="当前筛选条件下没有可回流的 Trace。" />
            : <TableWrap minWidth={860}>
              <thead className="bg-bg-elevated"><tr><Th>时间 / 来源</Th><Th>Trace / 工作流</Th><Th>用户问题</Th><Th>失败信息</Th><Th>状态</Th><Th end>操作</Th></tr></thead>
              <tbody className="divide-y divide-border-subtle">
                {traces.map(trace => <tr key={trace.id}>
                  <Td><span className="block whitespace-nowrap">{timeShort(trace.timestamp)}</span><span className="text-[11px] text-text-muted">{SOURCE_LABELS[trace.source || ''] || trace.source || '未分类'}</span></Td>
                  <Td><span className="block max-w-40 truncate font-mono text-[11px] text-text-primary" title={trace.id}>{trace.id}</span><span className="block max-w-40 truncate text-[11px] text-text-muted">{trace.workflow_name || '未知工作流'}</span></Td>
                  <Td className="max-w-[260px]"><span className="line-clamp-2 text-text-primary">{trace.question || 'Trace 未记录用户问题'}</span></Td>
                  <Td className="max-w-[220px]"><span className="line-clamp-2">{traceErrorText(trace) || 'Trace 有异常状态；详情中可查看错误 Span。'}</span></Td>
                  <Td><Badge text={STATUS_LABELS[trace.status || ''] || trace.status || '未知'} tone={trace.status === 'error' || trace.status === 'failed' || trace.status === 'timeout' ? 'bad' : 'warn'} /></Td>
                  <Td end><div className="flex justify-end gap-1.5"><button type="button" className={ctlGhost} onClick={() => { setSaveError(''); setSaveMessage(''); setSelectedTrace(trace) }}>{isIndexingTrace(trace) ? '详情' : '查看 / 收录'}</button></div></Td>
                </tr>)}
              </tbody>
            </TableWrap>}
      {!tracesQuery.isError && <Pagination page={page} pageSize={TRACE_PAGE_SIZE} total={total} onPageChange={setPage} />}
      <p className="mt-2 text-[11px] text-text-muted">数据来自授权后的服务端 Trace 列表；分页与筛选由服务端执行。前端代码异常需先接入独立上报，不能在这里伪装成模型回答失败。</p>
    </Card>

    <Drawer open={Boolean(selectedTrace)} title="核对失败 Trace" subtitle="收录仅建立待审核候选；系统不把实际回答冒充为标准答案。" onClose={() => setSelectedTrace(null)} width="max-w-3xl"
      footer={<div className="flex flex-wrap items-center justify-between gap-2"><p className="text-[11px] text-text-muted">提交后可在目标数据集的待审核候选中补齐标准答案与证据。</p><div className="flex gap-2"><button type="button" className={ctl} onClick={() => setSelectedTrace(null)}>关闭</button>{selectedTrace && !isIndexingTrace(selectedTrace) && <button type="button" className={ctlPrimary} disabled={!selectedTraceCanCreate || datasetsQuery.isPending || saveMessage.length > 0} onClick={() => void createCandidate()}>创建待核验候选</button>}</div></div>}>
      {selectedTrace && <div className="space-y-4">
        {saveError && <p role="alert" className="rounded-md border border-red-200 p-2.5 text-xs text-red-700">{saveError}</p>}
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-border-subtle p-3"><p className="text-[11px] text-text-muted">Trace ID</p><p className="mt-1 break-all font-mono text-xs text-text-primary">{selectedTrace.id}</p></div>
          <div className="rounded-md border border-border-subtle p-3"><p className="text-[11px] text-text-muted">来源 / 状态</p><p className="mt-1 text-xs text-text-primary">{SOURCE_LABELS[selectedTrace.source || ''] || selectedTrace.source || '未分类'} · {STATUS_LABELS[selectedTrace.status || ''] || selectedTrace.status || '未知'}</p></div>
          <div className="rounded-md border border-border-subtle p-3"><p className="text-[11px] text-text-muted">工作流 / 耗时</p><p className="mt-1 break-words text-xs text-text-primary">{selectedTrace.workflow_name || '未知工作流'} · {selectedTrace.duration_ms} ms</p></div>
          <div className="rounded-md border border-border-subtle p-3"><p className="text-[11px] text-text-muted">异常信息</p><p className="mt-1 break-words text-xs text-text-primary">{traceErrorText(selectedTrace) || 'Trace 状态异常，具体信息请查看 Span 明细。'}</p></div>
        </div>
        <section className="rounded-md border border-border-subtle p-3"><h3 className="text-xs font-semibold text-text-primary">用户问题</h3><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-text-secondary">{selectedTrace.question || 'Trace 未记录用户问题，无法创建评测候选。'}</p></section>
        <section className="rounded-md border border-border-subtle p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-semibold text-text-primary">Trace 实际回答</h3><Badge text="不能作为标准答案" tone="warn" /></div><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-text-secondary">{selectedTrace.answer_preview || '未记录回答内容'}</p></section>
        {isIndexingTrace(selectedTrace) ? <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">这是知识索引内部 Trace，不是用户问答记录，不能转成答案质量样本。可打开完整 Trace 查看索引失败的 Span。</div> : <label className="block text-xs font-medium text-text-primary">目标 RAG 数据集
          <select aria-label="目标评测数据集" value={datasetId} onChange={event => setDatasetId(event.target.value)} className={`${ctl} mt-1.5 w-full`} disabled={datasetsQuery.isPending || datasets.length === 0}>
            {datasets.length === 0 && <option value="">暂无可用数据集</option>}
            {datasets.map(item => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.dataset_key} · {item.domain}</option>)}
          </select>
        </label>}
        {!isIndexingTrace(selectedTrace) && <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <p className="font-medium">提交后的审核要求</p>
          <p className="mt-1">问题和实际回答会保留为回流线索。审核人仍需填写正确答案、补充该目标数据集可访问的证据，并通过后端核验后才能发布新版本。客服、旅游与 SQL 的 Trace 需要各自的结构化 Golden 表单，当前不开放收录到这些数据集。</p>
        </div>}
        <a className="inline-flex items-center text-xs text-accent hover:underline" href={`/observability/traces/${encodeURIComponent(selectedTrace.id)}`} target="_blank" rel="noreferrer">打开完整 Trace <ExternalLink size={12} className="ml-1" aria-hidden="true" /></a>
        {!selectedTrace.question.trim() && <p role="status" className="text-xs text-amber-700">该 Trace 没有可用问题，不能创建候选。</p>}
        {datasetsQuery.isError && <p role="alert" className="text-xs text-red-700">读取目标数据集失败：{errMessage(datasetsQuery.error)}</p>}
      </div>}
    </Drawer>
  </div>
}
