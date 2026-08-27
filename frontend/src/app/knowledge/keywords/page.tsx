'use client'

import { useState, useEffect, useCallback } from 'react'
import { Tags, Search, Plus, Trash2, RefreshCw, Loader2, Pencil, Power, Upload, X, ChevronLeft, ChevronRight } from 'lucide-react'
import { keywordService, type KeywordRule } from '@/services/keyword'
import { useToast } from '@/components/shared/Toast'

const DOC_TYPE_CN: Record<string, string> = {
  compliance: '合规', policy: '制度', legal: '法律', financial: '财务',
  faq: 'FAQ', product_spec: '商品规格', sop: '操作流程', listing: '上架',
  ad_policy: '广告政策', training: '培训', security: '安全',
  customer_data: '客户数据', contract_template: '合同模板', general: '通用',
}
const docTypeCn = (t: string) => DOC_TYPE_CN[t] || t
const PAGE_SIZE = 20

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

export default function KeywordsPage() {
  const toast = useToast()
  const [items, setItems] = useState<KeywordRule[]>([])
  const [loading, setLoading] = useState(true)
  const [docTypes, setDocTypes] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])

  // 筛选
  const [search, setSearch] = useState('')
  const [docType, setDocType] = useState('')
  const [category, setCategory] = useState('')
  const [enabled, setEnabled] = useState('')
  const [page, setPage] = useState(1)

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
    const r = await keywordService.list({ doc_type: docType, category, search, enabled })
    setItems(r.items || [])
    setLoading(false)
  }, [docType, category, search, enabled])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    keywordService.docTypes().then(r => setDocTypes(r.doc_types || []))
    keywordService.categories().then(r => setCategories(r.categories || []))
  }, [])
  // 筛选变化回到第一页
  useEffect(() => { setPage(1) }, [docType, category, search, enabled])

  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const pageItems = items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  const openCreate = () => { setEditOldKeyword(''); setEditing({ mode: 'create', keyword: '', doc_type: docType || 'general', category: '', weight: 5, enabled: 1 }) }
  const openEditWith = (r: KeywordRule) => {
    setEditOldKeyword(r.keyword)
    setEditing({ mode: 'edit', keyword: r.keyword, doc_type: r.doc_type, category: r.category || '', weight: r.weight, enabled: r.enabled })
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
    load()
  }

  const resetFilters = () => { setSearch(''); setDocType(''); setCategory(''); setEnabled('') }

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2"><Tags size={20} className="text-accent" />词库管理</h1>
          <p className="text-sm text-text-secondary mt-1">
            维护文档类型识别与检索关键词，改动约 60 秒内热生效（无需重启服务）
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className={BTN_SECONDARY} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />刷新
          </button>
          <button onClick={() => { setBatchDocType(docType || 'general'); setBatchOpen(true) }} className={BTN_SECONDARY}>
            <Upload size={14} />批量导入
          </button>
          <button onClick={openCreate} className={BTN_PRIMARY}>
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
            placeholder="搜索关键词..."
            className="bg-transparent outline-none text-xs text-text-primary w-36"
          />
        </div>
        <select value={docType} onChange={e => setDocType(e.target.value)} className={INPUT_CLS}>
          <option value="">全部类型</option>
          {docTypes.map(t => <option key={t} value={t}>{docTypeCn(t)}（{t}）</option>)}
        </select>
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
        <span className="ml-auto text-xs text-text-secondary">共 {items.length} 条</span>
      </div>

      {/* 表格 */}
      <div className={`${CARD_CLS} overflow-hidden`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-text-secondary border-b border-border-subtle">
                <th className="px-4 py-2.5 font-medium">关键词</th>
                <th className="px-4 py-2.5 font-medium">文档类型</th>
                <th className="px-4 py-2.5 font-medium">业务分类</th>
                <th className="px-4 py-2.5 font-medium">权重</th>
                <th className="px-4 py-2.5 font-medium">来源</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
                <th className="px-4 py-2.5 font-medium">更新时间</th>
                <th className="px-4 py-2.5 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-text-secondary">
                  <Loader2 size={16} className="inline-block animate-spin mr-2" />加载中…
                </td></tr>
              ) : pageItems.length === 0 ? (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-text-secondary">暂无关键词，点击右上角「新增关键词」添加</td></tr>
              ) : pageItems.map(r => (
                <tr key={r.id} className="border-b border-border-subtle last:border-b-0 hover:bg-black/[0.015]">
                  <td className="px-4 py-2.5 font-medium text-text-primary">{r.keyword}</td>
                  <td className="px-4 py-2.5">
                    <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-600">{docTypeCn(r.doc_type)}</span>
                  </td>
                  <td className="px-4 py-2.5 text-text-secondary">{r.category || '-'}</td>
                  <td className="px-4 py-2.5">{r.weight}</td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 rounded-md ${r.source === 'manual' ? 'bg-purple-50 text-purple-600' : 'bg-gray-100 text-gray-500'}`}>
                      {r.source === 'manual' ? '手动' : '种子'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={`px-2 py-0.5 rounded-md ${r.enabled ? 'bg-green-50 text-green-600' : 'bg-gray-100 text-gray-400'}`}>
                      {r.enabled ? '启用' : '停用'}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-text-secondary">{r.updated_at || '-'}</td>
                  <td className="px-4 py-2.5 text-right whitespace-nowrap">
                    <button onClick={() => openEditWith(r)} className="p-1.5 rounded-md hover:bg-black/5 text-text-secondary" title="编辑"><Pencil size={13} /></button>
                    <button onClick={() => toggle(r)} className="p-1.5 rounded-md hover:bg-black/5 text-text-secondary" title={r.enabled ? '停用' : '启用'}><Power size={13} /></button>
                    <button onClick={() => setDeleteTarget(r)} className="p-1.5 rounded-md hover:bg-red-50 text-red-500" title="删除"><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {/* 分页 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-2.5 border-t border-border-subtle text-xs text-text-secondary">
            <span>第 {page} / {totalPages} 页</span>
            <div className="flex items-center gap-1">
              <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="p-1 rounded-md hover:bg-black/5 disabled:opacity-30"><ChevronLeft size={15} /></button>
              <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages} className="p-1 rounded-md hover:bg-black/5 disabled:opacity-30"><ChevronRight size={15} /></button>
            </div>
          </div>
        )}
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
                  {docTypes.map(t => <option key={t} value={t}>{docTypeCn(t)}（{t}）</option>)}
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
