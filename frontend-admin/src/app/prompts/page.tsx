'use client'

import { useEffect, useState, useCallback } from 'react'
import { Database, RefreshCw } from 'lucide-react'
import { promptsService, type PromptListItem } from '@/api/prompts'
import { PROMPT_GROUPS, WHITELIST_KEYS } from '@/config/promptGroups'
import PromptCard from '@/components/prompts/PromptCard'
import { AssetActionButton, AssetPageShell, AssetSection, AssetState } from '@/components/layout/AssetPageShell'
import ErrorState from '@/components/shared/ErrorState'
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
    <AssetPageShell
      title="Prompt CI/CD"
      desc="管理业务 Prompt 的版本流水线：草稿 → 测试 → 评估 → 发布"
      actions={
        <>
          <AssetActionButton icon={<RefreshCw size={13} className={loading ? 'animate-spin' : ''} />} onClick={() => void load()}>
            刷新
          </AssetActionButton>
          <AssetActionButton icon={<Database size={13} />} onClick={() => void handleSeed()}>
            种子默认值
          </AssetActionButton>
        </>
      }
    >

        {loading ? (
          <AssetState className="p-6 text-left">
            <Skeleton rows={6} />
          </AssetState>
        ) : error ? (
          <ErrorState title="Prompt 加载失败" message={error} onRetry={load} className="rounded-xl border border-border-subtle bg-surface-base shadow-card" />
        ) : (
          <div className="space-y-4">
            {PROMPT_GROUPS.map(group => {
              const Icon = group.icon
              const prompts = group.keys.map(k => map[k]).filter(Boolean)
              return (
                <AssetSection
                  key={group.id}
                  title={group.label}
                  meta={`${prompts.length} prompts`}
                  icon={<Icon size={15} className="text-accent" />}
                  bodyClassName="pt-3"
                >
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {prompts.map(p => (
                      <PromptCard key={p.key} prompt={p} group={group} />
                    ))}
                  </div>
                </AssetSection>
              )
            })}
          </div>
        )}
    </AssetPageShell>
  )
}
