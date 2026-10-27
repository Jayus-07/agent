'use client'

/**
 * 能力包授权矩阵（Batch 2）。
 *
 * 行 = 能力包（成员为静态 capability 与动态 mcp.* 的统一清单），
 * 列 = 产品入口（主助手 / 客服）。授权立即生效：动态成员同步
 * ai.mcp_tool_grants（写调用出网前强一致复核），静态成员经快照进入
 * product_allows_capability 白名单（15s 收敛）。
 */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, CircleSlash, Loader2, Plus, RefreshCw } from 'lucide-react'
import { AssetActionButton, AssetSection } from '@/components/layout/AssetPageShell'
import { useToast } from '@/components/shared/Toast'
import { ApiError } from '@/api/client'
import {
  createCapabilityBundle, getCapabilityBundles, grantCapabilityBundle,
  updateCapabilityBundle,
  type CapabilityBundle,
} from '@/api/bundles'

const PRODUCTS = [
  { key: 'agent', label: '主助手' },
  { key: 'customer_service', label: '客服' },
] as const

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message
  return e instanceof Error ? e.message : '请求失败'
}

export default function BundleGrantMatrix() {
  const toast = useToast()
  const [bundles, setBundles] = useState<CapabilityBundle[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newCaps, setNewCaps] = useState('')

  const reload = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setBundles(await getCapabilityBundles())
    } catch (e) {
      setError(errMessage(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const toggleGrant = async (bundle: CapabilityBundle,
                             product: 'agent' | 'customer_service') => {
    const active = (bundle.granted_products ?? []).includes(product)
    setBusy(`grant:${bundle.id}:${product}`)
    try {
      await grantCapabilityBundle(bundle.id, product, active)
      toast.success(active
        ? `已撤销「${bundle.name}」的 ${product} 授权（写调用立即拦截）`
        : `已授权「${bundle.name}」给 ${product}`)
      await reload()
    } catch (e) {
      toast.error(`授权操作失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  const toggleEnabled = async (bundle: CapabilityBundle) => {
    setBusy(`enable:${bundle.id}`)
    try {
      await updateCapabilityBundle(bundle.id, { enabled: !bundle.enabled })
      toast.success(bundle.enabled ? '能力包已停用' : '能力包已启用')
      await reload()
    } catch (e) {
      toast.error(`操作失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  const submitCreate = async () => {
    if (!newName.trim()) { toast.error('能力包名不能为空'); return }
    setBusy('create')
    try {
      const caps = newCaps.split('\n').map((s) => s.trim().toLowerCase())
        .filter(Boolean)
      await createCapabilityBundle({
        name: newName.trim(), description: '', domain: 'general',
        capabilities: caps,
      })
      toast.success(`能力包已创建（${caps.length} 个成员）`)
      setCreating(false)
      setNewName('')
      setNewCaps('')
      await reload()
    } catch (e) {
      toast.error(`创建失败: ${errMessage(e)}`)
    } finally {
      setBusy('')
    }
  }

  return (
    <AssetSection
      title="能力包与 Agent 授权"
      meta="授权立即生效：动态成员同步工具 grants（写调用即时拦截），静态成员经快照白名单（最迟 15 秒收敛）"
      bodyClassName="p-0"
    >
      <div className="flex items-center justify-end gap-2 border-b border-border-subtle px-4 py-2">
        <AssetActionButton disabled={loading}
          onClick={() => void reload()}>
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          刷新
        </AssetActionButton>
        <AssetActionButton tone="primary"
          onClick={() => setCreating((v) => !v)}>
          <Plus size={13} /> 新建能力包
        </AssetActionButton>
      </div>
      {creating && (
        <div className="grid gap-3 border-b border-border-subtle p-4 text-[12px] md:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">能力包名（唯一）</span>
            <input value={newName} onChange={(e) => setNewName(e.target.value)}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5"
              placeholder="如 数据分析包" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-text-muted">成员（每行一个；静态 capability 或动态 mcp.*）</span>
            <textarea value={newCaps} onChange={(e) => setNewCaps(e.target.value)} rows={3}
              className="rounded border border-border-subtle bg-surface-base px-2 py-1.5 font-mono"
              placeholder={'sql.query\nmcp.rest_api__get_order'} />
          </label>
          <div className="flex gap-2 md:col-span-2">
            <AssetActionButton tone="primary" disabled={busy === 'create'}
              onClick={() => void submitCreate()}>
              {busy === 'create' ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
              创建
            </AssetActionButton>
            <AssetActionButton onClick={() => setCreating(false)}>取消</AssetActionButton>
          </div>
        </div>
      )}

      {error ? (
        <div className="p-4 text-[12px] text-red-600">{error}</div>
      ) : bundles.length === 0 ? (
        <div className="p-4 text-[12px] text-text-muted">
          尚无能力包。授权表为空时静态能力按既有域黑名单判定；创建并授权后切换为白名单。
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-border-subtle text-left text-[11px] text-text-muted">
                <th className="px-4 py-2 font-normal">能力包 / 成员</th>
                <th className="px-2 py-2 font-normal">状态</th>
                {PRODUCTS.map((p) => (
                  <th key={p.key} className="px-2 py-2 font-normal">{p.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bundles.map((b) => (
                <tr key={b.id} className="border-b border-border-subtle align-top">
                  <td className="px-4 py-2">
                    <div className="font-medium">{b.name}</div>
                    <div className="mt-0.5 font-mono text-[10px] text-text-muted">
                      {b.capabilities.length > 0
                        ? b.capabilities.join('、')
                        : '（无成员）'}
                    </div>
                  </td>
                  <td className="px-2 py-2">
                    <button disabled={busy === `enable:${b.id}`}
                      onClick={() => void toggleEnabled(b)}
                      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium ${
                        b.enabled
                          ? 'bg-emerald-100 text-emerald-700'
                          : 'bg-slate-100 text-slate-500'
                      }`}>
                      {busy === `enable:${b.id}`
                        ? <Loader2 size={10} className="animate-spin" />
                        : b.enabled
                          ? <CheckCircle2 size={10} />
                          : <CircleSlash size={10} />}
                      {b.enabled ? '启用' : '停用'}
                    </button>
                  </td>
                  {PRODUCTS.map((p) => {
                    const active = (b.granted_products ?? []).includes(p.key)
                    return (
                      <td key={p.key} className="px-2 py-2">
                        <button
                          disabled={busy === `grant:${b.id}:${p.key}`}
                          onClick={() => void toggleGrant(b, p.key)}
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                            active
                              ? 'bg-emerald-100 text-emerald-700'
                              : 'bg-slate-100 text-slate-500'
                          } ${busy === `grant:${b.id}:${p.key}` ? 'opacity-50' : ''}`}>
                          {active ? '已授权 ✓' : '未授权'}
                        </button>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </AssetSection>
  )
}
