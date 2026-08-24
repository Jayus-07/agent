'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { selectionDecisionApi, SelectionTask, TaskPayload } from '@/services/selectionDecision'

const PLATFORMS = [
  { key: 'jd', label: '京东' },
  { key: 'taobao', label: '淘宝' },
  { key: 'amazon', label: '亚马逊' },
]

const STATUS_LABEL: Record<string, { text: string; cls: string }> = {
  running: { text: '执行中', cls: 'bg-blue-100 text-blue-700' },
  success: { text: '完成', cls: 'bg-green-100 text-green-700' },
  partial: { text: '部分完成', cls: 'bg-yellow-100 text-yellow-700' },
  failed: { text: '失败', cls: 'bg-red-100 text-red-700' },
}

export default function SelectionDecisionPage() {
  const [category, setCategory] = useState('')
  const [platforms, setPlatforms] = useState<string[]>(['jd', 'amazon'])
  const [sellPrice, setSellPrice] = useState('129')
  const [unitCost, setUnitCost] = useState('45')
  const [panelSize, setPanelSize] = useState(7)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState('')
  const [tasks, setTasks] = useState<SelectionTask[]>([])

  const loadTasks = useCallback(async () => {
    try {
      const res = await selectionDecisionApi.list()
      setTasks(res.tasks)
    } catch (e) {
      setMessage(`加载任务列表失败: ${e}`)
    }
  }, [])

  useEffect(() => {
    loadTasks()
    const timer = setInterval(loadTasks, 3000)  // 轮询进度
    return () => clearInterval(timer)
  }, [loadTasks])

  const togglePlatform = (key: string) =>
    setPlatforms(p => p.includes(key) ? p.filter(x => x !== key) : [...p, key])

  const submit = async () => {
    if (!category.trim()) { setMessage('请填写品类关键词'); return }
    const payload: TaskPayload = {
      category: category.trim(),
      platforms,
      finance: { sell_price: Number(sellPrice), unit_cost: Number(unitCost) },
      panel_size: panelSize,
    }
    setSubmitting(true)
    try {
      const res = await selectionDecisionApi.submit(payload)
      setMessage(`任务已提交（${res.task_id}），后台执行中…`)
      loadTasks()
    } catch (e) {
      setMessage(`提交失败: ${e}`)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6 space-y-6 max-w-4xl">
      <h1 className="text-xl font-semibold">选品决策（Go/No-Go）</h1>
      <p className="text-sm text-gray-500">
        提交后异步执行：市场评估 → 差异化分析 → 财务测算 → AI 评审团 → 决策报告。
        请先在「竞品监控」添加候选商品 URL（Phase 1 数据源）。
      </p>

      <div className="border rounded-lg p-4 space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">品类关键词</label>
          <input className="border rounded px-3 py-2 w-64" value={category}
                 onChange={e => setCategory(e.target.value)} placeholder="例如：蓝牙耳机" />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">目标平台</label>
          <div className="flex gap-4">
            {PLATFORMS.map(p => (
              <label key={p.key} className="flex items-center gap-1 text-sm">
                <input type="checkbox" checked={platforms.includes(p.key)}
                       onChange={() => togglePlatform(p.key)} />
                {p.label}
              </label>
            ))}
          </div>
        </div>
        <div className="flex gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">预期售价</label>
            <input type="number" className="border rounded px-3 py-2 w-32" value={sellPrice}
                   onChange={e => setSellPrice(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">单件成本</label>
            <input type="number" className="border rounded px-3 py-2 w-32" value={unitCost}
                   onChange={e => setUnitCost(e.target.value)} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">评审团人数</label>
            <select className="border rounded px-3 py-2" value={panelSize}
                    onChange={e => setPanelSize(Number(e.target.value))}>
              <option value={3}>3 人</option>
              <option value={5}>5 人</option>
              <option value={7}>7 人</option>
            </select>
          </div>
        </div>
        <button onClick={submit} disabled={submitting}
                className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-50">
          {submitting ? '提交中…' : '提交决策任务'}
        </button>
        {message && <p className="text-sm text-gray-600">{message}</p>}
      </div>

      <div>
        <h2 className="font-medium mb-2">任务列表</h2>
        <table className="w-full text-sm border">
          <thead>
            <tr className="bg-gray-50 text-left">
              <th className="p-2 border-b">品类</th>
              <th className="p-2 border-b">提交时间</th>
              <th className="p-2 border-b">状态</th>
              <th className="p-2 border-b">决策</th>
              <th className="p-2 border-b">报告</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map(t => {
              const st = STATUS_LABEL[t.status] ?? { text: t.status, cls: 'bg-gray-100' }
              return (
                <tr key={t.id}>
                  <td className="p-2 border-b">{t.inputs?.category}</td>
                  <td className="p-2 border-b">{t.created_at}</td>
                  <td className="p-2 border-b">
                    <span className={`px-2 py-0.5 rounded text-xs ${st.cls}`}>{st.text}</span>
                  </td>
                  <td className="p-2 border-b">
                    {t.verdict === 'go' ? '🚀 Go' : t.verdict === 'no_go' ? '❌ No-Go' : '-'}
                  </td>
                  <td className="p-2 border-b">
                    {t.finished_at && (
                      <Link className="text-blue-600 underline" href={`/selection-decision/${t.id}`}>
                        查看
                      </Link>
                    )}
                  </td>
                </tr>
              )
            })}
            {tasks.length === 0 && (
              <tr><td colSpan={5} className="p-4 text-center text-gray-400">暂无任务</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
