'use client'

import React, { useState, useMemo } from 'react'

export interface CorpusItem {
  id: string
  fileName: string
  category: 'rules' | 'contracts' | 'tables' | 'faq_sop' | 'robustness'
  categoryLabel: string
  fileType: 'pdf' | 'md' | 'docx' | 'xlsx' | 'csv' | 'txt'
  sizeKb: number
  description: string
  chunkCountEst: number
  qaCountEst: number
  status: 'active' | 'indexing' | 'ready'
}

/** 36 份基准评测语料元数据事实源 */
export const CORPUS_36_DATA: readonly CorpusItem[] = [
  // 业务制度
  { id: 'c1', fileName: 'TEST-CS-001_虚构客服退换与工单处理规则.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.3, description: '售后退换货时限与工单流转演练规则', chunkCountEst: 6, qaCountEst: 12, status: 'active' },
  { id: 'c2', fileName: 'TEST-FIN-001_虚构差旅与费用报销规则.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.2, description: '差旅住宿标准与票据审核红线', chunkCountEst: 5, qaCountEst: 10, status: 'active' },
  { id: 'c3', fileName: 'TEST-HR-001_虚构员工请假与加班规则.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.0, description: '年假折算、调休与加班审批规范', chunkCountEst: 4, qaCountEst: 8, status: 'active' },
  { id: 'c4', fileName: 'TEST-OPS-001_虚构订单库存与配送演练.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.1, description: '多仓调拨与超时赔付 SLA', chunkCountEst: 5, qaCountEst: 10, status: 'active' },
  { id: 'c5', fileName: 'TEST-PRODUCT-001_虚构商品价格与促销.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.0, description: '满减券与会员折上折叠加互斥规则', chunkCountEst: 4, qaCountEst: 8, status: 'active' },
  { id: 'c6', fileName: 'TEST-SQL-001_虚构数据字典指标与权限.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.3, description: '财务与运营表字段脱敏及访问权限', chunkCountEst: 6, qaCountEst: 12, status: 'active' },
  { id: 'c7', fileName: 'TEST-SUPPLIER-001_虚构供应商采购协议演练.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.2, description: '账期账单、供货违约金与验收规范', chunkCountEst: 5, qaCountEst: 10, status: 'active' },
  { id: 'c8', fileName: 'TEST-TRAVEL-001_虚构旅游产品与实时状态演练.md', category: 'rules', categoryLabel: '制度规则', fileType: 'md', sizeKb: 1.2, description: '线路库存退改签与极速出票策略', chunkCountEst: 5, qaCountEst: 10, status: 'active' },
  
  // 法律合同与专著
  { id: 'c9', fileName: 'contract_gf2017_0201_cn.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 988.5, description: '标准建设工程施工合同（GF-2017-0201）', chunkCountEst: 74, qaCountEst: 35, status: 'active' },
  { id: 'c10', fileName: 'doc_cn.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 2697.9, description: '中文综合长文档，多章节标题层级', chunkCountEst: 182, qaCountEst: 40, status: 'active' },
  { id: 'c11', fileName: 'progit_en_501p.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 6495.2, description: 'ProGit 英文原版 501 页，超长文档压力样本', chunkCountEst: 1240, qaCountEst: 60, status: 'active' },
  { id: 'c12', fileName: 'progit_zh.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 19659.6, description: 'ProGit 中文官方完整版，巨型跨度切片测试', chunkCountEst: 2860, qaCountEst: 80, status: 'active' },
  { id: 'c13', fileName: 'report.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 3480.3, description: '行业年度研究与趋势预测图文报告', chunkCountEst: 210, qaCountEst: 30, status: 'active' },
  { id: 'c14', fileName: 'staff_regulation_scnu.pdf', category: 'contracts', categoryLabel: '合同专著', fileType: 'pdf', sizeKb: 529.6, description: '教职工管理规章，条款结构紧密', chunkCountEst: 48, qaCountEst: 24, status: 'active' },

  // 结构化表格
  { id: 'c15', fileName: 'contract_table.docx', category: 'tables', categoryLabel: '表格结构', fileType: 'docx', sizeKb: 36.4, description: '嵌入多行多列复杂嵌套费率表格', chunkCountEst: 18, qaCountEst: 15, status: 'active' },
  { id: 'c16', fileName: 'multi_sheet.xlsx', category: 'tables', categoryLabel: '表格结构', fileType: 'xlsx', sizeKb: 8.9, description: '多标签页 Excel 关联数据账簿', chunkCountEst: 12, qaCountEst: 16, status: 'active' },
  { id: 'c17', fileName: 'sku.csv', category: 'tables', categoryLabel: '表格结构', fileType: 'csv', sizeKb: 16.0, description: '商品 SKU 基础属性对照表', chunkCountEst: 15, qaCountEst: 12, status: 'active' },
  { id: 'c18', fileName: 'sku_bom.csv', category: 'tables', categoryLabel: '表格结构', fileType: 'csv', sizeKb: 16.0, description: '物料 BOM 级联物料清单', chunkCountEst: 15, qaCountEst: 12, status: 'active' },
  { id: 'c19', fileName: 'sku_gbk.csv', category: 'tables', categoryLabel: '表格结构', fileType: 'csv', sizeKb: 15.0, description: 'GBK 特殊中文编码兼容性用例', chunkCountEst: 14, qaCountEst: 10, status: 'active' },

  // FAQ 与 SOP
  { id: 'c20', fileName: 'faq_only.docx', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'docx', sizeKb: 36.6, description: '高频一问一答规范格式（Word 版）', chunkCountEst: 22, qaCountEst: 22, status: 'active' },
  { id: 'c21', fileName: 'faq_only.md', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'md', sizeKb: 4.9, description: '高频一问一答规范格式（Markdown 版）', chunkCountEst: 16, qaCountEst: 22, status: 'active' },
  { id: 'c22', fileName: 'help_center_mixed.md', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'md', sizeKb: 10.1, description: '帮助中心混合样式，富含超链接与操作指引', chunkCountEst: 26, qaCountEst: 18, status: 'active' },
  { id: 'c23', fileName: 'long_sop.docx', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'docx', sizeKb: 37.2, description: '标准化作业程序长文档，含 12 步流水', chunkCountEst: 30, qaCountEst: 20, status: 'active' },
  { id: 'c24', fileName: 'interleaved.docx', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'docx', sizeKb: 36.5, description: '段落与要点交织混排布局', chunkCountEst: 20, qaCountEst: 14, status: 'active' },
  { id: 'c25', fileName: 'preamble.docx', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'docx', sizeKb: 36.5, description: '法律文档前置引言与主体释义', chunkCountEst: 15, qaCountEst: 10, status: 'active' },
  { id: 'c26', fileName: 'preamble.md', category: 'faq_sop', categoryLabel: 'FAQ & SOP', fileType: 'md', sizeKb: 1.6, description: 'Markdown 版本引言与前置约定', chunkCountEst: 8, qaCountEst: 8, status: 'active' },

  // 边界与鲁棒性对照
  { id: 'c27', fileName: 'base_v1.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.3, description: '基线版本原始条款对照', chunkCountEst: 14, qaCountEst: 10, status: 'active' },
  { id: 'c28', fileName: 'v2_revised_2024.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.4, description: '2024 修订版，微调 3 处核心参数', chunkCountEst: 14, qaCountEst: 12, status: 'active' },
  { id: 'c29', fileName: 'exact_copy.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.3, description: '完全重复副本，验证指纹去重与去燥', chunkCountEst: 14, qaCountEst: 10, status: 'active' },
  { id: 'c30', fileName: 'near_numbers.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.3, description: '相似度 99% 但篡改关键数值', chunkCountEst: 14, qaCountEst: 12, status: 'active' },
  { id: 'c31', fileName: 'near_reorder.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.3, description: '段落颠倒但句义相同，检验检索重排', chunkCountEst: 14, qaCountEst: 10, status: 'active' },
  { id: 'c32', fileName: 'near_typos.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 10.3, description: '含同音别字与拼写错误，容错测试', chunkCountEst: 14, qaCountEst: 10, status: 'active' },
  { id: 'c33', fileName: 'no_title.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 72.9, description: '无章节标题的纯大段文本', chunkCountEst: 64, qaCountEst: 15, status: 'active' },
  { id: 'c34', fileName: 'no_title_oneline.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 19.0, description: '极端单行无换行超长字符串', chunkCountEst: 18, qaCountEst: 8, status: 'active' },
  { id: 'c35', fileName: 'scanned_partial_fail.pdf', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'pdf', sizeKb: 2254.5, description: '局部模糊扫描件，OCR 容错质检', chunkCountEst: 36, qaCountEst: 12, status: 'active' },
  { id: 'c36', fileName: 'unrelated_control.txt', category: 'robustness', categoryLabel: '边界与鲁棒性', fileType: 'txt', sizeKb: 8.9, description: '无关控制组干扰对照样本', chunkCountEst: 10, qaCountEst: 6, status: 'active' },
]

export interface CorpusEvalCorrelatorProps {
  onLaunchEval?: (selectedIds: string[]) => void
}

export default function CorpusEvalCorrelator({ onLaunchEval }: CorpusEvalCorrelatorProps) {
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [activeTab, setActiveTab] = useState<string>('all')
  const [search, setSearch] = useState('')

  const categories = [
    { key: 'all', label: '全部语料 (36)' },
    { key: 'rules', label: '制度规则 (8)' },
    { key: 'contracts', label: '合同与长专著 (6)' },
    { key: 'tables', label: '结构化表格 (5)' },
    { key: 'faq_sop', label: 'FAQ与SOP (7)' },
    { key: 'robustness', label: '边界鲁棒性 (10)' },
  ]

  const filteredItems = useMemo(() => {
    return CORPUS_36_DATA.filter(item => {
      if (activeTab !== 'all' && item.category !== activeTab) return false
      if (search.trim() && !item.fileName.toLowerCase().includes(search.toLowerCase()) && !item.description.includes(search)) {
        return false
      }
      return true
    })
  }, [activeTab, search])

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
  }

  const toggleSelectAll = () => {
    if (selectedIds.length === filteredItems.length) {
      setSelectedIds([])
    } else {
      setSelectedIds(filteredItems.map(x => x.id))
    }
  }

  const totalChunks = useMemo(() => CORPUS_36_DATA.reduce((acc, cur) => acc + cur.chunkCountEst, 0), [])
  const totalQAs = useMemo(() => CORPUS_36_DATA.reduce((acc, cur) => acc + cur.qaCountEst, 0), [])

  return (
    <div className="space-y-4">
      {/* 语料资产看板顶栏 */}
      <div className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-xl border border-border-subtle bg-surface-base p-3.5 shadow-xs">
          <p className="text-xs text-text-muted">已入库基准语料</p>
          <p className="mt-1 text-xl font-bold tracking-tight text-text-primary">36 份</p>
          <p className="mt-0.5 text-[11px] text-emerald-600 dark:text-emerald-400">已就绪 · rag_eval_kb</p>
        </div>
        <div className="rounded-xl border border-border-subtle bg-surface-base p-3.5 shadow-xs">
          <p className="text-xs text-text-muted">分块切片总规模</p>
          <p className="mt-1 text-xl font-bold tracking-tight text-text-primary">{totalChunks.toLocaleString()}</p>
          <p className="mt-0.5 text-[11px] text-text-muted">覆盖单行~500页专著</p>
        </div>
        <div className="rounded-xl border border-border-subtle bg-surface-base p-3.5 shadow-xs">
          <p className="text-xs text-text-muted">关联评测用例集</p>
          <p className="mt-1 text-xl font-bold tracking-tight text-text-primary">{totalQAs} 条</p>
          <p className="mt-0.5 text-[11px] text-text-muted">含预期切片与参考答案</p>
        </div>
        <div className="rounded-xl border border-border-subtle bg-surface-base p-3.5 shadow-xs">
          <p className="text-xs text-text-muted">已勾选待测语料</p>
          <p className="mt-1 text-xl font-bold tracking-tight text-indigo-600 dark:text-indigo-400">
            {selectedIds.length} <span className="text-xs font-normal text-text-muted">/ 36 份</span>
          </p>
          <button
            type="button"
            disabled={selectedIds.length === 0}
            onClick={() => onLaunchEval?.(selectedIds)}
            className="mt-1 inline-flex w-full items-center justify-center rounded-md bg-indigo-600 px-2 py-1 text-xs font-medium text-white shadow-xs transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 dark:disabled:bg-slate-700"
          >
            基于所选发起评测
          </button>
        </div>
      </div>

      {/* 筛选与操作栏 */}
      <div className="flex flex-col gap-2 rounded-xl border border-border-subtle bg-surface-base p-3 shadow-xs sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-1">
          {categories.map(c => (
            <button
              key={c.key}
              type="button"
              onClick={() => setActiveTab(c.key)}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                activeTab === c.key
                  ? 'bg-indigo-600 font-medium text-white'
                  : 'bg-bg-elevated text-text-secondary hover:text-text-primary'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="搜索语料文件名或说明..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-48 rounded-md border border-border-subtle bg-bg-elevated px-2.5 py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-indigo-500 sm:w-64"
          />
          <button
            type="button"
            onClick={toggleSelectAll}
            className="shrink-0 rounded-md border border-border-subtle bg-surface-base px-2.5 py-1 text-xs font-medium text-text-secondary hover:bg-bg-hover"
          >
            {selectedIds.length === filteredItems.length && filteredItems.length > 0 ? '取消全选' : '全选本页'}
          </button>
        </div>
      </div>

      {/* 语料列表 */}
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
        {filteredItems.map(item => {
          const isSelected = selectedIds.includes(item.id)
          return (
            <div
              key={item.id}
              onClick={() => toggleSelect(item.id)}
              className={`cursor-pointer rounded-xl border p-3.5 transition-all ${
                isSelected
                  ? 'border-indigo-600 bg-indigo-50/20 shadow-xs dark:bg-indigo-950/20'
                  : 'border-border-subtle bg-surface-base hover:border-border-default hover:bg-bg-hover'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => {}} // 由父 div 点击接管
                    className="h-4 w-4 rounded border-border-default text-indigo-600 focus:ring-indigo-500"
                  />
                  <span className="rounded bg-bg-elevated px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase text-text-secondary">
                    {item.fileType}
                  </span>
                </div>
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                  已入库
                </span>
              </div>

              <h4 className="mt-2 text-xs font-semibold leading-snug text-text-primary line-clamp-1" title={item.fileName}>
                {item.fileName}
              </h4>
              <p className="mt-1 text-[11px] text-text-muted line-clamp-2">
                {item.description}
              </p>

              <div className="mt-3 flex items-center justify-between border-t border-border-subtle/60 pt-2 text-[10px] text-text-secondary">
                <span>切片: ~{item.chunkCountEst}</span>
                <span>QA用例: {item.qaCountEst}</span>
                <span>大小: {item.sizeKb} KB</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
