'use client'

import { useState } from 'react'
import { Library, Loader2 } from 'lucide-react'
import { sendRAG } from '@/lib/api'

export default function RAGPage() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSearch() {
    if (!question.trim() || loading) return
    setLoading(true)
    setError('')
    setAnswer('')
    try {
      const res = await sendRAG(question, 'rag-page')
      setAnswer(res.answer || '无结果')
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-lg font-semibold mb-6 flex items-center gap-2">
        <Library size={20} /> 知识库检索
      </h1>

      <div className="flex gap-3 mb-6">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="输入检索问题，例如：微服务架构最佳实践"
          className="flex-1 bg-[#2f2f2f] border border-[#3f3f3f] rounded-lg px-4 py-2.5 text-sm text-[#ececec] placeholder-[#8e8e8e] outline-none focus:border-[#5f5f5f]"
        />
        <button
          onClick={handleSearch}
          disabled={loading || !question.trim()}
          className="px-5 py-2.5 bg-[#ececec] text-[#171717] rounded-lg text-sm font-medium hover:bg-white disabled:opacity-30 transition-all"
        >
          {loading ? <Loader2 size={16} className="animate-spin" /> : '检索'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400 mb-4">
          {error}
        </div>
      )}

      {answer && (
        <div className="bg-[#1a1a1a] border border-[#3f3f3f] rounded-xl p-5 text-sm text-[#ececec] whitespace-pre-wrap leading-relaxed">
          {answer}
        </div>
      )}
    </div>
  )
}
