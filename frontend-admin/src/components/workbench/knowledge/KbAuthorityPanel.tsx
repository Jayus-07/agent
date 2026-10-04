'use client'

import { useEffect, useState } from 'react'
import { RefreshCw, Loader2, ShieldCheck } from 'lucide-react'
import { listKbAuthority, type KbAuthorityItem } from '@/api/knowledge'

/** audience / 授权语义徽章 */
const AUDIENCE_BADGE: Record<string, { label: string; className: string }> = {
  customer: { label: '对客', className: 'bg-blue-50 text-blue-600' },
  internal: { label: '内部', className: 'bg-amber-50 text-amber-700' },
  test: { label: '测试', className: 'bg-gray-100 text-gray-500' },
}

/**
 * S2 知识库授权盘点（admin）：全量 KB 注册口径（owner_depts/audience/文档计数）。
 * 只读盘点——可见性变更走 knowledge_base.py 注册表（代码评审随仓库发布），
 * 避免"运行时改授权口径造成审计断链"（企业治理：授权面必须可盘点、可追溯）。
 */
export default function KbAuthorityPanel() {
  const [items, setItems] = useState<KbAuthorityItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const data = await listKbAuthority()
      setItems(data.knowledge_bases || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [])

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <ShieldCheck className="w-4 h-4 text-emerald-600" />
          知识库授权口径盘点（owner_depts=可见部门，all=全员；变更走注册表代码评审）
        </div>
        <button
          onClick={() => { void load() }}
          disabled={loading}
          className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          刷新
        </button>
      </div>

      {error && <div className="text-sm text-red-600 bg-red-50 rounded-md px-3 py-2">{error}</div>}

      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-left">
            <tr>
              <th className="px-3 py-2">知识库</th>
              <th className="px-3 py-2">域</th>
              <th className="px-3 py-2">可见部门（owner_depts）</th>
              <th className="px-3 py-2">受众</th>
              <th className="px-3 py-2 text-right">文档数</th>
              <th className="px-3 py-2">状态</th>
            </tr>
          </thead>
          <tbody>
            {items.map((k) => {
              const badge = AUDIENCE_BADGE[k.audience] ?? AUDIENCE_BADGE.internal
              return (
                <tr key={k.kb_id} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-3 py-2">
                    <div className="font-medium text-gray-800">{k.name}</div>
                    <div className="text-xs text-gray-400 font-mono">{k.kb_id}</div>
                  </td>
                  <td className="px-3 py-2 text-gray-600">{k.domain}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {(k.owner_depts || []).map((d) => (
                        <span key={d} className="px-1.5 py-0.5 text-xs rounded bg-emerald-50 text-emerald-700">{d}</span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    <span className={`px-1.5 py-0.5 text-xs rounded ${badge.className}`}>{badge.label}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-gray-700">{k.doc_count}</td>
                  <td className="px-3 py-2">
                    {k.deprecated
                      ? <span className="px-1.5 py-0.5 text-xs rounded bg-red-50 text-red-600">已废弃{k.alias_for ? ` → ${k.alias_for}` : ''}</span>
                      : k.read_only
                        ? <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 text-gray-500">只读</span>
                        : <span className="px-1.5 py-0.5 text-xs rounded bg-green-50 text-green-600">在用</span>}
                  </td>
                </tr>
              )
            })}
            {!loading && items.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">暂无数据</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
