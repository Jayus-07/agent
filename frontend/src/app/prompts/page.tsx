'use client'

import { useEffect, useState, useCallback, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { ScrollText, Search, Shield, ShieldAlert, ShieldCheck, ShieldQuestion, Lock, Database } from 'lucide-react'
import { promptsService, type PromptListItem } from '@/services/prompts'
import { useToast } from '@/components/shared/Toast'
import PageHeader from '@/components/layout/PageHeader'
import EmptyState from '@/components/shared/EmptyState'
import Skeleton from '@/components/shared/Skeleton'

const RISK_BADGE: Record<string, { label: string; cls: string; icon: typeof Shield }> = {
  critical: { label: 'CRITICAL', cls: 'bg-red-100 text-red-700', icon: ShieldAlert },
  high:     { label: 'HIGH',     cls: 'bg-orange-100 text-orange-700', icon: Shield },
  medium:   { label: 'MEDIUM',   cls: 'bg-yellow-100 text-yellow-700', icon: ShieldCheck },
  low:      { label: 'LOW',      cls: 'bg-green-100 text-green-700', icon: ShieldQuestion },
}

const CATEGORIES = ['all', 'rag', 'memory', 'sql', 'router', 'planner', 'selection', 'evaluation', 'competitor', 'business_report', 'capability', 'orchestration']

export default function PromptsPage() {
  const router = useRouter()
  const toast = useToast()
  const [items, setItems] = useState<PromptListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [riskFilter, setRiskFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await promptsService.list({
        category: categoryFilter !== 'all' ? categoryFilter : undefined,
        risk_level: riskFilter || undefined,
        q: search || undefined,
      })
      setItems(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [categoryFilter, riskFilter, search])

  useEffect(() => { load() }, [load])

  const handleSeed = async () => {
    try {
      const res = await promptsService.seed()
      toast.success(`已种子 ${res.seeded} 个默认 Prompt`)
      load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '种子失败')
    }
  }

  const filtered = useMemo(() => {
    if (!search && categoryFilter === 'all' && !riskFilter) return items
    return items
  }, [items, search, categoryFilter, riskFilter])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <PageHeader
          title="Prompt 管理"
          desc="集中管理所有 LLM 提示词模板，支持版本控制、审计追踪和热更新"
        />

        {/* 工具栏 */}
        <div className="flex flex-wrap items-center gap-3 mb-6">
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              placeholder="搜索 Prompt..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-full pl-9 pr-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
            />
          </div>
          <select
            value={categoryFilter}
            onChange={e => setCategoryFilter(e.target.value)}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
          >
            {CATEGORIES.map(c => (
              <option key={c} value={c}>{c === 'all' ? '全部分类' : c}</option>
            ))}
          </select>
          <select
            value={riskFilter}
            onChange={e => setRiskFilter(e.target.value)}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors"
          >
            <option value="">全部风险等级</option>
            <option value="critical">CRITICAL</option>
            <option value="high">HIGH</option>
            <option value="medium">MEDIUM</option>
            <option value="low">LOW</option>
          </select>
          <button
            onClick={handleSeed}
            className="ml-auto px-3 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5"
          >
            <Database size={14} />
            种子默认值
          </button>
        </div>

        {/* 内容 */}
        {loading ? (
          <Skeleton rows={8} />
        ) : error ? (
          <div className="text-center py-12">
            <p className="text-sm text-red-500 mb-3">{error}</p>
            <button onClick={load} className="text-xs text-accent hover:underline">重试</button>
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="暂无 Prompt"
            description="点击「种子默认值」初始化所有 Prompt 模板"
            icon={<ScrollText size={40} className="text-text-muted" />}
          />
        ) : (
          <div className="space-y-2">
            {filtered.map(item => {
              const risk = RISK_BADGE[item.risk_level] || RISK_BADGE.low
              const RiskIcon = risk.icon
              return (
                <div
                  key={item.key}
                  onClick={() => router.push(`/prompts/${encodeURIComponent(item.key)}`)}
                  className="bg-surface-base rounded-xl border border-border-subtle p-4 hover:shadow-card cursor-pointer transition-shadow"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-medium text-text-primary truncate">
                          {item.name}
                        </span>
                        {item.is_code_controlled && (
                          <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">
                            <Lock size={9} />
                            代码控制
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-[11px] text-text-muted">
                        <span className="font-mono">{item.key}</span>
                        <span>v{item.active_version ?? '—'}</span>
                        <span>{item.variable_count} 变量</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full font-medium ${risk.cls}`}>
                        <RiskIcon size={10} />
                        {risk.label}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-50 text-blue-600">
                        {item.category}
                      </span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
