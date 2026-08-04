'use client'

import { useState, useRef, useEffect } from 'react'
import { Upload, Loader2, CheckCircle2, XCircle, FileText, AlertCircle, ChevronDown, ChevronRight } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'

interface Props { open: boolean; onClose: () => void; onSuccess: () => void }

// 阶段映射：后端 SSE stage → 显示文案 + UI 索引（9 阶段，覆盖后端 span 树）
const STAGES = [
  { key: 'uploading', label: '上传文件' },
  { key: 'loading',   label: '文件加载' },
  { key: 'parsing',   label: '文本解析' },
  { key: 'cleaning',  label: '文本清洗' },
  { key: 'dedup',     label: '去重检查' },
  { key: 'chunking',  label: 'Chunk 切分' },
  { key: 'metadata',  label: '元数据抽取' },
  { key: 'embedding', label: 'Embedding' },
  { key: 'writing',   label: '写入向量库' },
  { key: 'done',      label: '完成' },
] as const

type StageKey = typeof STAGES[number]['key']

/** 格式化耗时：<1ms 显示 "<1ms"，<1s 显示 ms，>=1s 显示 x.xs */
function fmtMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '<1ms'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 单文件处理报告 */
interface FileReport {
  name: string
  status: 'running' | 'success' | 'duplicate' | 'failed'
  error?: string
  /** 阶段耗时（成功/duplicate 时填充） */
  stageElapsed?: Record<string, number>
  /** 后端真实总耗时（覆盖 stageElapsed 之和，避免幽灵时间） */
  totalMs?: number
  /** 当前阶段（运行中） */
  currentStage?: StageKey | null
  /** 当前阶段附带消息 */
  stageMessage?: string
  /** 展开详情 */
  expanded?: boolean
}

export default function UploadDialog({ open, onClose, onSuccess }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [reports, setReports] = useState<FileReport[]>([])
  const [error, setError] = useState('')
  const [aggregatedExpanded, setAggregatedExpanded] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)
  const [ragReady, setRagReady] = useState<boolean | null>(null)
  const healthTimerRef = useRef<ReturnType<typeof setInterval>>()
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
    check()
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
    setReports([])
    setAggregatedExpanded(true)
    onClose()
  }

  const toggleExpanded = (idx: number) => {
    setReports(prev => prev.map((r, i) => i === idx ? { ...r, expanded: !r.expanded } : r))
  }

  const updateReport = (idx: number, patch: Partial<FileReport>) => {
    setReports(prev => prev.map((r, i) => i === idx ? { ...r, ...patch } : r))
  }

  const handleUpload = async () => {
    if (!files.length) return
    setUploading(true)
    setError('')
    setReports(files.map(f => ({ name: f.name, status: 'running', expanded: false })))

    let okCount = 0
    const batchId = files.length > 1 ? crypto.randomUUID() : undefined

    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      const fileStart = performance.now()
      const stageTimes: Record<string, number> = {}

      try {
        const res = await knowledgeService.uploadDocument(file, (stage, message, durationMs, stageElapsed) => {
          // duplicate 推进时附带 stage_elapsed，由终态分支使用
          if (stage === 'duplicate') {
            stageTimes.uploading = performance.now() - fileStart
            return
          }
          const now = performance.now()
          stageTimes[stage] = now
          updateReport(i, {
            currentStage: stage as StageKey,
            stageMessage: message,
          })
        }, batchId, selectedKbId, selectedDept)

        if (res.duplicate) {
          // 终态 stage_elapsed（后端传）优先；前端兜底仍按 SSE 间隔法
          const serverElapsed = res.stage_elapsed
          const elapsed: Record<string, number> = {
            uploading: stageTimes.uploading ?? (performance.now() - fileStart),
            ...(serverElapsed || {}),
          }
          updateReport(i, { status: 'duplicate', stageElapsed: elapsed, totalMs: res.total_ms, currentStage: null, stageMessage: '' })
          okCount++
        } else if (res.ok) {
          // 优先使用后端提供的 stage_elapsed；缺失阶段用 SSE 间隔法兜底
          const serverElapsed = res.stage_elapsed
          const elapsed: Record<string, number> = {}
          let prev = fileStart
          for (const s of STAGES) {
            const t = stageTimes[s.key]
            if (t !== undefined) {
              elapsed[s.key] = t - prev
              prev = t
            }
          }
          // 用后端值覆盖前端累加（前端累加只能保留 uploading）
          const finalElapsed: Record<string, number> = { uploading: elapsed.uploading ?? 0 }
          if (serverElapsed) {
            for (const [k, v] of Object.entries(serverElapsed)) {
              finalElapsed[k] = v
            }
          } else {
            // 无后端数据时回退到前端 SSE 间隔法
            Object.assign(finalElapsed, elapsed)
          }
          updateReport(i, { status: 'success', stageElapsed: finalElapsed, totalMs: res.total_ms, currentStage: null, stageMessage: '' })
          okCount++
        } else {
          updateReport(i, { status: 'failed', error: res.error || '上传失败', currentStage: null, stageMessage: '' })
        }
      } catch (e) {
        updateReport(i, { status: 'failed', error: (e as Error).message || '网络错误', currentStage: null, stageMessage: '' })
      }
    }

    setUploading(false)
    if (okCount > 0) onSuccess()
  }

  const isMulti = files.length > 1
  const okCount = reports.filter(r => r.status === 'success' || r.status === 'duplicate').length
  const failCount = reports.filter(r => r.status === 'failed').length
  const completedCount = reports.filter(r => r.status !== 'running').length
  const showReports = uploading || completedCount > 0

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[480px] p-6" onClick={e => e.stopPropagation()}>
        {/* 标题：始终展示，并在多文件/有进度时显示聚合信息 */}
        <div className="mb-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-text-primary">
              上传文档{isMulti ? `（${files.length} 个文件）` : ''}
            </h3>
            {isMulti && showReports && (
              <div className="text-xs text-text-muted flex items-center gap-2">
                <span>进度 {completedCount} / {files.length}</span>
                <span className="text-green-600">✓ {okCount}</span>
                {failCount > 0 && <span className="text-red-500">⚠ {failCount}</span>}
              </div>
            )}
          </div>
          {/* 失败数（用户要求：只显示成功/失败统计，不展开失败详情） */}
          {!uploading && failCount > 0 && (
            <div className="mt-2 text-xs text-red-500 flex items-center gap-2">
              <AlertCircle size={12} />
              <span>{failCount} 个文件失败，未显示详情</span>
            </div>
          )}
        </div>

        {/* 文件选择区（非上传中显示） */}
        {!uploading && completedCount === 0 && (
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

        {/* 文件条目列表（上传中或完成后展示） */}
        {showReports && (
          <div className="space-y-2 mb-4">
            {/* 多文件时可整体折叠/展开 */}
            {isMulti && (
              <button
                onClick={() => setAggregatedExpanded(!aggregatedExpanded)}
                className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary"
              >
                {aggregatedExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <span>{aggregatedExpanded ? '收起详情' : '展开详情'}</span>
              </button>
            )}
            {(isMulti ? aggregatedExpanded : true) && (
              <div className={`space-y-2 ${isMulti ? 'max-h-64 overflow-y-auto' : ''}`}>
                {reports.map((r, idx) => {
                  const isRunning = r.status === 'running'
                  // 总耗时优先用后端真实值（覆盖 stageElapsed 之和，避免幽灵时间）
                  const totalMs = r.totalMs ?? (r.stageElapsed ? Object.values(r.stageElapsed).reduce((a, b) => a + b, 0) : 0)
                  const showExpanded = r.expanded && (r.status === 'success')
                  const currentStageIdx = r.currentStage ? STAGES.findIndex(x => x.key === r.currentStage) : -1
                  return (
                    <div key={idx} className={`rounded-lg px-3 py-2 ${r.status === 'failed' ? 'bg-red-50/50 border border-red-200' : 'bg-surface-elevated'}`}>
                      {/* 文件名 + 状态 + 总耗时 */}
                      <div
                        className={`flex items-center gap-2 text-xs ${r.status === 'success' ? 'cursor-pointer' : ''}`}
                        onClick={() => r.status === 'success' && toggleExpanded(idx)}
                      >
                        {r.status === 'duplicate' ? <CheckCircle2 size={13} className="text-amber-500 shrink-0" />
                          : r.status === 'success' ? (showExpanded ? <ChevronDown size={13} className="text-text-muted shrink-0" /> : <ChevronRight size={13} className="text-text-muted shrink-0" />)
                          : r.status === 'failed' ? <XCircle size={13} className="text-red-500 shrink-0" />
                          : <Loader2 size={13} className="text-accent animate-spin shrink-0" />}
                        <FileText size={13} className={`shrink-0 ${r.status === 'failed' ? 'text-red-400' : 'text-accent'}`} />
                        <span className={`truncate font-medium flex-1 ${r.status === 'failed' ? 'text-red-700' : r.status === 'duplicate' ? 'text-text-muted' : 'text-text-primary'}`}>{r.name}</span>
                        {r.status === 'duplicate' ? <span className="text-[10px] text-amber-500 shrink-0">已存在，跳过</span>
                          : r.status === 'failed' ? <span className="text-[10px] text-red-400 shrink-0">失败</span>
                          : r.status === 'running' && r.currentStage ? <span className="text-[10px] text-accent shrink-0">{r.currentStage}…</span>
                          : r.status === 'success' && <span className="text-text-muted text-[10px] tabular-nums shrink-0">{totalMs > 0 ? fmtMs(totalMs) : '<1ms'}</span>}
                      </div>
                      {/* 运行中阶段明细 */}
                      {isRunning && (
                        <div className="space-y-0.5 ml-6 mt-1.5">
                          {STAGES.map((s) => {
                            const thisIdx = STAGES.findIndex(x => x.key === s.key)
                            const isDone = thisIdx < currentStageIdx
                            const isCurrent = thisIdx === currentStageIdx
                            return (
                              <div key={s.key} className="flex items-center gap-2 text-xs">
                                {isDone ? <CheckCircle2 size={10} className="text-green-500 shrink-0" />
                                  : isCurrent ? <Loader2 size={10} className="text-accent animate-spin shrink-0" />
                                  : <div className="w-[10px] h-[10px] rounded-full border border-border-subtle shrink-0" />}
                                <span className={isDone || isCurrent ? 'text-text-primary' : 'text-text-muted'}>{s.label}</span>
                              </div>
                            )
                          })}
                        </div>
                      )}
                      {/* 完成后展开：阶段耗时 */}
                      {showExpanded && r.stageElapsed && Object.keys(r.stageElapsed).length > 1 && (
                        <div className="space-y-0.5 ml-6 mt-1.5">
                          {STAGES.filter(s => r.stageElapsed![s.key] !== undefined).map((s) => {
                            const ms = r.stageElapsed![s.key]
                            return (
                              <div key={s.key} className="flex items-center gap-2 text-xs">
                                <CheckCircle2 size={10} className="text-green-400 shrink-0" />
                                <span className="text-text-secondary text-[10px]">{s.label}</span>
                                <span className="text-text-muted text-[10px] ml-auto tabular-nums">{fmtMs(ms)}</span>
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
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          {completedCount > 0 && !uploading ? (
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