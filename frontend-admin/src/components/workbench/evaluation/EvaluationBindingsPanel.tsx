'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { evaluationAssets, type ImportedDataset, type EvaluationBinding, type EvaluationTask } from '@/api/evaluation-assets'
import { evaluationService, type EvalRunDetail } from '@/api/evaluation'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'
import { atLeast } from '@/lib/auth'
import { useToast } from '@/components/shared/Toast'
import CaseSampleDetail from '@/components/evaluations/CaseSampleDetail'
import EvaluationSubjectPicker from './EvaluationSubjectPicker'
import PublicationGateControl from './PublicationGateControl'

const control = 'min-h-11 rounded-md border border-border-subtle bg-surface-base px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-blue-300 disabled:opacity-50'
const taskLabels: Record<string, string> = { PENDING: '排队中', RETRYING: '等待自动重试', RUNNING: '评测中', SUCCESS: '已完成', FAILED: '执行失败', CANCELLED: '已取消', PAUSED: '已暂停', WAITING_USER: '等待处理' }

export default function EvaluationBindingsPanel() {
  const params = useSearchParams()
  const toast = useToast()
  const toastRef = useRef(toast)
  toastRef.current = toast
  const [imports, setImports] = useState<ImportedDataset[]>([])
  const [kbs, setKbs] = useState<KbAuthorityItem[]>([])
  const [bindings, setBindings] = useState<EvaluationBinding[]>([])
  const [selected, setSelected] = useState(params.get('import_id') || '')
  const [kb, setKb] = useState(params.get('kb_id') || '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [run, setRun] = useState<EvalRunDetail | null>(null)
  const [task, setTask] = useState<EvaluationTask | null>(null)
  const [mode, setMode] = useState('retrieval')
  const [split, setSplit] = useState('')
  const [fixed, setFixed] = useState(true)
  const [subject, setSubject] = useState('')
  const [minimum, setMinimum] = useState(5)
  const [passRate, setPassRate] = useState(0.95)
  const [cancelRequested, setCancelRequested] = useState(false)
  const load = useCallback(async () => {
    const [datasets, catalog, links] = await Promise.all([evaluationAssets.list(), listKbAuthority(), evaluationAssets.bindings()])
    setImports(datasets.filter(item => item.review.decision === 'approved'))
    setKbs(catalog.knowledge_bases.filter(item => item.active && !item.alias_for && !item.deprecated))
    setBindings(links)
  }, [])
  useEffect(() => {
    setReady(true)
    if (atLeast('super_admin')) void load().catch(e => setError(e instanceof Error ? e.message : '加载关联失败'))
  }, [load])
  useEffect(() => {
    if (!atLeast('super_admin')) return
    const saved = localStorage.getItem('evaluation-active-task')
    if (saved) void evaluationAssets.task(saved).then(setTask).catch(() => setError('读取上次任务失败，请刷新重试'))
  }, [])
  useEffect(() => {
    if (!task || ['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status)) return
    let active = true
    let inFlight = false
    const timer = setInterval(() => {
      if (inFlight) return
      inFlight = true
      void evaluationAssets.task(task.task_id).then(next => {
        if (!active) return
        setTask(next)
        if (['SUCCESS', 'FAILED', 'CANCELLED'].includes(next.status)) {
          localStorage.removeItem('evaluation-active-task')
        }
      }).catch(e => { if (active) setError(e instanceof Error ? e.message : '任务状态读取失败') }).finally(() => { inFlight = false })
    }, 2000)
    return () => { active = false; clearInterval(timer) }
  }, [task?.task_id, task?.status])
  useEffect(() => {
    if (task?.status !== 'SUCCESS' || !task.output?.run_id) return
    let active = true
    void evaluationService.getRun(task.output.run_id).then(detail => {
      if (!active) return
      setRun(detail)
      if (task.output?.validity !== 'VALID') toastRef.current.warning('运行无效，请查看原因')
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : '读取结果失败，请重试') })
    void load().catch(e => { if (active) setError(e instanceof Error ? e.message : '刷新关联失败') })
    return () => { active = false }
  }, [task?.task_id, task?.status, task?.output?.run_id, load])
  if (!ready) return <p className="p-4 text-xs">加载关联…</p>
  if (!atLeast('super_admin')) return <p className="p-4 text-xs text-text-muted">共享知识库评测关联仅限平台超级管理员管理。</p>
  const action = async (fn: () => Promise<void>) => {
    setBusy(true); setError('')
    try { await fn() } catch (e) { setError(e instanceof Error ? e.message : '评测操作失败') } finally { setBusy(false) }
  }
  const scopedBindings = bindings.filter(item => !params.get('kb_id') || item.kb_id === params.get('kb_id'))
  const runValidity = run?.report.metadata?.run_validity as { validity?: string; invalid_reason?: string } | undefined
  const passed = run?.report.results.filter(item => item.status === 'pass').length || 0
  const runningTask = task && !['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status)
  return <div className="mx-auto max-w-7xl space-y-5 px-6 py-6">
    <div><h2 className="text-sm font-semibold">关联知识库并运行</h2><p className="mt-1 text-xs text-text-muted">固定题集版本，可选择子集和检索或答案评测。任务由后台执行，关闭页面后仍可查看状态。</p></div>
    {error && <div role="alert" className="rounded bg-red-50 p-3 text-xs text-red-700">{error}</div>}
    <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border-subtle p-4">
      <label className="min-w-56 flex-1 text-xs">评测集版本<select aria-label="评测集版本" className={`${control} mt-2 w-full`} value={selected} disabled={busy} onChange={e => setSelected(e.target.value)}><option value="">请选择已发布题集</option>{imports.map(item => <option key={item.import_id} value={item.import_id}>{item.manifest.name} / {item.manifest.version}</option>)}</select></label>
      <label className="min-w-56 flex-1 text-xs">目标知识库<select aria-label="目标知识库" className={`${control} mt-2 w-full`} disabled={busy} value={kb} onChange={e => setKb(e.target.value)}><option value="">请选择知识库</option>{kbs.map(item => <option key={item.kb_id} value={item.kb_id}>{item.name} / {item.kb_id}</option>)}</select></label>
      <label className="text-xs">评测方式<select aria-label="评测方式" className={control} value={mode} onChange={e => setMode(e.target.value)}><option value="retrieval">检索评测</option><option value="answer">检索与答案评测</option></select></label>
      <details className="order-last basis-full border-t border-border-subtle pt-3 text-sm"><summary className="cursor-pointer text-text-secondary">高级设置</summary><p className="my-3 text-xs text-text-muted">默认评测全部题目，并保留固定语料以便复跑。只有需要专项评测或配置发布门禁时才需调整。</p><div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
      <label className="text-xs">子集<input aria-label="子集" className={control} value={split} onChange={e => setSplit(e.target.value)} placeholder="留空执行全部" /></label>
      <label className="text-xs"><input type="checkbox" checked={fixed} onChange={e => setFixed(e.target.checked)} /> 保留固定索引快照</label>
      <EvaluationSubjectPicker value={subject} onChange={setSubject} />
      <label className="text-xs">门禁最少题数<input aria-label="门禁最少题数" className={`${control} w-20`} type="number" min={1} max={100} value={minimum} onChange={e => setMinimum(Number(e.target.value))} /></label>
      <label className="text-xs">门禁通过率<input aria-label="门禁通过率" className={`${control} w-24`} type="number" min={0.5} max={1} step={0.01} value={passRate} onChange={e => setPassRate(Number(e.target.value))} /></label>
      </div></details>
      <button disabled={busy || !selected || !kb} className={`${control} bg-blue-600 text-white`} onClick={() => void action(async () => { const snapshot = fixed ? await evaluationAssets.freeze(kb) : null; await evaluationAssets.bind(selected, kb, { mode, split, snapshot_id: snapshot?.snapshot_id || '', subject_user_id: subject ? Number(subject) : null, min_samples: minimum, pass_rate: passRate }); if (snapshot?.warnings?.length) toast.warning('关联已保存：旧索引登记需要复核，可运行诊断评测，发布门禁不会放行'); else toast.success('知识库关联已保存'); await load() })}>校验证据并关联</button>
    </div>
    {imports.length === 0 && <p className="text-xs text-text-muted">暂无已发布业务题集。<Link className="ml-2 text-blue-600" href="/evaluations/center?tab=datasets">导入评测集</Link></p>}
    <div className="overflow-x-auto rounded-lg border border-border-subtle"><table className="w-full min-w-[650px] text-left text-xs"><thead className="bg-slate-50"><tr><th className="p-3">评测集 / 固定版本</th><th className="p-3">知识库</th><th className="p-3">最近运行</th><th className="p-3">操作</th></tr></thead><tbody>{scopedBindings.map(item => <tr key={item.binding_id} className="border-t border-border-subtle"><td className="p-3">{item.dataset_name}<span className="ml-2 text-text-muted">v{item.dataset_version}</span><p className="mt-1 text-xs text-text-muted">{item.mode === "answer" ? "检索与答案" : "仅检索"} · {item.split || "全部题目"} · {item.snapshot_id ? "固定语料" : "当前语料"}</p></td><td className="p-3"><Link href={`/knowledge/workbench?tab=authority&kb_id=${encodeURIComponent(item.kb_id)}`} className="text-blue-600">{kbs.find(k => k.kb_id === item.kb_id)?.name || item.kb_id}</Link></td><td className="p-3">{item.latest_run ? <button className="text-blue-600" disabled={busy} onClick={() => void action(async () => { setRun(await evaluationService.getRun(item.latest_run!.run_id)) })}>{item.latest_run.validity === 'VALID' ? '已完成 · 查看结果' : '未能有效评测 · 查看原因'}</button> : '尚未运行'}</td><td className="p-3"><button disabled={busy || Boolean(runningTask)} className={control} onClick={() => void action(async () => {
      setRun(null)
      const storageKey = `evaluation-request-${item.binding_id}`
      const requestId = localStorage.getItem(storageKey) || crypto.randomUUID()
      localStorage.setItem(storageKey, requestId)
      const result = await evaluationAssets.run(item.binding_id, requestId)
      localStorage.removeItem(storageKey)
      localStorage.setItem('evaluation-active-task', result.task_id)
      setCancelRequested(false); setTask(result)
    })}>{busy ? '处理中…' : '运行评测'}</button><PublicationGateControl binding={item} /></td></tr>)}</tbody></table>{scopedBindings.length === 0 && <p className="p-8 text-center text-xs text-text-muted">暂无关联，选择题集与知识库后创建。</p>}</div>
    {busy && <p role="status" className="text-xs text-text-muted">请求处理中，检索评测可能需要数分钟。请勿重复提交；超时后请先在评测结果页核实运行状态。</p>}
    {task && <div role="status" className="rounded border border-border-subtle p-3 text-xs">任务 {task.task_id}：{taskLabels[task.status] || task.status} · {task.progress}{task.error && <p className="text-red-700">{task.error}</p>}{["FAILED", "PAUSED"].includes(task.status) && <button className={control} onClick={() => void action(async () => { setTask(await evaluationAssets.resume(task.task_id)); localStorage.setItem("evaluation-active-task", task.task_id) })}>恢复任务</button>}{!['SUCCESS', 'FAILED', 'CANCELLED'].includes(task.status) && <button className={`${control} ml-3`} disabled={cancelRequested || busy} onClick={() => void action(async () => { await evaluationAssets.cancel(task.task_id); setCancelRequested(true); setTask(await evaluationAssets.task(task.task_id)) })}>{cancelRequested ? "取消请求已发送" : "取消任务"}</button>}</div>}
    {cancelRequested && runningTask && <p role="status" className="text-xs text-text-muted">取消请求已送达，当前题目或模型调用结束后停止。请等待服务端确认已取消。</p>}{run && <section aria-label="本次评测结果" className="space-y-3"><h3 className="text-sm font-semibold">运行结果：{run.run_id}</h3><div className="rounded-md bg-slate-50 p-4 text-sm"><p className="font-medium">{runValidity?.validity === "VALID" ? `评测完成：${passed} / ${run.report.results.length} 题通过` : "本次运行无效，不能用于判断知识库质量"}</p>{runValidity?.invalid_reason && <p className="mt-2 text-red-700">原因：{runValidity.invalid_reason}</p>}{Array.isArray(run.report.metadata?.corpus_capture_warnings) && run.report.metadata.corpus_capture_warnings.length > 0 && <p role="alert" className="mt-2 text-amber-700">旧索引的文档或父块登记不完整。本次结果用于诊断，不能通过发布门禁；请先重建报告中标记的文档。</p>}<p className="mt-2 text-xs text-text-muted">展开下方题目查看回答、检索证据和未通过原因。</p></div><details className="text-xs text-text-muted"><summary className="cursor-pointer">查看诊断信息</summary><pre className="mt-2 overflow-auto rounded bg-slate-50 p-3">{JSON.stringify(run.report.metadata?.run_validity || {}, null, 2)}</pre></details>{run.report.results.map(item => <details key={item.case_id} className="rounded border border-border-subtle p-3 text-xs"><summary className="cursor-pointer">{item.case_id} · {item.status === 'pass' ? '通过' : item.status === 'fail' ? '未通过' : item.status === 'error' ? '执行错误' : '已跳过'}</summary><CaseSampleDetail result={item} /></details>)}</section>}
  </div>
}
