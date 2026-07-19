'use client'

import { useState, useRef } from 'react'
import { flushSync } from 'react-dom'
import { useRouter } from 'next/navigation'
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

// 单文件上传结果（用于已完成列表）
interface CompletedFile {
  name: string
  ok: boolean
  duplicate?: boolean
  error?: string
}

export default function UploadDialog({ open, onClose, onSuccess }: Props) {
  const router = useRouter()
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [currentStage, setCurrentStage] = useState<StageKey | null>(null)
  const [stageMessage, setStageMessage] = useState('')
  const [currentIdx, setCurrentIdx] = useState(0)       // 当前正在上传的文件下标
  const [completed, setCompleted] = useState<CompletedFile[]>([])
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  if (!open) return null

  const handleClose = () => {
    setFiles([])
    setUploading(false)
    setError('')
    setCurrentStage(null)
    setStageMessage('')
    setCurrentIdx(0)
    setCompleted([])
    onClose()
  }

  const handleUpload = async () => {
    if (!files.length) return
    setUploading(true)
    setError('')
    setCompleted([])
    setCurrentIdx(0)

    let okCount = 0
    // 多文件上传生成批次号，后端写入操作日志关联同一批次
    const batchId = files.length > 1 ? crypto.randomUUID() : undefined
    // 串行上传：一次一个，本地 embedding 模型不并发
    for (let i = 0; i < files.length; i++) {
      const file = files[i]
      setCurrentIdx(i)
      setCurrentStage('uploading')
      setStageMessage('')
      try {
        const res = await knowledgeService.uploadDocument(file, (stage, message) => {
          flushSync(() => {
            setCurrentStage(stage as StageKey)
            setStageMessage(message)
          })
        }, batchId)
        if (res.ok) {
          okCount++
          setCompleted(prev => [...prev, { name: file.name, ok: true, duplicate: res.duplicate }])
          // 单文件上传成功后自动跳转到 trace 详情
          if (!isMulti && res.trace_id) {
            setTimeout(() => {
              handleClose()
              router.push(`/knowledge/operations/traces/${res.trace_id}`)
            }, 600)
          }
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
    // 全成功 → 1 秒后自动关窗；有失败 → 停留让用户查看
    if (okCount === files.length) {
      setTimeout(() => handleClose(), 1000)
    }
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
            <div className="border-2 border-dashed border-border-subtle rounded-xl p-6 text-center">
              <input type="file" ref={fileRef} multiple
                accept=".pdf,.md,.txt,.docx" className="hidden"
                onChange={e => { const fs = Array.from(e.target.files || []); if (fs.length) setFiles(fs) }} />
              <div className="cursor-pointer" onClick={() => fileRef.current?.click()}>
                <Upload size={28} className="mx-auto mb-2 text-text-muted" />
                <p className="text-xs text-text-muted">点击选择 PDF / MD / TXT / DOCX（可多选）</p>
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

            {/* 当前文件的 6 阶段进度条 */}
            {uploading && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 text-xs text-text-primary">
                  <FileText size={13} className="text-accent shrink-0" />
                  <span className="truncate">{files[currentIdx]?.name}</span>
                </div>
                {STAGES.map((s) => {
                  const currentIdxStage = STAGES.findIndex(x => x.key === currentStage)
                  const thisIdx = STAGES.findIndex(x => x.key === s.key)
                  const isDone = thisIdx < currentIdxStage || currentStage === 'done'
                  const isCurrent = thisIdx === currentIdxStage && currentStage !== 'done'
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

            {/* 已完成文件列表（多文件时） */}
            {isMulti && completed.length > 0 && (
              <div className="space-y-1 max-h-32 overflow-y-auto border-t border-border-subtle pt-2">
                {completed.map((c, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs px-1">
                    {c.ok ? <CheckCircle2 size={12} className="text-green-500 shrink-0" />
                      : <XCircle size={12} className="text-red-500 shrink-0" />}
                    <span className={`truncate flex-1 ${c.ok ? 'text-text-secondary' : 'text-red-500'}`}>{c.name}</span>
                    {c.duplicate && <span className="text-[10px] text-amber-500">已存在</span>}
                    {c.error && <span className="text-[10px] text-red-400 truncate max-w-[120px]">{c.error}</span>}
                  </div>
                ))}
              </div>
            )}

            {/* 完成汇总（非上传中） */}
            {!uploading && currentStage === 'done' && (
              <div className="text-xs text-text-secondary flex items-center gap-2">
                <CheckCircle2 size={14} className="text-green-500" />
                完成：成功 {okCount}{failCount > 0 && `，失败 ${failCount}`}
                {failCount === 0 && <span className="text-text-muted">，窗口即将关闭...</span>}
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={handleClose} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">
            {uploading ? '后台上传' : '取消'}
          </button>
          <button onClick={handleUpload} disabled={!files.length || uploading}
            className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors">
            {uploading ? '上传中...' : `上传并索引${files.length > 1 ? `（${files.length}）` : ''}`}
          </button>
        </div>
      </div>
    </div>
  )
}
