'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { evaluationAssets, type EvaluationTask } from '@/api/evaluation-assets'
import { createIdempotencyKey } from '@/api/client'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { EvalButton, EvalCard, EvalEmpty, EvalField, EvalInfoBox, EvalWarnBox, evalControl } from './evalUi'

const taskLabels: Record<string, string> = { PENDING: '排队中', RUNNING: '生成中', RETRYING: '等待自动重试', SUCCESS: '已完成', FAILED: '执行失败', CANCELLED: '已取消', PAUSED: '已暂停' }
export default function DatasetSynthesisPanel({ expanded = false, onGoReview }: { expanded?: boolean; onGoReview?: () => void }) {
  const [kb, setKb] = useState('')
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [isOpen, setIsOpen] = useState(expanded)
  const loadingKbs = useRef(false)
  const [cancelRequested, setCancelRequested] = useState(false)
  const [version, setVersion] = useState('1.0.0')
  const [count, setCount] = useState(5)
  const [task, setTask] = useState<EvaluationTask | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const loadKnowledgeBases = useCallback(async () => {
    if (loaded || loadingKbs.current) return
    loadingKbs.current = true
    try {
      const result = await listKbAuthority()
      setKbs(result.knowledge_bases.filter(k => k.active && !k.alias_for))
      setLoaded(true)
    } catch (cause) { setError(cause instanceof Error ? cause.message : '读取知识库失败') }
    finally { loadingKbs.current = false }
  }, [loaded])
  useEffect(() => { if (expanded) { setIsOpen(true); void loadKnowledgeBases() } }, [expanded, loadKnowledgeBases])

  useEffect(() => {
    const id = localStorage.getItem('evaluation-synthesis-task')
    if (id) void evaluationAssets.task(id).then(setTask).catch(() => setError('读取合成任务失败，请刷新重试'))
  }, [])
  useEffect(() => {
    if (!task || ['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status)) return
    let active = true
    const timer = setInterval(() => void evaluationAssets.task(task.task_id).then(next => { if (active) setTask(next) }).catch(e => { if (active) setError(String(e)) }), 2000)
    return () => { active = false; clearInterval(timer) }
  }, [task])
  const submit = () => void (async () => {
    setBusy(true); setError('')
    try {
      const saved = localStorage.getItem('evaluation-synthesis-request')
      const request = saved ? JSON.parse(saved) : { ...(await evaluationAssets.freeze(kb)), request_id: createIdempotencyKey(), count, manifest: { schema_version: 'rag-eval/1.0', dataset_key: key || `synthetic_${Date.now()}`, name, module: 'rag', version } }
      localStorage.setItem('evaluation-synthesis-request', JSON.stringify(request))
      const next = await evaluationAssets.synthesize(request.snapshot_id, request.manifest, request.count, request.request_id)
      localStorage.removeItem('evaluation-synthesis-request'); localStorage.setItem('evaluation-synthesis-task', next.task_id); setCancelRequested(false); setTask(next)
    } catch (e) { setError(e instanceof Error ? e.message : '合成任务提交失败') } finally { setBusy(false) }
  })()
  return <div className="grid gap-4 xl:grid-cols-2">
    <EvalCard title="生成任务配置" subtitle="先规划覆盖度，再由审核人员验证候选；不自动发布正式版本。">
      <p className="mb-4 text-xs leading-5 text-text-muted">使用已配置评测模型从知识文档合成候选题，会产生模型用量；每批最多 5 题，结果须人工审核。</p>
      <div className="grid gap-4 sm:grid-cols-2">
        <EvalField label="来源知识库"><select aria-label="合成来源知识库" className={evalControl + ' w-full'} value={kb} onChange={e => setKb(e.target.value)}><option value="">请选择知识库</option>{kbs.map(k => <option key={k.kb_id} value={k.kb_id}>{k.name}</option>)}</select></EvalField>
        <EvalField label="新评测集名称"><input aria-label="合成评测集名称" className={evalControl + ' w-full'} value={name} onChange={e => setName(e.target.value)} placeholder="如：员工手册候选题" /></EvalField>
        <EvalField label="生成题数"><input aria-label="生成题数" className={evalControl + ' w-full'} type="number" min={1} max={5} value={count} onChange={e => setCount(Number(e.target.value))} /></EvalField>
        <EvalField label="数据集标识（高级）"><input aria-label="合成数据集标识" className={evalControl + ' w-full'} value={key} onChange={e => setKey(e.target.value)} placeholder="留空自动生成" /></EvalField>
        <EvalField label="版本（高级）"><input aria-label="合成版本" className={evalControl + ' w-full'} value={version} onChange={e => setVersion(e.target.value)} /></EvalField>
      </div>
      <div className="mt-4"><EvalWarnBox>⚠ 合成结果只是待审核事实建议，不会自动成为 Golden；每批生成会产生模型用量，请先确认预算。</EvalWarnBox></div>
      <div className="mt-4 flex gap-2">
        <EvalButton variant="primary" disabled={busy || !kb || !name.trim() || Boolean(task && !["SUCCESS", "FAILED", "CANCELLED"].includes(task.status))} onClick={submit}>{busy ? '正在提交…' : '✧ 生成候选并送审'}</EvalButton>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
    </EvalCard>
    <EvalCard title="生成预览与任务状态" subtitle="候选不等于经过验证的标准答案；模型输出不会自动成为 Golden。">
      {!task ? <EvalEmpty>提交生成任务后，这里展示后台执行状态与写入 V2 待审核队列的数量。</EvalEmpty> : <div role="status">
        <p className="text-sm text-text-primary">任务 {task.task_id.slice(0, 8)}：{taskLabels[task.status] || task.status} · {task.progress}</p>
        {task.error && <p className="mt-2 text-red-700">{task.error}</p>}
        {task.status === 'SUCCESS' && <div className="mt-3">
          <p className="text-sm text-text-secondary">已将 {task.output?.v2_candidate_count ?? 0} 条生成结果写入 V2 待审核队列；模型答案仍只是建议事实。</p>
          {onGoReview && <div className="mt-3"><EvalButton variant="soft" onClick={onGoReview}>打开 V2 标注审核</EvalButton></div>}
        </div>}
        {!['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status) && <div className="mt-3">
          <EvalButton disabled={cancelRequested} onClick={() => void evaluationAssets.cancel(task.task_id).then(() => { setCancelRequested(true); return evaluationAssets.task(task.task_id) }).then(setTask).catch(e => setError(String(e)))}>{cancelRequested ? '取消已申请，当前调用结束后生效' : '取消合成'}</EvalButton>
        </div>}
      </div>}
      <div className="mt-4"><EvalInfoBox>真实链路：结构解析 → 场景采样 → LLM 合成 → 原文定位 → 事实校验 → 去重/隐私过滤 → 人工复核。</EvalInfoBox></div>
    </EvalCard>
  </div>
}
