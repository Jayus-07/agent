'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { Tags, Search, Plus, Trash2, RefreshCw, Loader2, Pencil, Power, Upload, X, ChevronDown, Scale, ShieldCheck, BookOpen, ShoppingBag, Wallet, Boxes, Layers } from 'lucide-react'
import { keywordService, type KeywordRule } from '@/services/keyword'
import { useToast } from '@/components/shared/Toast'

const DOC_TYPE_CN: Record<string, string> = {
  compliance: '合规', policy: '制度', legal: '法律', financial: '财务',
  faq: 'FAQ', product_spec: '商品规格', sop: '操作流程', listing: '上架',
  ad_policy: '广告政策', training: '培训', security: '安全',
  customer_data: '客户数据', contract_template: '合同模板', general: '通用',
}
const docTypeCn = (t: string) => DOC_TYPE_CN[t] || t

/**
 * 展示层业务场景分组（两级折叠的一级）。
 * 仅影响前端展示：doc_type 数据、分类器计分、DB 结构均不变。
 * 未映射到的 doc_type 自动落入「其他」兜底分组，保证新增类型也能展示。
 */
const TYPE_CLUSTERS: { key: string; name: string; icon: typeof Scale; desc: string; types: string[] }[] = [
  { key: 'govern', name: '合规与法务', icon: Scale, desc: '合规要求、法律文书与合同模板', types: ['compliance', 'legal', 'contract_template'] },
  { key: 'privacy', name: '数据安全与隐私', icon: ShieldCheck, desc: '安全管控与客户/个人信息保护', types: ['security', 'customer_data'] },
  { key: 'process', name: '制度与流程', icon: BookOpen, desc: '内部制度、标准流程与培训', types: ['policy', 'sop', 'training'] },
  { key: 'ecom', name: '电商运营', icon: ShoppingBag, desc: '上架、广告投放、商品规格与售后问答', types: ['listing', 'ad_policy', 'product_spec', 'faq'] },
  { key: 'finance', name: '财务', icon: Wallet, desc: '财务、报销、预算与报表', types: ['financial'] },
  { key: 'general', name: '通用词', icon: Boxes, desc: '跨类型通用词（不参与类型分类计分）', types: ['general'] },
]

// 样式对齐 knowledge/documents 页
const INPUT_CLS = 'px-3 py-2 text-xs rounded-lg border border-border-subtle bg-surface-base text-text-primary outline-none hover:border-accent/40 transition-colors'
const BTN_PRIMARY = 'px-4 py-2 text-xs rounded-lg bg-accent text-white hover:bg-accent-hover transition-colors flex items-center gap-1.5 disabled:opacity-50'
const BTN_SECONDARY = 'px-4 py-2 text-xs rounded-lg border border-border-subtle text-text-secondary hover:text-text-primary transition-colors flex items-center gap-1.5 disabled:opacity-50'
const BTN_DANGER = 'px-4 py-2 text-xs rounded-lg bg-red-500 text-white hover:bg-red-600 transition-colors'
const CARD_CLS = 'bg-surface-base rounded-xl border border-border-subtle'

interface EditState {
  mode: 'create' | 'edit'
  keyword: string
  doc_type: string
  category: string
  weight: number
  enabled: number
}

interface TypeGroup {
  type: string
  items: KeywordRule[]
  enabledCount: number
}

interface Cluster {
  key: string
  name: string
  icon: typeof Scale
  desc: string
  groups: TypeGroup[]
  total: number
  enabledTotal: number
}

/** doc_type -> 所属业务场景 key（未映射返回 'other'） */
const clusterKeyOf = (t: string) => TYPE_CLUSTERS.find(c => c.types.includes(t))?.key ?? 'other'

export default function KeywordsPage() {
  const toast = useToast()
  const [items, setItems] = useState<KeywordRule[]>([])
  const [loading, setLoading] = useState(true)
  const [docTypes, setDocTypes] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])

  // 筛选（客户端过滤，数据一次性全量拉取后按类型分组）
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('')
  const [enabled, setEnabled] = useState('')

  // 两级折叠状态：一级=业务场景，二级=文档类型（默认全收起，首屏只显示 6 个场景行）
  const [expandedClusters, setExpandedClusters] = useState<string[]>([])
  const [expandedTypes, setExpandedTypes] = useState<string[]>([])

  const [editing, setEditing] = useState<EditState | null>(null)
  const [saving, setSaving] = useState(false)
  const [editOldKeyword, setEditOldKeyword] = useState('') // 编辑弹窗打开时的原始词，改词后需删旧行
  const [deleteTarget, setDeleteTarget] = useState<KeywordRule | null>(null)
  const [batchOpen, setBatchOpen] = useState(false)
  const [batchText, setBatchText] = useState('')
  const [batchDocType, setBatchDocType] = useState('general')
  const [batchWeight, setBatchWeight] = useState(5)

  const load = useCallback(async () => {
    setLoading(true)
    const r = await keywordService.list({})
    setItems(r.items || [])
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    keywordService.docTypes().then(r => setDocTypes(r.doc_types || []))
    keywordService.categories().then(r => setCategories(r.categories || []))
  }, [])

  // 客户端过滤 → 按 doc_type 分组 → 装配进业务场景大类（组内按权重降序）
  const clusters: Cluster[] = useMemo(() => {
    const kw = search.trim().toLowerCase()
    const filtered = items.filter(r =>
      (!kw || r.keyword.toLowerCase().includes(kw)) &&
      (!category || r.category === category) &&
      (enabled === '' || String(r.enabled) === enabled)
    )
    const byType = new Map<string, KeywordRule[]>()
    for (const r of filtered) {
      if (!byType.has(r.doc_type)) byType.set(r.doc_type, [])
      byType.get(r.doc_type)!.push(r)
    }
    const makeGroup = (type: string): TypeGroup | null => {
      const list = byType.get(type)
      if (!list?.length) return null
      return { type, items: [...list].sort((a, b) => b.weight - a.weight), enabledCount: list.filter(r => r.enabled).length }
    }
    const out: Cluster[] = TYPE_CLUSTERS.map(c => {
      const groups = c.types.map(makeGroup).filter(Boolean) as TypeGroup[]
      return {
        key: c.key, name: c.name, icon: c.icon, desc: c.desc, groups,
        total: groups.reduce((s, g) => s + g.items.length, 0),
        enabledTotal: groups.reduce((s, g) => s + g.enabledCount, 0),
      }
    })
    // 兜底：未映射到任何场景的 doc_type 归入「其他」
    const mapped = new Set(TYPE_CLUSTERS.flatMap(c => c.types))
    const otherGroups = Array.from(byType.keys()).filter(t => !mapped.has(t)).map(makeGroup).filter(Boolean) as TypeGroup[]
    if (otherGroups.length) {
      out.push({
        key: 'other', name: '其他', icon: Layers, desc: '未归入业务场景的文档类型',
        groups: otherGroups,
        total: otherGroups.reduce((s, g) => s + g.items.length, 0),
        enabledTotal: otherGroups.reduce((s, g) => s + g.enabledCount, 0),
      })
    }
    return out.filter(c => c.groups.length > 0)
  }, [items, search, category, enabled])

  // 搜索时自动展开所有命中的场景与类型；清空搜索后回到手动展开状态
  const searching = search.trim().length > 0
  const isClusterOpen = (key: string) => searching || expandedClusters.includes(key)
  const isTypeOpen = (type: string) => searching || expandedTypes.includes(type)

  const toggleCluster = (key: string) =>
    setExpandedClusters(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key])
  const toggleType = (type: string) =>
    setExpandedTypes(prev => prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type])

  const expandAll = () => {
    setExpandedClusters(clusters.map(c => c.key))
    setExpandedTypes(clusters.flatMap(c => c.groups.map(g => g.type)))
  }
  const collapseAll = () => { setExpandedClusters([]); setExpandedTypes([]) }

  const openCreate = (presetType?: string) => {
    setEditOldKeyword('')
    setEditing({ mode: 'create', keyword: '', doc_type: presetType || 'general', category: '', weight: 5, enabled: 1 })
  }
  const openEditWith = (r: KeywordRule) => {
    setEditOldKeyword(r.keyword)
    setEditing({ mode: 'edit', keyword: r.keyword, doc_type: r.doc_type, category: r.category || '', weight: r.weight, enabled: r.enabled })
  }

  /** 保存/导入后确保目标类型及其所属场景处于展开状态，便于立即看到结果 */
  const revealType = (type: string) => {
    setExpandedClusters(prev => { const k = clusterKeyOf(type); return prev.includes(k) ? prev : [...prev, k] })
    setExpandedTypes(prev => prev.includes(type) ? prev : [...prev, type])
  }

  /** 保存：编辑时若关键词文本被修改，upsert 新词后再删旧行（keyword 是主键） */
  const saveEdit = async () => {
    if (!editing) return
    const kw = editing.keyword.trim()
    if (!kw) { toast.error('关键词不能为空'); return }
    setSaving(true)
    const r = await keywordService.upsert({ keyword: kw, doc_type: editing.doc_type, category: editing.category.trim(), weight: editing.weight, enabled: editing.enabled })
    if (r.ok === false) { setSaving(false); toast.error('保存失败'); return }
    if (editOldKeyword && editOldKeyword !== kw) await keywordService.delete(editOldKeyword)
    setSaving(false)
    toast.success('已保存')
    setEditing(null)
    revealType(editing.doc_type)
    load()
  }

  const toggle = async (r: KeywordRule) => {
    const next = r.enabled ? 0 : 1
    const res = await keywordService.toggle(r.keyword, next)
    if (res.ok === false) { toast.error('操作失败'); return }
    toast.success(next ? `已启用「${r.keyword}」` : `已停用「${r.keyword}」`)
    load()
  }

  const confirmDelete = async () => {
    if (!deleteTarget) return
    const r = await keywordService.delete(deleteTarget.keyword)
    setDeleteTarget(null)
    if (r.ok === false) { toast.error('删除失败'); return }
    toast.success('已删除')
    load()
  }

  const importBatch = async () => {
    const lines = batchText.split('\n').map(l => l.trim()).filter(Boolean)
    if (!lines.length) { toast.error('请输入至少一行关键词'); return }
    const itemsToUpsert = lines.map(line => {
      // 支持 "关键词,doc_type,分类,权重" 或仅 "关键词"，分隔符支持中英文逗号/Tab
      const parts = line.split(/[,，\t]/).map(p => p.trim())
      return {
        keyword: parts[0],
        doc_type: parts[1] || batchDocType,
        category: parts[2] || '',
        weight: parts[3] ? (parseInt(parts[3], 10) || batchWeight) : batchWeight,
      }
    }).filter(it => it.keyword)
    setSaving(true)
    const r = await keywordService.batchUpsert(itemsToUpsert)
    setSaving(false)
    if (r.ok === false) { toast.error('批量导入失败'); return }
    toast.success(`已导入 ${itemsToUpsert.length} 条`)
    setBatchOpen(false)
    setBatchText('')
    revealType(batchDocType)
    load()
  }

  const resetFilters = () => { setSearch(''); setCategory(''); setEnabled('') }
  const totalFiltered = clusters.reduce((s, c) => s + c.total, 0)
  const totalTypes = clusters.reduce((s, c) => s + c.groups.length, 0)

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Tags size={20} className="text-accent" />词库管理</h1>
          <p className="text-sm text-text-secondary mt-1">
            按业务场景 → 文档类型两级分组，点击逐级展开；改动约 60 秒内热生效（无需重启服务）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className={BTN_SECONDARY} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新
          </button>
          <button onClick={() => setBatchOpen(true)} className={BTN_SECONDARY}>
            <Upload size={14} />批量导入
          </button>
          <button onClick={() => openCreate()} className={BTN_PRIMARY}>
            <Plus size={14} />新增关键词
          </button>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className={`${CARD_CLS} p-3 flex flex-wrap items-center gap-2`}>
        <div className="flex items-center gap-2 bg-surface-base rounded-lg border border-border-subtle px-3 py-2">
          <Search size={14} className="text-text-muted" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索关键词（自动展开命中项）..."
            className="bg-transparent outline-none text-xs text-text-primary w-56"
          />
        </div>
        <select value={category} onChange={e => setCategory(e.target.value)} className={INPUT_CLS}>
          <option value="">全部分类</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={enabled} onChange={e => setEnabled(e.target.value)} className={INPUT_CLS}>
          <option value="">全部状态</option>
          <option value="1">启用</option>
          <option value="0">停用</option>
        </select>
        <button onClick={resetFilters} className={BTN_SECONDARY}>重置</button>
        <div className="ml-auto flex items-center gap-2 text-xs text-text-secondary">
          {searching && <span>{totalTypes} 个类型命中 · 共 {totalFiltered} 条</span>}
          {!searching && clusters.length > 0 && (
            <>
              <button onClick={expandAll} className="px-2 py-1 rounded-md hover:bg-black/5 text-text-secondary hover:text-text-primary">展开全部</button>
              <button onClick={collapseAll} className="px-2 py-1 rounded-md hover:bg-black/5 text-text-secondary hover:text-text-primary">收起全部</button>
            </>
          )}
        </div>
      </div>

      {/* 两级分组：一级=业务场景（默认收起，首屏只有场景行） */}
      <div className="space-y-2">
        {loading ? (
          <div className={`${CARD_CLS} px-4 py-12 text-center text-xs text-text-secondary`}>
            <Loader2 size={16} className="inline-block animate-spin mr-2" />加载中…
          </div>
        ) : clusters.length === 0 ? (
          <div className={`${CARD_CLS} px-4 py-12 text-center text-xs text-text-secondary`}>
            暂无关键词，点击右上角「新增关键词」添加
          </div>
        ) : clusters.map(c => {
          const open = isClusterOpen(c.key)
          const Icon = c.icon
          return (
            <div key={c.key} className={`${CARD_CLS} overflow-hidden`}>
              {/* 一级：业务场景分组头 */}
              <button
                onClick={() => toggleCluster(c.key)}
                className="w-full flex items-center gap-3 px-4 py-3.5 text-left hover:bg-black/[0.015] transition-colors"
              >
                <ChevronDown size={15} className={`text-text-muted transition-transform ${open ? '' : '-rotate-90'}`} />
                <span className="w-7 h-7 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center shrink-0"><Icon size={14} /></span>
                <span className="text-sm font-medium text-text-primary">{c.name}</span>
                <span className="hidden md:inline text-xs text-text-secondary">{c.desc}</span>
                <span className="ml-auto text-xs text-text-secondary whitespace-nowrap">
                  {c.groups.length} 个类型 · {c.total} 词 · {c.enabledTotal} 启用
                </span>
              </button>

              {/* 二级：场景下的文档类型子分组 */}
              {open && (
                <div className="border-t border-border-subtle bg-black/[0.008] divide-y divide-border-subtle">
                  {c.groups.map(g => {
                    const tOpen = isTypeOpen(g.type)
                    return (
                      <div key={g.type}>
                        <div
                          onClick={() => toggleType(g.type)}
                          className="w-full flex items-center gap-3 px-4 py-2.5 pl-11 text-left cursor-pointer hover:bg-black/[0.015] transition-colors select-none"
                        >
                          <ChevronDown size={13} className={`text-text-muted transition-transform ${tOpen ? '' : '-rotate-90'}`} />
                          <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-600 text-xs font-medium">{docTypeCn(g.type)}</span>
                          <span className="text-xs text-text-muted">{g.type}</span>
                          <span className="text-xs text-text-secondary">
                            {g.items.length} 词 · {g.enabledCount} 启用 · 权重 {Math.max(...g.items.map(r => r.weight))}–{Math.min(...g.items.map(r => r.weight))}
                          </span>
                          <span className="ml-auto flex items-center gap-1">
                            <span
                              onClick={e => { e.stopPropagation(); openCreate(g.type) }}
                              className="p-1.5 rounded-md hover:bg-blue-50 text-text-secondary hover:text-blue-600" title="新增到此类型"
                            >
                              <Plus size={13} />
                            </span>
                          </span>
                        </div>

                        {/* 三级内容：该类型下的关键词表（仅展开时渲染） */}
                        {tOpen && (
                          <div className="overflow-x-auto bg-surface-base">
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-left text-text-secondary border-b border-border-subtle bg-black/[0.01]">
                                  <th className="px-4 py-2 font-medium">关键词</th>
                                  <th className="px-4 py-2 font-medium">业务分类</th>
                                  <th className="px-4 py-2 font-medium">权重</th>
                                  <th className="px-4 py-2 font-medium">来源</th>
                                  <th className="px-4 py-2 font-medium">状态</th>
                                  <th className="px-4 py-2 font-medium">更新时间</th>
                                  <th className="px-4 py-2 font-medium text-right">操作</th>
                                </tr>
                              </thead>
                              <tbody>
                                {g.items.map(r => (
                                  <tr key={r.id} className="border-b border-border-subtle last:border-b-0 hover:bg-black/[0.015]">
                                    <td className="px-4 py-2 font-medium text-text-primary">{r.keyword}</td>
                                    <td className="px-4 py-2 text-text-secondary">{r.category || '-'}</td>
                                    <td className="px-4 py-2">{r.weight}</td>
                                    <td className="px-4 py-2">
                                      <span className={`px-2 py-0.5 rounded-md ${r.source === 'manual' ? 'bg-purple-50 text-purple-600' : 'bg-gray-100 text-gray-500'}`}>
                                        {r.source === 'manual' ? '手动' : '种子'}
                                      </span>
                                    </td>
                                    <td className="px-4 py-2">
                                      <span className={`px-2 py-0.5 rounded-md ${r.enabled ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-400'}`}>
                                        {r.enabled ? '启用' : '停用'}
                                      </span>
                                    </td>
                                    <td className="px-4 py-2 text-text-secondary">{r.updated_at || '-'}</td>
                                    <td className="px-4 py-2 text-right whitespace-nowrap">
                                      <button onClick={() => openEditWith(r)} className="p-1.5 rounded-md hover:bg-black/5 text-text-secondary" title="编辑"><Pencil size={13} /></button>
                                      <button onClick={() => toggle(r)} className="p-1.5 rounded-md hover:bg-black/5 text-text-secondary" title={r.enabled ? '停用' : '启用'}><Power size={13} /></button>
                                      <button onClick={() => setDeleteTarget(r)} className="p-1.5 rounded-md hover:bg-red-50 text-red-500" title="删除"><Trash2 size={13} /></button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* 新增 / 编辑弹窗 */}
      {editing && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={() => !saving && setEditing(null)}>
          <div className={`${CARD_CLS} shadow-xl rounded-2xl w-full max-w-md p-5 space-y-4`} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">{editing.mode === 'create' ? '新增关键词' : '编辑关键词'}</h3>
              <button onClick={() => setEditing(null)} className="p-1 rounded-md hover:bg-black/5"><X size={16} /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-text-secondary">关键词 *</label>
                <input value={editing.keyword} onChange={e => setEditing({ ...editing, keyword: e.target.value })} className={`${INPUT_CLS} w-full mt-1`} placeholder="如：违约责任 / GDPR" />
              </div>
              <div>
                <label className="text-xs text-text-secondary">文档类型</label>
                <select value={editing.doc_type} onChange={e => setEditing({ ...editing, doc_type: e.target.value })} className={`${INPUT_CLS} w-full mt-1`}>
                  {/* 弹窗里的类型选项也按场景分组（optgroup），便于定位 */}
                  {TYPE_CLUSTERS.map(c => (
                    <optgroup key={c.key} label={c.name}>
                      {(docTypes.filter(t => c.types.includes(t))).map(t => <option key={t} value={t}>{docTypeCn(t)}（{t}）</option>)}
                    </optgroup>
                  ))}
                  {(() => {
                    const mapped = new Set(TYPE_CLUSTERS.flatMap(c => c.types))
                    const rest = docTypes.filter(t => !mapped.has(t))
                    return rest.length ? (
                      <optgroup label="其他">
                        {rest.map(t => <option key={t} value={t}>{docTypeCn(t)}（{t}）</option>)}
                      </optgroup>
                    ) : null
                  })()}
                  {!docTypes.includes(editing.doc_type) && <option value={editing.doc_type}>{editing.doc_type}</option>}
                </select>
              </div>
              <div>
                <label className="text-xs text-text-secondary">业务分类（可选）</label>
                <input value={editing.category} onChange={e => setEditing({ ...editing, category: e.target.value })} className={`${INPUT_CLS} w-full mt-1`} placeholder="如：客户服务" list="kw-categories" />
                <datalist id="kw-categories">{categories.map(c => <option key={c} value={c} />)}</datalist>
              </div>
              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="text-xs text-text-secondary">权重（1-20，越高越影响分类）</label>
                  <input type="number" min={1} max={20} value={editing.weight} onChange={e => setEditing({ ...editing, weight: parseInt(e.target.value, 10) || 1 })} className={`${INPUT_CLS} w-full mt-1`} />
                </div>
                <div className="flex-1">
                  <label className="text-xs text-text-secondary">状态</label>
                  <select value={editing.enabled} onChange={e => setEditing({ ...editing, enabled: parseInt(e.target.value, 10) })} className={`${INPUT_CLS} w-full mt-1`}>
                    <option value={1}>启用</option>
                    <option value={0}>停用</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setEditing(null)} className={BTN_SECONDARY} disabled={saving}>取消</button>
              <button onClick={saveEdit} className={BTN_PRIMARY} disabled={saving}>
                {saving && <Loader2 size={13} className="animate-spin" />}保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 批量导入弹窗 */}
      {batchOpen && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={() => !saving && setBatchOpen(false)}>
          <div className={`${CARD_CLS} shadow-xl rounded-2xl w-full max-w-lg p-5 space-y-4`} onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">批量导入关键词</h3>
              <button onClick={() => setBatchOpen(false)} className="p-1 rounded-md hover:bg-black/5"><X size={16} /></button>
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="text-xs text-text-secondary">默认文档类型</label>
                <select value={batchDocType} onChange={e => setBatchDocType(e.target.value)} className={`${INPUT_CLS} w-full mt-1`}>
                  {docTypes.map(t => <option key={t} value={t}>{docTypeCn(t)}（{t}）</option>)}
                </select>
              </div>
              <div className="flex-1">
                <label className="text-xs text-text-secondary">默认权重</label>
                <input type="number" min={1} max={20} value={batchWeight} onChange={e => setBatchWeight(parseInt(e.target.value, 10) || 1)} className={`${INPUT_CLS} w-full mt-1`} />
              </div>
            </div>
            <div>
              <label className="text-xs text-text-secondary">每行一条，格式：<code>关键词,文档类型,分类,权重</code>（后三项可省略）</label>
              <textarea value={batchText} onChange={e => setBatchText(e.target.value)} rows={8} className={`${INPUT_CLS} w-full mt-1 font-mono leading-relaxed`} placeholder={'违约责任,legal,法务,10\n数据安全法,compliance,,8\n退款流程'} />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button onClick={() => setBatchOpen(false)} className={BTN_SECONDARY} disabled={saving}>取消</button>
              <button onClick={importBatch} className={BTN_PRIMARY} disabled={saving}>
                {saving && <Loader2 size={13} className="animate-spin" />}导入
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认 */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4" onClick={() => setDeleteTarget(null)}>
          <div className={`${CARD_CLS} shadow-xl rounded-2xl w-full max-w-sm p-5 space-y-4`} onClick={e => e.stopPropagation()}>
            <h3 className="font-semibold text-sm">删除关键词</h3>
            <p className="text-xs text-text-secondary leading-relaxed">确认删除「{deleteTarget.keyword}」？该词将不再参与文档类型分类与检索关键词抽取。</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeleteTarget(null)} className={BTN_SECONDARY}>取消</button>
              <button onClick={confirmDelete} className={BTN_DANGER}>删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
