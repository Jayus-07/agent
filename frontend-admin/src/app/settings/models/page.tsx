'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Database, KeyRound, RefreshCw, Settings2, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import RoleGate from '@/components/auth/RoleGate'
import PageHeader from '@/components/layout/PageHeader'
import { atLeast } from '@/lib/auth'
import { getConfigDrift, listConfigHistory, listModelCatalog, listModelRoles, listProviderPresets, listProviders } from '@/api/modelConfig'
import RoleBindingsTab from '@/components/model-config/RoleBindingsTab'
import ProvidersTab from '@/components/model-config/ProvidersTab'
import ConfigHistoryTab from '@/components/model-config/ConfigHistoryTab'
import DriftTab from '@/components/model-config/DriftTab'
import PriceTab from '@/components/model-config/PriceTab'

type Tab = 'roles' | 'providers' | 'prices' | 'history' | 'drift'

const TABS: Array<{ id: Tab; label: string; icon: typeof Settings2 }> = [
  { id: 'roles', label: '角色绑定', icon: SlidersHorizontal },
  { id: 'providers', label: '供应商与密钥', icon: KeyRound },
  { id: 'prices', label: '模型价格', icon: Database },
  { id: 'history', label: '变更历史', icon: RefreshCw },
  { id: 'drift', label: '体检与漂移', icon: ShieldCheck },
]

function readTab(value: string | null): Tab {
  return TABS.some((tab) => tab.id === value) ? value as Tab : 'roles'
}

export default function ModelConfigPage() {
  const queryClient = useQueryClient()
  const canEditor = atLeast('editor')
  const canAdmin = atLeast('admin')
  const [tab, setTab] = useState<Tab>('roles')

  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get('tab')
    const parsed = readTab(initial)
    setTab(parsed === 'providers' && !canAdmin ? 'roles' : parsed)
  }, [canAdmin])

  function changeTab(next: Tab) {
    setTab(next)
    const params = new URLSearchParams(window.location.search)
    params.set('tab', next)
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`)
  }

  const roles = useQuery({ queryKey: ['model-config-roles'], queryFn: listModelRoles, enabled: canEditor })
  const catalog = useQuery({ queryKey: ['model-config-catalog'], queryFn: listModelCatalog, enabled: canEditor })
  const providers = useQuery({ queryKey: ['model-config-providers'], queryFn: listProviders, enabled: canEditor && canAdmin })
  // 预置端点目录：admin only（与 providers 同门槛）。请求失败不影响页面 ——
  // ProvidersTab 会降级为手填 Base URL，所以这里不把 isError 计入全局错误条。
  const presets = useQuery({ queryKey: ['model-config-provider-presets'], queryFn: listProviderPresets, enabled: canAdmin, staleTime: 5 * 60_000 })
  const history = useQuery({ queryKey: ['model-config-history'], queryFn: () => listConfigHistory(), enabled: canEditor && tab === 'history' })
  const drift = useQuery({ queryKey: ['model-config-drift'], queryFn: getConfigDrift, enabled: canEditor, refetchInterval: 60_000 })

  const driftItems = drift.data?.items ?? []
  const critical = driftItems.filter((item) => item.severity === 'critical').length
  const warns = driftItems.filter((item) => item.severity === 'warn').length
  const verified = (providers.data?.items ?? []).filter((provider) => provider.lastProbe?.ok).length
  const defaultModels = Object.fromEntries((catalog.data?.models ?? []).map((model) => [model.provider, model.name]))
  // 摘要卡随 tab 切换：每张卡都应描述当前 tab 正在看的东西，
  // 而不是把别的 tab 的指标顶成「—」占位（原实现 4 张卡全 tab 共用）。
  const unavailableRoles = (roles.data?.items ?? []).filter((role) => !role.available).length
  const roleCount = roles.data?.items.length ?? 0
  const modelCount = catalog.data?.models.length ?? 0
  const presetCount = presets.data?.items.length ?? 0
  const historyCount = history.data?.items.length ?? 0

  function summaryCards(current: Tab): Array<Parameters<typeof SummaryCard>[0]> {
    const driftCard = { icon: <AlertTriangle size={15} />, label: '严重漂移', value: String(critical), note: critical ? '需要处理' : '当前无严重项', tone: (critical ? 'bad' : 'good') as 'bad' | 'good' }
    const roleCard = { icon: <Settings2 size={15} />, label: '模型角色', value: String(roleCount), note: '当前注册角色' }
    const modelCard = { icon: <Database size={15} />, label: '登记模型', value: String(modelCount), note: '供应商目录' }
    if (current === 'roles') {
      return [
        roleCard,
        { icon: <CheckCircle2 size={15} />, label: '不可用角色', value: String(unavailableRoles), note: unavailableRoles ? '需处理绑定' : '全部可用', tone: (unavailableRoles ? 'bad' : 'good') as 'bad' | 'good' },
        modelCard,
        driftCard,
      ]
    }
    if (current === 'providers') {
      return [
        { icon: <KeyRound size={15} />, label: '供应商', value: canAdmin ? String(providers.data?.items.length ?? 0) : '—', note: canAdmin ? (providers.data?.source === 'db' ? '注册表已接通' : '代码层兜底') : '管理员可见' },
        { icon: <CheckCircle2 size={15} />, label: '已验证', value: canAdmin ? `${verified}/${providers.data?.items.length ?? 0}` : '—', note: canAdmin ? '最近一次探测通过' : '管理员可见', tone: (verified === (providers.data?.items.length ?? 0) && verified > 0 ? 'good' : 'neutral') as 'good' | 'neutral' },
        { icon: <Database size={15} />, label: '预置端点', value: canAdmin ? String(presetCount) : '—', note: '内置厂商目录' },
        driftCard,
      ]
    }
    if (current === 'drift') {
      return [
        { icon: <ShieldCheck size={15} />, label: '漂移项', value: String(driftItems.length), note: '本轮体检发现' },
        driftCard,
        { icon: <AlertTriangle size={15} />, label: '提醒项', value: String(warns), note: warns ? '建议关注' : '无提醒', tone: (warns ? 'neutral' : 'good') as 'good' | 'neutral' },
        modelCard,
      ]
    }
    if (current === 'history') {
      return [
        { icon: <RefreshCw size={15} />, label: '变更记录', value: history.isLoading ? '…' : String(historyCount), note: '全部可回滚' },
        roleCard,
        modelCard,
        driftCard,
      ]
    }
    // prices：PriceTab 自管数据，头部只给全局背景卡
    return [roleCard, modelCard, driftCard, { icon: <SlidersHorizontal size={15} />, label: '不可用角色', value: String(unavailableRoles), note: unavailableRoles ? '需处理绑定' : '全部可用', tone: (unavailableRoles ? 'bad' : 'good') as 'bad' | 'good' }]
  }

  const cards = summaryCards(tab)

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['model-config-roles'] }),
      queryClient.invalidateQueries({ queryKey: ['model-config-catalog'] }),
      queryClient.invalidateQueries({ queryKey: ['model-config-providers'] }),
      queryClient.invalidateQueries({ queryKey: ['model-config-history'] }),
      queryClient.invalidateQueries({ queryKey: ['model-config-drift'] }),
    ])
  }

  function renderContent() {
    if (tab === 'roles') return <RoleBindingsTab roles={roles.data?.items ?? []} catalog={catalog.data?.models ?? []} canAdmin={canAdmin} onSaved={refreshAll} />
    if (tab === 'providers' && canAdmin) return <ProvidersTab providers={providers.data?.items ?? []} defaultModels={defaultModels} source={providers.data?.source ?? 'builtin'} canAdmin={canAdmin} onChanged={refreshAll} plans={presets.data?.plans ?? []} presets={presets.data?.items ?? []} presetsLoading={presets.isLoading} />
    if (tab === 'prices') return <PriceTab />
    if (tab === 'history') return <ConfigHistoryTab items={history.data?.items ?? []} canAdmin={canAdmin} onChanged={refreshAll} />
    return <DriftTab items={driftItems} />
  }

  const visibleTabs = TABS.filter((item) => item.id !== 'providers' || canAdmin)
  return <RoleGate minRole="editor" pageName="模型与供应商"><div className="flex-1 overflow-y-auto"><div className="mx-auto max-w-7xl px-6 py-8"><PageHeader title="模型与供应商" desc="集中管理模型角色、供应商地址、托管密钥与价格治理；每次变更都可追溯。" />
    <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">{cards.map((card) => <SummaryCard key={card.label} {...card} />)}</div>
    {critical > 0 && <button onClick={() => changeTab('drift')} className="mb-5 flex w-full items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-left text-xs text-red-800"><AlertTriangle size={15} />存在 {critical} 项严重配置问题，点击查看处理建议。</button>}
    <div className="mb-5 flex gap-1 overflow-x-auto border-b border-slate-200">{visibleTabs.map(({ id, label, icon: Icon }) => <button key={id} onClick={() => changeTab(id)} className={`flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2.5 text-xs transition-colors ${tab === id ? 'border-accent text-accent' : 'border-transparent text-text-muted hover:text-text-primary'}`}>{<Icon size={14} />}{label}{id === 'prices' && <span className="rounded-full bg-amber-50 px-1.5 py-0.5 text-[9px] text-amber-700">需审核</span>}</button>)}</div>
    {!canAdmin && <div className="mb-5 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-xs text-blue-800">当前为只读模式。模型角色、供应商密钥、价格审核和历史回滚需要管理员权限。</div>}
    {roles.isLoading || providers.isLoading ? <LoadingState /> : renderContent()}
    {roles.isError || providers.isError || (tab === 'drift' && drift.isError) || (tab === 'history' && history.isError) ? <div className="mt-4 rounded-lg border border-red-100 bg-red-50 px-4 py-3 text-xs text-red-800">配置数据加载失败，请刷新页面或检查 APISIX / 记忆库状态。</div> : null}
    </div></div></RoleGate>
}

function SummaryCard({ icon, label, value, note, tone = 'neutral' }: { icon: React.ReactNode; label: string; value: string; note: string; tone?: 'neutral' | 'good' | 'bad' }) {
  return <div className="rounded-xl border border-black/5 bg-white p-4 shadow-card"><div className="flex items-center gap-2 text-[11px] text-text-muted">{icon}{label}</div><div className={`mt-2 font-mono text-xl font-semibold ${tone === 'good' ? 'text-emerald-700' : tone === 'bad' ? 'text-red-700' : 'text-text-primary'}`}>{value}</div><div className="mt-1 text-[10px] text-text-muted">{note}</div></div>
}

function LoadingState() {
  return <div className="space-y-3">{[1, 2, 3].map((item) => <div key={item} className="h-16 animate-pulse rounded-xl border border-slate-100 bg-slate-50" />)}</div>
}
