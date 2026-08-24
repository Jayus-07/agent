'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import ReactMarkdown from 'react-markdown'
import { selectionDecisionApi, SelectionTaskDetail } from '@/services/selectionDecision'

export default function SelectionDecisionReportPage() {
  const params = useParams<{ id: string }>()
  const [task, setTask] = useState<SelectionTaskDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null
    const load = async () => {
      try {
        const t = await selectionDecisionApi.get(params.id)
        setTask(t)
        if (t.status === 'running' && !timer) {
          timer = setInterval(load, 3000)  // 运行中轮询
        }
      } catch (e) {
        setError(`加载失败: ${e}`)
      }
    }
    load()
    return () => { if (timer) clearInterval(timer) }
  }, [params.id])

  if (error) return <div className="p-6 text-red-600">{error}</div>
  if (!task) return <div className="p-6 text-gray-500">加载中…</div>
  if (task.status === 'running') {
    return <div className="p-6 text-gray-500">任务执行中，页面自动刷新…</div>
  }
  return (
    <div className="p-6 max-w-3xl">
      {task.status === 'failed' ? (
        <div className="text-red-600">任务失败：{task.error || '未知错误'}</div>
      ) : (
        <div className="prose prose-sm max-w-none">
          <ReactMarkdown>{task.report_md || '无报告内容'}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
