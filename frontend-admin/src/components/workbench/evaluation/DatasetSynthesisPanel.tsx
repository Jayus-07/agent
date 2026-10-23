'use client'

import { useEffect, useState } from 'react'
import { evaluationAssets, type EvaluationTask } from '@/api/evaluation-assets'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm disabled:opacity-50'
const taskLabels: Record<string, string> = { PENDING: '排队中', RUNNING: '生成中', RETRYING: '等待自动重试', SUCCESS: '已完成', FAILED: '执行失败', CANCELLED: '已取消', PAUSED: '已暂停' }
export default function DatasetSynthesisPanel() {
  const [kb, setKb] = useState('')
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [loaded, setLoaded] = useState(false)
  const [cancelRequested, setCancelRequested] = useState(false)
  const [version, setVersion] = useState('1.0.0')
  const [count, setCount] = useState(5)
  const [task, setTask] = useState<EvaluationTask | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
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
  return <details className="rounded-lg border border-border-subtle p-4 text-sm" onToggle={e => { if (e.currentTarget.open && !loaded) void listKbAuthority().then(result => { setKbs(result.knowledge_bases.filter(k => k.active && !k.alias_for)); setLoaded(true) }).catch(e => setError(String(e))) }}><summary className="cursor-pointer">更多：从知识库生成候选题</summary><p className="my-3 text-xs leading-5 text-text-muted">使用 Ragas 和已配置评测模型，会产生模型用量。每批最多 5 题；合成结果须人工审核。</p>
    <div className="grid gap-4 sm:grid-cols-3"><label>来源知识库<select aria-label="合成来源知识库" className={`${control} mt-1 w-full`} value={kb} onChange={e => setKb(e.target.value)}><option value="">请选择知识库</option>{kbs.map(k => <option key={k.kb_id} value={k.kb_id}>{k.name}</option>)}</select></label><label>新评测集名称<input aria-label="合成评测集名称" className={`${control} mt-1 w-full`} value={name} onChange={e => setName(e.target.value)} placeholder="如：员工手册候选题" /></label><label>生成题数<input aria-label="合成题数" className={`${control} mt-1 w-full`} type="number" min={1} max={5} value={count} onChange={e => setCount(Number(e.target.value))} /></label></div><details className="my-3 text-xs text-text-muted"><summary className="cursor-pointer">高级：标识与版本</summary><div className="mt-2 flex gap-2"><input aria-label="合成数据集标识" className={control} value={key} onChange={e => setKey(e.target.value)} placeholder="留空自动生成" /><input aria-label="合成版本" className={control} value={version} onChange={e => setVersion(e.target.value)} /></div></details><button className={`${control} bg-blue-600 text-white`} disabled={busy || !kb || !name.trim() || Boolean(task && !["SUCCESS", "FAILED", "CANCELLED"].includes(task.status))} onClick={() => void (async () => {
      setBusy(true); setError('')
      try {
        const saved = localStorage.getItem('evaluation-synthesis-request')
        const request = saved ? JSON.parse(saved) : { ...(await evaluationAssets.freeze(kb)), request_id: crypto.randomUUID(), count, manifest: { schema_version: 'rag-eval/1.0', dataset_key: key || `synthetic_${Date.now()}`, name, module: 'rag', version } }
        localStorage.setItem('evaluation-synthesis-request', JSON.stringify(request))
        const next = await evaluationAssets.synthesize(request.snapshot_id, request.manifest, request.count, request.request_id)
        localStorage.removeItem('evaluation-synthesis-request'); localStorage.setItem('evaluation-synthesis-task', next.task_id); setCancelRequested(false); setTask(next)
      } catch (e) { setError(e instanceof Error ? e.message : '合成任务提交失败') } finally { setBusy(false) }
    })()}>{busy ? '正在提交…' : '生成待审核候选'}</button>
    {error && <p role="alert" className="mt-2 text-red-700">{error}</p>}{task && <div role="status" className="mt-2">任务 {task.task_id}：{taskLabels[task.status] || task.status} · {task.progress}<p className="text-red-700">{task.error}</p>{task.status === 'SUCCESS' && <p>候选已生成，请刷新评测集并前往审核。</p>}{!['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status) && <button className={control} disabled={cancelRequested} onClick={() => void evaluationAssets.cancel(task.task_id).then(() => { setCancelRequested(true); return evaluationAssets.task(task.task_id) }).then(setTask).catch(e => setError(String(e)))}>{cancelRequested ? "取消已申请，当前调用结束后生效" : "取消合成"}</button>}</div>}
  </details>
}
