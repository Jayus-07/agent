'use client'

import { ArrowDown, CheckCircle2, Clock } from 'lucide-react'

const STAGES = [
  { name: '文件上传', desc: 'PDF/TXT/MD/DOCX', status: 'done', detail: '支持 4 种格式，自动检测编码' },
  { name: '文本解析', desc: 'Unstructured / PyPDF', status: 'done', detail: '提取纯文本，保留段落结构' },
  { name: '文本切片', desc: '类型感知分块', status: 'done', detail: '500 字符/块，50 字符重叠，按文档类型调整策略' },
  { name: 'Metadata 生成', desc: '关键词 + 实体提取', status: 'running', detail: 'jieba 分词 + 人名/品牌名提取 + 信号词规则匹配' },
  { name: 'Embedding', desc: 'BAAI/bge-small-zh-v1.5', status: 'pending', detail: '768 维向量，批量 32 条' },
  { name: '写入向量库', desc: 'Chroma', status: 'pending', detail: '增量索引，SHA256 diff 避免重复写入' },
]

export default function PipelinePage() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8">
        <div className="mb-8">
          <h1 className="text-lg font-semibold text-text-primary">文档解析流程</h1>
          <p className="text-xs text-text-muted mt-1">RAG Pipeline: 文件 → 文本 → 切片 → 向量 → 检索</p>
        </div>

        <div className="space-y-3">
          {STAGES.map((s, i) => (
            <div key={s.name}>
              <div className="flex items-start gap-3">
                <div className="mt-0.5">
                  {s.status === 'done' ? <CheckCircle2 size={18} className="text-green-500" />
                   : s.status === 'running' ? <Clock size={18} className="text-amber-500 animate-pulse" />
                   : <div className="w-[18px] h-[18px] rounded-full border-2 border-border-default" />}
                </div>
                <div className={`flex-1 rounded-xl p-4 border transition-colors ${s.status === 'running' ? 'bg-accent/4 border-accent/20' : 'bg-surface-base border-border-subtle'}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-medium text-text-primary">{s.name}</span>
                    <span className="text-xs text-text-muted">{s.desc}</span>
                  </div>
                  <div className="text-xs text-text-muted">{s.detail}</div>
                </div>
              </div>
              {i < STAGES.length - 1 && (
                <div className="flex justify-center py-1"><ArrowDown size={14} className="text-text-muted" /></div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
