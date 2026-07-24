'use client'

import { useState, useRef, useEffect } from 'react'
import { flushSync } from 'react-dom'
import { Upload, Loader2, CheckCircle2, XCircle, FileText, AlertCircle } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'

interface Props { open: boolean; onClose: () => void; onSuccess: () => void }

// 阶段映射：后端 SSE stage → 显示文案 + UI 索引
const STAGES = [
  { key: 'uploading', label: '上传文件' },
  { key: 'parsing',   label: '文本解析' },
  { key: 'chunking',  label: 'Chunk 切分' },
  { key: 'embedding', label: 'Embedding' },
  { key: 'writing',   label: '写入向量库' },
  { key: 'done',      label: '完成' },
] as const

type StageKey = typeof STAGES[number]['key']

/** 格式化耗时：<1ms 显示小数，<1s 显示 ms，>=1s 显示 x.xs */
function fmtMs(ms: number): string {
  if (ms < 1) return `${(ms * 1000).toFixed(0)}μs`
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// 单文件上传结果（用于已完成列表）
interface CompletedFile {
  name: string
  ok: boolean
  duplicate?: boolean
  error?: string
  stageElapsed?: Record<string, number>  // 每阶段耗时(ms)
}

export default function UploadDialog({ open, onClose, onSuccess }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [currentStage, setCurrentStage] = useState<StageKey | null>(null)
  const [stageMessage, setStageMessage] = useState('')
  const [currentIdx, setCurrentIdx] = useState(0)
  const [completed, setCompleted] = useState<CompletedFile[]>([])
  const [error, setError] = useState('')
  // 阶段耗时追踪：每个阶段完成时的相对时间（ms）
  const [stageElapsed, setStageElapsed] = useState<Record<string, number>>({})
  const stageStartRef = useRef<Record<string, number>>({})
  const uploadStartRef = useRef<number>(0)
  const fileElapsedRef = useRef<Record<string, number>>({})
  const fileRef = useRef<HTMLInputElement>(null)
  // 轮询 RAG 就绪状态
  const [ragReady, setRagReady] = useState<boolean | null>(null)
  const healthTimerRef = useRef<ReturnType<typeof setInterval>>()
  // KB + 部门选择
  const [selectedKbId, setSelectedKbId] = useState('policy_general')
  const [selectedDept, setSelectedDept] = useState('general')
  const [kbList, setKbList] = useState<{ id: string; name: string; depts: { id: string; label: string }[] }[]>([])

  // 加载 KB 列表
  useEffect(() => {
    if (!open) return
    fetch('/api/rag/knowledge-bases')
      .then(r => r.json())
      .then(d => {
        const kbs = d.knowledge_bases || []
        setKbList(kbs)
        if (kbs.length > 0 && !kbs.find((k: any) => k.id === selectedKbId)) {
          setSelectedKbId(kbs[0].id)
          setSelectedDept(kbs[0].depts?.[0]?.id || 'general')
        }
      })
      .catch(() => {})
  }, [open])

  const currentKb = kbList.find(k => k.id === selectedKbId)

  // 轮询 RAG 就绪状态
  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const res = await fetch('/api/rag/health')
        const data = await res.json()
        if (!cancelled) {
          setRagReady(data.ready === true)
          if (data.ready) clearInterval(healthTimerRef.current)
        }
      } catch { /* 后端未启动，继续轮询 */ }
    }
    check() // 立即查一次
    healthTimerRef.current = setInterval(check, 2000)
    return () => {
      cancelled = true
      clearInterval(healthTimerRef.current)
    }
  }, [open])

  if (!open) return null

  const handleClose = () => {
    setFiles([])
    setUploading(false)
    setError('')
    setCurrentStage(null)
    setStageMessage('')
    setCurrentIdx(0)
    setCompleted([])
    setStageElapsed({})
    stageStartRef.current = {}
    uploadStartRef.current = 0
    onClose()
  }

  const handleUpload = async () => {
    if (!files.length) return
    setUploading(true)
    setError('')
    setCompleted([])
    setCurrentIdx(0)
    setStageElapsed({})

    let okCount = 0
    // 多文件上传生成批次号，后端写入操作日志关联同一批次
    const batchId = files.length > 1 ? crypto.randomUUID() : undefined
    // 串行上传：一次一个，本地 embedding 模型不并发
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      // 每文件独立计时
      stageStartRef.current = {}
      uploadStartRef.current = performance.now()
      fileElapsedRef.current = {}
      setCurrentIdx(i)
      setCurrentStage('uploading')
      setStageMessage('')
      try {
        const res = await knowledgeService.uploadDocument(file, (stage, message) => {
          const now = performance.now()
          // duplicate: 后端检测到相同文件，跳过索引
          if (stage === 'duplicate') {
            fileElapsedRef.current = { uploading: now - uploadStartRef.current }
            return
          }
          if (!stageStartRef.current[stage]) stageStartRef.current[stage] = now
          flushSync(() => {
            setCurrentStage(stage as StageKey)
            setStageMessage(message)
          })
          // 最后一个阶段：计算每阶段增量耗时，存到 ref（批量上传时每文件独立保留）
          if (stage === 'done') {
            const elapsed: Record<string, number> = {}
            let prev = uploadStartRef.current
            for (const s of STAGES) {
              const t = stageStartRef.current[s.key]
              if (t && prev) {
                elapsed[s.key] = t - prev
                prev = t
              }
            }
            fileElapsedRef.current = elapsed
            setStageElapsed(elapsed)
          }
        }, batchId, selectedKbId, selectedDept)
        if (res.ok) {
          okCount++
          setCompleted(prev => [...prev, { name: file.name, ok: true, duplicate: res.duplicate, stageElapsed: { ...fileElapsedRef.current } }])
        } else {
          setCompleted(prev => [...prev, { name: file.name, ok: false, error: res.error || '上传失败' }])
        }
      } catch (e) {
        setCompleted(prev => [...prev, { name: file.name, ok: false, error: (e as Error).message || '网络错误' }])
      }
    }

    // 不在 finally 里 setUploading(false) — 那会让进度条瞬间消失（原 bug）
    // 改为：上传完成后保持进度条显示，根据结果决定关窗时机
    setUploading(false)
    setCurrentStage('done')

    if (okCount > 0) onSuccess()
  }

  const isMulti = files.length > 1
  const okCount = completed.filter(c => c.ok).length
  const failCount = completed.filter(c => !c.ok).length

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[480px] p-6" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-text-primary mb-4">
          上传文档{isMulti ? `（${files.length} 个文件）` : ''}
        </h3>

        {/* 文件选择区（非上传中显示） */}
        {!uploading && currentStage !== 'done' && (
          <div className="space-y-3 mb-4">
            {/* KB + 部门选择 */}
            <div className="flex gap-2">
              <select value={selectedKbId} onChange={e => { setSelectedKbId(e.target.value); const kb = kbList.find(k => k.id === e.target.value); if (kb?.depts[0]) setSelectedDept(kb.depts[0].id) }}
                className="flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none">
                {kbList.map(kb => <option key={kb.id} value={kb.id}>{kb.name}</option>)}
              </select>
              <select value={selectedDept} onChange={e => setSelectedDept(e.target.value)}
                className="flex-1 px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none">
                {(currentKb?.depts || [{id:'general',label:'通用'}]).map(d => <option key={d.id} value={d.id}>{d.label}</option>)}
              </select>
            </div>

            {/* RAG 未就绪 → 等待提示 */}
            {ragReady === false && (
              <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-700">
                <Loader2 size={14} className="animate-spin shrink-0" />
                <span>知识库服务启动中，预计 15s 内就绪…</span>
              </div>
            )}
            <div className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors ${ragReady === false ? 'border-amber-200 bg-amber-50/30 cursor-not-allowed' : 'border-border-subtle cursor-pointer'}`}>
              <input type="file" ref={fileRef} multiple
                accept=".pdf,.md,.txt,.docx" className="hidden"
                disabled={ragReady === false}
                onChange={e => { const fs = Array.from(e.target.files || []); if (fs.length) setFiles(fs) }} />
              <div onClick={() => { if (ragReady !== false) fileRef.current?.click() }}>
                <Upload size={28} className={`mx-auto mb-2 ${ragReady === false ? 'text-amber-300' : 'text-text-muted'}`} />
                <p className={`text-xs ${ragReady === false ? 'text-amber-400' : 'text-text-muted'}`}>
                  {ragReady === false ? '请等待服务就绪…' : '点击选择 PDF / MD / TXT / DOCX（可多选）'}
                </p>
              </div>
            </div>
            {files.length > 0 && (
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {files.map((f, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs text-text-primary px-2 py-1 bg-surface-elevated rounded">
                    <FileText size={13} className="text-accent shrink-0" />
                    <span className="truncate flex-1">{f.name}</span>
                    <span className="text-text-muted text-[10px]">{f.size < 1024 ? f.size + ' B' : (f.size / 1024).toFixed(1) + ' KB'}</span>
                    <button onClick={() => setFiles(files.filter((_, j) => j !== i))} className="text-text-muted hover:text-red-500">
                      <XCircle size={13} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {error && <div className="flex items-center gap-2 text-xs text-red-500"><AlertCircle size={13} /> {error}</div>}
          </div>
        )}

        {/* 上传进度（上传中或刚完成） */}
        {(uploading || currentStage === 'done') && files.length > 0 && (
          <div className="space-y-3 mb-4">
            {/* 多文件时显示计数 */}
            {isMulti && (
              <div className="text-xs text-text-muted flex items-center justify-between">
                <span>进度 {Math.min(currentIdx + (uploading ? 1 : okCount + failCount), files.length)} / {files.length}</span>
                <span className="text-green-600">✓ {okCount}{failCount > 0 && <span className="text-red-500">  ✗ {failCount}</span>}</span>
              </div>
            )}

            {/* 上传中：6 阶段实时进度 */}
            {uploading && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-xs text-text-primary">
                  <FileText size={13} className="text-accent shrink-0" />
                  <span className="truncate">{files[currentIdx]?.name}</span>
                </div>
                {STAGES.map((s) => {
                  const currentIdxStage = STAGES.findIndex(x => x.key === currentStage)
                  const thisIdx = STAGES.findIndex(x => x.key === s.key)
                  const isDone = thisIdx < currentIdxStage
                  const isCurrent = thisIdx === currentIdxStage
                  return (
                    <div key={s.key} className="flex items-center gap-2 text-xs pl-5">
                      {isDone ? <CheckCircle2 size={12} className="text-green-500" />
                        : isCurrent ? <Loader2 size={12} className="text-accent animate-spin" />
                        : <div className="w-[12px] h-[12px] rounded-full border border-border-subtle" />}
                      <span className={isDone || isCurrent ? 'text-text-primary' : 'text-text-muted'}>{s.label}</span>
                      {isCurrent && stageMessage && (
                        <span className="text-text-muted text-[10px] ml-1 truncate">— {stageMessage}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {/* 完成后：每文件独立展示处理报告 */}
            {!uploading && currentStage === 'done' && completed.length > 0 && (
              <div className={`space-y-3 ${isMulti ? 'max-h-56 overflow-y-auto' : ''}`}>
                {completed.map((c, i) => {
                  const totalMs = c.stageElapsed ? Object.values(c.stageElapsed).reduce((a, b) => a + b, 0) : 0
                  return (
                    <div key={i} className="space-y-1.5">
                      {/* 文件名 + 总耗时/状态 */}
                      <div className="flex items-center gap-2 text-xs">
                        {c.duplicate ? <CheckCircle2 size={13} className="text-amber-500 shrink-0" />
                          : c.ok ? <CheckCircle2 size={13} className="text-green-500 shrink-0" />
                          : <XCircle size={13} className="text-red-500 shrink-0" />}
                        <FileText size={13} className="text-accent shrink-0" />
                        <span className={`truncate font-medium ${c.duplicate ? 'text-text-muted' : 'text-text-primary'}`}>{c.name}</span>
                        {c.duplicate ? <span className="text-[10px] text-amber-500 shrink-0 ml-auto">已存在，跳过</span>
                          : c.ok && <span className="text-text-muted text-[10px] tabular-nums shrink-0 ml-auto">{totalMs > 0 ? fmtMs(totalMs) : '<1ms'}</span>}
                        {c.error && <span className="text-[10px] text-red-400 truncate max-w-[120px] shrink-0 ml-auto">{c.error}</span>}
                      </div>
                      {/* 6 阶段耗时子行 */}
                      {c.ok && !c.duplicate && c.stageElapsed && Object.keys(c.stageElapsed).length > 1 && (
                        <div className="space-y-0.5 ml-8">
                          {STAGES.filter(s => c.stageElapsed![s.key] !== undefined).map((s) => {
                            const ms = c.stageElapsed![s.key]
                            return (
                              <div key={s.key} className="flex items-center gap-2 text-xs">
                                <CheckCircle2 size={10} className="text-green-400 shrink-0" />
                                <span className="text-text-secondary text-[10px]">{s.label}</span>
                                {ms !== undefined && (
                                  <span className="text-text-muted text-[10px] ml-auto tabular-nums">{fmtMs(ms)}</span>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {/* 完成汇总 */}
            {!uploading && currentStage === 'done' && (
              <div className="text-xs text-text-secondary flex items-center gap-2">
                <CheckCircle2 size={14} className="text-green-500" />
                成功 {okCount} 篇{failCount > 0 && `，失败 ${failCount} 篇`}
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          {currentStage === 'done' ? (
            <button onClick={handleClose} className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors">关闭</button>
          ) : (
            <>
              <button onClick={handleClose} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">
                {uploading ? '后台上传' : '取消'}
              </button>
              <button onClick={handleUpload} disabled={!files.length || uploading || ragReady === false}
                className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors">
                {ragReady === false ? '等待服务就绪…' : uploading ? '上传中...' : `上传并索引${files.length > 1 ? `（${files.length}）` : ''}`}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
