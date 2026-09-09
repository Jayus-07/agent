'use client'

import { useEffect, useState, useCallback } from 'react'
import { Database } from 'lucide-react'
import { promptsService, type PromptListItem } from '@/services/prompts'
import { PROMPT_GROUPS, WHITELIST_KEYS } from '@/config/promptGroups'
import PromptCard from '@/components/prompts/PromptCard'
import PageHeader from '@/components/layout/PageHeader'
import Skeleton from '@/components/shared/Skeleton'
import { useToast } from '@/components/shared/Toast'

export default function PromptsPage() {
  const toast = useToast()
  const [map, setMap] = useState<Record<string, PromptListItem>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const items = await promptsService.list({ keys: WHITELIST_KEYS })
      const m: Record<string, PromptListItem> = {}
      for (const it of items) m[it.key] = it
      setMap(m)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

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

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-end justify-between mb-8">
          <PageHeader
            title="Prompt CI/CD"
            desc="管理业务 Prompt 的版本流水线：草稿 → 测试 → 评估 → 发布"
          />
          <button
            onClick={handleSeed}
            className="px-3 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5"
          >
            <Database size={14} />
            种子默认值
          </button>
        </div>

        {loading ? (
          <Skeleton rows={6} />
        ) : error ? (
          <div className="text-center py-12">
            <p className="text-sm text-red-500 mb-3">{error}</p>
            <button onClick={load} className="text-xs text-accent hover:underline">重试</button>
          </div>
        ) : (
          <div className="space-y-8">
            {PROMPT_GROUPS.map(group => {
              const Icon = group.icon
              const prompts = group.keys.map(k => map[k]).filter(Boolean)
              return (
                <section key={group.id}>
                  <div className="flex items-center gap-2 mb-3">
                    <div className="p-1.5 rounded-lg bg-accent/10 text-accent">
                      <Icon size={16} />
                    </div>
                    <h2 className="text-sm font-semibold text-text-primary">{group.label}</h2>
                    <span className="text-[10px] text-text-muted">{prompts.length} prompts</span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {prompts.map(p => (
                      <PromptCard key={p.key} prompt={p} group={group} />
                    ))}
                  </div>
                </section>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
