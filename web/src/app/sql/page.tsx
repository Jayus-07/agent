'use client'

import { useState, useEffect } from 'react'
import { Database, Loader2 } from 'lucide-react'
import { sendSQL } from '@/lib/api'

export default function SQLPage() {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleQuery() {
    if (!question.trim() || loading) return
    setLoading(true)
    setError('')
    setResult('')
    try {
      const res = await sendSQL(question)
      setResult(res.answer || JSON.stringify(res, null, 2))
    } catch (err: any) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-lg font-semibold mb-6 flex items-center gap-2">
        <Database size={20} /> SQL 安全查询
      </h1>

      <div className="flex gap-3 mb-6">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleQuery()}
          placeholder="输入自然语言查询，例如：查询所有项目预算"
          className="flex-1 bg-[#2f2f2f] border border-[#3f3f3f] rounded-lg px-4 py-2.5 text-sm text-[#ececec] placeholder-[#8e8e8e] outline-none focus:border-[#5f5f5f]"
        />
        <button
          onClick={handleQuery}
          disabled={loading || !question.trim()}
          className="px-5 py-2.5 bg-[#ececec] text-[#171717] rounded-lg text-sm font-medium hover:bg-white disabled:opacity-30 transition-all"
        >
          {loading ? <Loader2 size={16} className="animate-spin" /> : '查询'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-400 mb-4">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-[#1a1a1a] border border-[#3f3f3f] rounded-xl p-5">
          <pre className="text-sm text-[#ececec] whitespace-pre-wrap font-mono">{result}</pre>
        </div>
      )}
    </div>
  )
}
