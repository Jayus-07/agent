'use client'

import { useState } from 'react'
import { History, RotateCcw, ShieldAlert } from 'lucide-react'
import { rollbackConfigHistory } from '@/api/modelConfig'
import type { ConfigHistoryEntry } from '@/types/modelConfig'
import { redactForRole } from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  items: ConfigHistoryEntry[]
  canAdmin: boolean
  onChanged: () => Promise<unknown>
}

export default function ConfigHistoryTab({ items, canAdmin, onChanged }: Props) {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)

  async function rollback(item: ConfigHistoryEntry) {
    if (!item.rollbackable) {
      toast.error('密钥历史不可回滚，请重新轮换密钥')
      return
    }
    if (!window.confirm(`确认把 ${item.key} 回滚到旧值？该变更会立即生效。`)) return
    setBusy(item.id)
    try {
      await rollbackConfigHistory(item.id)
      toast.success('配置已回滚')
      await onChanged()
    } catch (error) { toast.error(error instanceof Error ? error.message : '配置回滚失败') } finally { setBusy(null) }
  }

  return <section className="rounded-xl border border-black/5 bg-white shadow-card"><div className="border-b border-slate-100 px-4 py-3"><h2 className="text-xs font-medium text-text-primary">变更历史</h2><p className="mt-1 text-[11px] text-text-muted">密钥只显示指纹，历史记录不会保存明文或密文。</p></div><div className="divide-y divide-slate-100">{items.map((item) => { const view = redactForRole(item, canAdmin); return <div key={item.id} className="grid gap-3 px-4 py-3 text-xs md:grid-cols-[170px_1fr_140px_auto] md:items-center"><div><div className="flex items-center gap-1 text-text-primary"><History size={13} />{new Date(item.at).toLocaleString('zh-CN')}</div><div className="mt-1 font-mono text-[10px] text-text-muted">{item.operator}</div></div><div><div className="font-mono text-[11px] text-text-primary">{item.object} · {item.key}</div><div className="mt-1 text-text-secondary">{view.text}</div>{view.redacted && <div className="mt-1 flex items-center gap-1 text-[10px] text-amber-700"><ShieldAlert size={11} />敏感值已脱敏</div>}</div><div className="text-[10px] text-text-muted">{item.rollbackable ? '可回滚' : '不可回滚'}</div><button disabled={!canAdmin || !item.rollbackable || busy === item.id} onClick={() => { void rollback(item) }} title={!item.rollbackable ? '密钥历史不可回滚' : undefined} className="flex items-center justify-center gap-1 rounded-lg border border-black/10 px-2.5 py-1.5 text-[11px] text-accent disabled:cursor-not-allowed disabled:opacity-35"><RotateCcw size={12} />回滚</button></div>})}{!items.length && <div className="px-4 py-10 text-center text-xs text-text-muted">暂无变更记录</div>}</div></section>
}
