'use client'

import { useState, useRef } from 'react'
import { Upload, Loader2, CheckCircle2, XCircle, FileText } from 'lucide-react'
import { knowledgeService } from '@/services/knowledge'

interface Props { open: boolean; onClose: () => void; onSuccess: () => void }

const STAGES = ['上传文件', '文本解析', 'Chunk 切分', 'Embedding', '写入向量库', '完成']

export default function UploadDialog({ open, onClose, onSuccess }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [stage, setStage] = useState(0)
  const [error, setError] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  if (!open) return null

  const handleUpload = async () => {
    if (!file) return
    setUploading(true); setError(''); setStage(1)

    // Simulate stage progression (backend processes synchronously for now)
    const timer = setInterval(() => setStage(s => Math.min(s + 1, STAGES.length - 1)), 600)

    try {
      const res = await knowledgeService.uploadDocument(file)
      clearInterval(timer)
      if (res.ok) { setStage(STAGES.length - 1); setTimeout(() => { onSuccess(); onClose(); setFile(null); setStage(0) }, 800) }
      else { setStage(0); setError(res.error || '上传失败') }
    } catch { setStage(0); setError('网络错误') }
    finally { setUploading(false) }
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

        {uploading && (
          <div className="space-y-2 mb-4">
            {STAGES.map((s, i) => (
              <div key={s} className="flex items-center gap-2 text-xs">
                {i < stage ? <CheckCircle2 size={13} className="text-green-500" />
                  : i === stage ? <Loader2 size={13} className="text-accent animate-spin" />
                  : <div className="w-[13px] h-[13px] rounded-full border border-border-subtle" />}
                <span className={i <= stage ? 'text-text-primary' : 'text-text-muted'}>{s}</span>
              </div>
            ))}
          </div>
        )}

        {error && (
          <div className="flex items-center gap-2 text-xs text-red-500 mb-4">
            <XCircle size={14} /> {error}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors">取消</button>
          <button onClick={handleUpload} disabled={!file || uploading}
            className="px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover disabled:opacity-40 transition-colors">
            {uploading ? '上传中...' : '上传并索引'}
          </button>
        </div>
      </div>
    </div>
  )
}
