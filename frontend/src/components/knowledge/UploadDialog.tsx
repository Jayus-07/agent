'use client'

import { useState, useRef } from 'react'
import { Upload, Loader2, CheckCircle2, XCircle, FileText } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'

interface Props { open: boolean; onClose: () => void; onSuccess: () => void }

// 阶段映射：后端 SSE event 名 → 显示文案 + UI 索引
const STAGES = [
  { key: 'uploading', label: '上传文件' },
  { key: 'parsing',   label: '文本解析' },
  { key: 'chunking',  label: 'Chunk 切分' },
  { key: 'embedding', label: 'Embedding' },
  { key: 'writing',   label: '写入向量库' },
  { key: 'done',      label: '完成' },
] as const

type StageKey = typeof STAGES[number]['key']

export default function UploadDialog({ open, onClose, onSuccess }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [currentStage, setCurrentStage] = useState<StageKey | null>(null)
  const [stageMessage, setStageMessage] = useState('')
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  if (!open) return null

  // 关闭弹窗时清空所有状态（否则重新打开时 error/currentStage 残留）
  // 用 useEffect 监听 open 变化是更稳的方案，但这里用 onClose wrapper 简化
  const handleClose = () => {
    setFile(null)
    setUploading(false)
    setError('')
    setCurrentStage(null)
    setStageMessage('')
    onClose()
  }

  const handleUpload = async () => {
    if (!file) return
    setUploading(true); setError(''); setCurrentStage('uploading')

    try {
      const res = await knowledgeService.uploadDocument(file, (stage, message) => {
        setCurrentStage(stage as StageKey)
        setStageMessage(message)
      })
      if (res.ok) {
        // 后端发 'done' 时 setCurrentStage('done') 已调用
        setTimeout(() => { onSuccess(); handleClose() }, 800)
      } else {
        setError(res.error || '上传失败')
        setCurrentStage(null)
      }
    } catch (e) {
      setError((e as Error).message || '网络错误')
      setCurrentStage(null)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
      <div className="bg-surface-base rounded-2xl border border-border-subtle shadow-xl w-[440px] p-6" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-text-primary mb-4">上传文档</h3>

        {!uploading && !error && (
          <div className="border-2 border-dashed border-border-subtle rounded-xl p-8 text-center">
            <input type="file" ref={fileRef} accept=".pdf,.md,.txt,.docx" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) setFile(f) }} />
            {file ? (
              <div className="flex items-center gap-2 justify-center text-sm text-text-primary">
                <FileText size={16} className="text-accent" /> {file.name}
              </div>
            ) : (
              <div className="cursor-pointer" onClick={() => fileRef.current?.click()}>
                <Upload size={32} className="mx-auto mb-2 text-text-muted" />
                <p className="text-xs text-text-muted">点击选择 PDF / MD / TXT / DOCX</p>
              </div>
            )}
          </div>
        )}

        {uploading && currentStage && (
          <div className="space-y-2 mb-4">
            {STAGES.map((s) => {
              const currentIdx = STAGES.findIndex((x) => x.key === currentStage)
              const thisIdx = STAGES.findIndex((x) => x.key === s.key)
              const isDone = thisIdx < currentIdx || currentStage === 'done'
              const isCurrent = thisIdx === currentIdx && currentStage !== 'done'
              return (
                <div key={s.key} className="flex items-center gap-2 text-xs">
                  {isDone ? <CheckCircle2 size={13} className="text-green-500" />
                    : isCurrent ? <Loader2 size={13} className="text-accent animate-spin" />
                    : <div className="w-[13px] h-[13px] rounded-full border border-border-subtle" />}
                  <span className={isDone || isCurrent ? 'text-text-primary' : 'text-text-muted'}>{s.label}</span>
                  {isCurrent && stageMessage && (
                    <span className="text-text-muted text-[10px] ml-1 truncate">— {stageMessage}</span>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 text-xs text-red-500 mb-4">
            <XCircle size={14} /> {error}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={handleClose} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">取消</button>
          <button onClick={handleUpload} disabled={!file || uploading}
            className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors">
            {uploading ? '上传中...' : '上传并索引'}
          </button>
        </div>
      </div>
    </div>
  )
}
