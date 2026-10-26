'use client'

import { useState } from 'react'
import { History, RotateCcw } from 'lucide-react'
import { rollbackConfigHistory } from '@/api/modelConfig'
import type { ConfigHistoryEntry } from '@/types/modelConfig'
import { redactForRole, roleLabel } from '@/types/modelConfig'
import { useToast } from '@/components/shared/Toast'

interface Props {
  items: ConfigHistoryEntry[]
  canAdmin: boolean
  onChanged: () => Promise<unknown>
}

const objectLabels: Record<ConfigHistoryEntry['object'], string> = {
  role: '角色模型', provider: '供应商配置',
  provider_credential: '供应商密钥', provider_network_scope: '网络访问范围',
  role_policy: '运行策略',
}

export default function ConfigHistoryTab({ items, canAdmin, onChanged }: Props) {
  const toast = useToast()
  const [busy, setBusy] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfigHistoryEntry | null>(null)
  const name = (item: ConfigHistoryEntry) => item.object === 'role' || item.object === 'role_policy' ? roleLabel(item.key) : item.key

  async function restore(item: ConfigHistoryEntry) {
    setBusy(item.id)
    try {
      await rollbackConfigHistory(item.id)
      toast.success('配置已恢复')
      setConfirm(null)
      await onChanged()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '恢复失败，请刷新后检查当前配置')
    } finally { setBusy(null) }
  }

  return <section className="rounded-xl border border-black/5 bg-white shadow-card">
    <div className="border-b border-slate-100 px-4 py-3">
      <h2 className="text-sm font-medium text-text-primary">变更历史</h2>
      <p className="mt-1 text-xs text-text-muted">查看谁修改了配置。恢复操作会新增一条记录，密钥无法从历史恢复。</p>
    </div>
    <div className="divide-y divide-slate-100">{items.map((item) => {
      const view = redactForRole(item, canAdmin)
      return <div key={item.id} className="grid gap-3 px-4 py-4 text-xs md:grid-cols-[1fr_180px_auto] md:items-center">
        <div>
          <div className="font-medium text-text-primary">{name(item)} · {objectLabels[item.object] || '配置变更'}</div>
          <div className="mt-1 break-words text-text-secondary">{item.object === 'role' ? `原模型：${item.oldValue || '默认或继承'} → 新模型：${item.newValue || '默认或继承'}` : item.object === 'provider' || item.object === 'role_policy' ? '配置已更新，展开详情查看修改前后的值' : view.text}</div>
          <details className="mt-2 text-text-muted"><summary className="cursor-pointer">查看详情</summary>
            <div className="mt-1 break-all">操作身份：{item.operator || '未记录'}<br />记录编号：{item.id}<br />配置标识：{item.object} / {item.key}</div>
            {(item.object === 'provider' || item.object === 'role_policy') && <div className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap break-all">{view.text}</div>}
            {view.redacted && <div className="mt-1">敏感值已脱敏</div>}
          </details>
        </div>
        <div className="text-text-secondary"><div>{item.operatorName || (item.operator.startsWith('user:') ? `未知用户（ID：${item.operator.slice(5)}）` : item.operator || '系统')}</div>
          <div className="mt-1 flex items-center gap-1 text-text-muted"><History size={12} />{new Date(item.at).toLocaleString('zh-CN')}</div>
        </div>
        {item.object === 'role' && item.key === 'embedding'
          ? <a href="/settings/models?tab=roles" className="text-accent">前往角色绑定恢复</a>
          : canAdmin && item.rollbackable
          ? <button disabled={busy !== null} onClick={() => setConfirm(item)} className="flex items-center gap-1 rounded-lg border border-black/10 px-3 py-2 text-accent disabled:opacity-40"><RotateCcw size={12} />恢复此配置</button>
          : <span className="text-text-muted">{item.rollbackable ? '仅管理员可恢复' : '不支持恢复'}</span>}
      </div>
    })}{!items.length && <div className="px-4 py-10 text-center text-sm text-text-muted">暂无变更记录</div>}</div>
    {confirm && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="恢复此配置">
      <div className="w-full max-w-md rounded-xl bg-white p-5 text-sm text-text-secondary">
        <h3 className="font-medium text-text-primary">恢复 {name(confirm)} 的历史配置？</h3>
        <p className="mt-3">将把当前配置替换为此记录变更前的值。期间其他人可能已修改配置，请先核对；后端仍会执行权限和兼容性检查。</p>
        <div className="mt-3 break-words rounded-lg bg-slate-50 p-3">将恢复为：{confirm.oldValue || '未单独配置（使用默认或继承规则）'}</div>
        <div className="mt-4 flex justify-end gap-2"><button disabled={busy !== null} onClick={() => setConfirm(null)} className="rounded-lg border px-3 py-2">取消</button>
          <button disabled={busy !== null} onClick={() => void restore(confirm)} className="rounded-lg bg-accent px-3 py-2 text-white disabled:opacity-40">{busy ? '恢复中…' : '确认恢复'}</button></div>
      </div>
    </div>}
  </section>
}
