'use client'

// cs-workstation-v2 P7：知识助手（/cs/knowledge）。
// 对齐原型 agent-workstation.html 四 Tab：
//   未解决问题（拒答/空结果聚合，点行展开原始查询变体，一键生成 FAQ 草稿
//   → ai.cs_faq status=draft 进既有草稿→发布机制）；
//   话术管理（个人/团队 Tab + 新增弹窗 + 启用开关，复用 P4 canned CRUD）；
//   知识检索（FAQ 只读命中 + 置信分）；
//   能力差距（静态清单 = TASK 第 11 节候选池展示，P9+ 未排期）。
import { Fragment, useCallback, useEffect, useState } from 'react'
import {
  createCanned,
  createFaqDraft,
  knowledgeSearch,
  listCanned,
  listUnresolved,
  updateCanned,
  type CannedItem,
  type KnowledgeSearchItem,
  type UnresolvedItem,
} from '@/cs2/workspace/api'

type Tab = 'unsolved' | 'canned' | 'search' | 'gaps'

// 能力差距清单（TASK 第 11 节候选池 P9+，与原型一致）
const GAP_LIST: Array<{
  question: string
  behavior: string
  missing: string
  priority: string
  priorityClass: string
}> = [
  { question: '买完降价了，退差价（价保）', behavior: '仅能答政策条文', missing: '比价 Tool + 部分退款', priority: '近期', priorityClass: 'bg-amber-50 text-amber-600' },
  { question: '注销账号 / 删除我的数据', behavior: '无流程', missing: '合规注销流程（红线级）', priority: '合规', priorityClass: 'bg-red-50 text-red-600' },
  { question: '催发货 / 催派送', behavior: '仅能查预计送达', missing: '催办动作 + 商家协同', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '优惠券能不能叠加 / 为什么没优惠', behavior: '知识答不了账户级事实', missing: '券校验 Tool（账户数据）', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '积分怎么用 / 等级权益', behavior: '无意图', missing: '积分/会员查询 Tool', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '支付失败 / 重复扣款', behavior: '无意图', missing: '支付域查询 + 退款联动', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '在途包裹改地址', behavior: '仅账户默认地址', missing: '物流商改址 API', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '帮我开票 / 修改发票抬头', behavior: '仅 FAQ 答复', missing: '开票 Tool + 抬头子表', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
  { question: '（发送破损照片）你看下', behavior: '消息拒收（仅文本）', missing: '消息多模态（前端 + 模型）', priority: '中期', priorityClass: 'bg-blue-50 text-blue-600' },
]

function fmtLastSeen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const yesterday = new Date(now.getTime() - 86400000).toDateString() === d.toDateString()
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return `今天 ${hhmm}`
  if (yesterday) return `昨天 ${hhmm}`
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function Cs2KnowledgePage() {
  const [tab, setTab] = useState<Tab>('unsolved')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // 未解决问题
  const [unresolved, setUnresolved] = useState<UnresolvedItem[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [draftDone, setDraftDone] = useState<Record<string, boolean>>({})

  // 话术管理
  const [canned, setCanned] = useState<CannedItem[]>([])
  const [cannedScope, setCannedScope] = useState<'personal' | 'team'>('personal')
  const [showAdd, setShowAdd] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')
  const [newCategory, setNewCategory] = useState('通用')

  // 知识检索
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<KnowledgeSearchItem[] | null>(null)

  const loadUnresolved = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listUnresolved(7)
      setUnresolved(data.items ?? [])
    } catch {
      setError('未解决问题加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadCanned = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setCanned(await listCanned())
    } catch {
      setError('话术加载失败，请重试')
    } finally {
      setLoading(false)
    }
  }, [])

  const switchTab = useCallback(
    (next: Tab) => {
      setTab(next)
      setNotice(null)
      setError(null)
      if (next === 'unsolved') void loadUnresolved()
      if (next === 'canned') void loadCanned()
    },
    [loadCanned, loadUnresolved],
  )

  useEffect(() => {
    void loadUnresolved()
  }, [loadUnresolved])

  const genDraft = useCallback(
    async (item: UnresolvedItem) => {
      setNotice(null)
      try {
        await createFaqDraft(
          item.question,
          Object.keys(item.variants ?? {}),
        )
        setDraftDone((prev) => ({ ...prev, [item.question]: true }))
        setNotice(`已生成 FAQ 草稿（${item.question.slice(0, 20)}），待管理端审核发布`)
      } catch {
        setNotice('草稿生成失败，请重试')
      }
    },
    [],
  )

  const addCanned = useCallback(async () => {
    if (!newTitle.trim() || !newContent.trim()) return
    setNotice(null)
    try {
      await createCanned({
        title: newTitle.trim(),
        content: newContent.trim(),
        category: newCategory.trim() || '通用',
        scope: cannedScope,
      })
      setNotice('话术已新增')
      setShowAdd(false)
      setNewTitle('')
      setNewContent('')
      setNewCategory('通用')
      await loadCanned()
    } catch {
      setNotice('新增失败：团队话术可能需要主管权限')
    }
  }, [cannedScope, loadCanned, newCategory, newContent, newTitle])

  const toggleCanned = useCallback(
    async (item: CannedItem) => {
      try {
        await updateCanned(item.id, { enabled: !item.enabled })
        await loadCanned()
      } catch {
        setNotice('更新失败：团队话术需主管权限')
      }
    },
    [loadCanned],
  )

  const doSearch = useCallback(async () => {
    if (!query.trim()) return
    setNotice(null)
    try {
      const data = await knowledgeSearch(query.trim())
      setResults(data.items ?? [])
    } catch {
      setNotice('检索失败，请重试')
    }
  }, [query])

  const scopeItems = canned.filter((item) => item.scope === cannedScope)

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <h1 className="text-sm font-semibold text-slate-900">知识助手</h1>
        <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">
          拒答 / 查询空结果 → FAQ 草稿 → 管理端审核
        </span>
        {notice && (
          <span className="ml-auto text-[11px] text-slate-500" role="status">
            {notice}
          </span>
        )}
      </div>

      <div className="flex flex-none gap-1 border-b border-slate-200 bg-white px-4 py-2">
        {(
          [
            { key: 'unsolved', label: '未解决问题' },
            { key: 'canned', label: '话术管理' },
            { key: 'search', label: '知识检索' },
            { key: 'gaps', label: '能力差距' },
          ] as Array<{ key: Tab; label: string }>
        ).map((item) => (
          <button
            key={item.key}
            type="button"
            className={
              tab === item.key
                ? 'rounded-full bg-indigo-600 px-2.5 py-1 text-[11px] text-white'
                : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-200'
            }
            onClick={() => switchTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {error && <p className="p-4 text-xs text-red-500">{error}</p>}
        {loading && <p className="p-4 text-xs text-slate-400">加载中…</p>}

        {tab === 'unsolved' && !loading && (
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-4 py-2 font-medium">查询原文</th>
                <th className="px-2 py-2 font-medium">命中（7日）</th>
                <th className="px-2 py-2 font-medium">最近时间</th>
                <th className="px-2 py-2 font-medium">来源</th>
                <th className="px-2 py-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {unresolved.length === 0 && (
                <tr>
                  <td className="px-4 py-3 text-slate-400" colSpan={5}>
                    近 7 天暂无未解决问题
                  </td>
                </tr>
              )}
              {unresolved.map((item) => (
                <Fragment key={item.question}>
                  <tr
                    className="cursor-pointer border-t border-slate-100 hover:bg-slate-50"
                    onClick={() =>
                      setExpanded(expanded === item.question ? null : item.question)
                    }
                  >
                    <td className="px-4 py-2 font-medium text-slate-900">
                      {item.question}
                    </td>
                    <td className="px-2 py-2 text-slate-600">{item.hits}</td>
                    <td className="px-2 py-2 text-slate-500">
                      {fmtLastSeen(item.last_seen)}
                    </td>
                    <td className="px-2 py-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] ${
                          item.source === 'rag_miss'
                            ? 'bg-red-50 text-red-600'
                            : 'bg-amber-50 text-amber-600'
                        }`}
                      >
                        {item.source_display}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      {draftDone[item.question] ? (
                        <span className="text-[11px] text-emerald-600">草稿已生成</span>
                      ) : (
                        <button
                          type="button"
                          className="rounded-lg bg-indigo-600 px-2 py-1 text-[11px] text-white hover:opacity-90"
                          onClick={(e) => {
                            e.stopPropagation()
                            void genDraft(item)
                          }}
                        >
                          生成FAQ草稿
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded === item.question && (
                    <tr className="border-t border-slate-100 bg-slate-50/60">
                      <td className="px-4 py-2 text-[11px] text-slate-600" colSpan={5}>
                        原始查询聚合：
                        {Object.entries(item.variants)
                          .map(([text, count]) => `${text} ×${count}`)
                          .join(' · ')}
                        {" —— 建议补充至 cs_faq"}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'canned' && !loading && (
          <div className="p-4">
            <div className="mb-3 flex items-center gap-2">
              <button
                type="button"
                className={
                  cannedScope === 'personal'
                    ? 'rounded-full bg-slate-700 px-2.5 py-1 text-[11px] text-white'
                    : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600'
                }
                onClick={() => setCannedScope('personal')}
              >
                个人话术（{canned.filter((c) => c.scope === 'personal').length}）
              </button>
              <button
                type="button"
                className={
                  cannedScope === 'team'
                    ? 'rounded-full bg-slate-700 px-2.5 py-1 text-[11px] text-white'
                    : 'rounded-full bg-slate-100 px-2.5 py-1 text-[11px] text-slate-600'
                }
                onClick={() => setCannedScope('team')}
              >
                团队话术（{canned.filter((c) => c.scope === 'team').length}）
              </button>
              <button
                type="button"
                className="ml-auto rounded-lg bg-indigo-600 px-3 py-1.5 text-[11px] text-white hover:opacity-90"
                onClick={() => setShowAdd((prev) => !prev)}
              >
                新增话术
              </button>
            </div>

            {showAdd && (
              <div className="mb-3 space-y-2 rounded-xl border border-slate-200 bg-white p-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    aria-label="话术标题"
                    placeholder="标题（必填）"
                    className="w-48 rounded border border-slate-200 px-2 py-1.5 text-xs"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                  />
                  <input
                    type="text"
                    aria-label="话术分类"
                    placeholder="分类（默认通用）"
                    className="w-32 rounded border border-slate-200 px-2 py-1.5 text-xs"
                    value={newCategory}
                    onChange={(e) => setNewCategory(e.target.value)}
                  />
                </div>
                <textarea
                  aria-label="话术内容"
                  placeholder="内容（必填）"
                  className="min-h-16 w-full resize-none rounded border border-slate-200 px-2 py-1.5 text-xs"
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                />
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="rounded-lg bg-indigo-600 px-3 py-1 text-[11px] text-white disabled:opacity-40"
                    disabled={!newTitle.trim() || !newContent.trim()}
                    onClick={() => void addCanned()}
                  >
                    保存到{cannedScope === 'personal' ? '个人' : '团队'}
                  </button>
                  <button
                    type="button"
                    className="rounded-lg border border-slate-200 px-3 py-1 text-[11px] text-slate-500"
                    onClick={() => setShowAdd(false)}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}

            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-2 py-2 font-medium">标题</th>
                  <th className="px-2 py-2 font-medium">内容</th>
                  <th className="px-2 py-2 font-medium">标签</th>
                  <th className="px-2 py-2 font-medium">启用</th>
                </tr>
              </thead>
              <tbody>
                {scopeItems.length === 0 && (
                  <tr>
                    <td className="px-2 py-3 text-slate-400" colSpan={4}>
                      暂无{cannedScope === 'personal' ? '个人' : '团队'}话术
                    </td>
                  </tr>
                )}
                {scopeItems.map((item) => (
                  <tr key={item.id} className="border-t border-slate-100">
                    <td className="px-2 py-2 font-medium text-slate-900">
                      {item.title}
                    </td>
                    <td className="max-w-96 truncate px-2 py-2 text-slate-500" title={item.content}>
                      {item.content}
                    </td>
                    <td className="px-2 py-2">
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500">
                        {item.category}
                      </span>
                    </td>
                    <td className="px-2 py-2">
                      <input
                        type="checkbox"
                        aria-label={`启用${item.title}`}
                        checked={item.enabled}
                        onChange={() => void toggleCanned(item)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'search' && (
          <div className="p-4">
            <div className="flex gap-2">
              <input
                type="text"
                aria-label="知识检索词"
                placeholder="输入问题检索知识库，例如：退款时效"
                className="w-96 rounded-lg border border-slate-200 px-3 py-2 text-xs outline-none focus:border-indigo-500"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void doSearch()
                }}
              />
              <button
                type="button"
                className="rounded-lg bg-indigo-600 px-3 py-2 text-xs text-white disabled:opacity-40"
                disabled={!query.trim()}
                onClick={() => void doSearch()}
              >
                检索
              </button>
            </div>
            {results === null && (
              <p className="mt-4 text-xs text-slate-400">输入检索词后回车</p>
            )}
            {results !== null && results.length === 0 && (
              <p className="mt-4 text-xs text-slate-400">无命中结果</p>
            )}
            {results !== null &&
              results.map((item) => (
                <div
                  key={item.question}
                  className="mb-2 rounded-xl border border-slate-200 bg-white p-3"
                >
                  <p className="flex items-center gap-2 text-xs font-medium text-slate-900">
                    {item.question}
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] ${
                        item.matched
                          ? 'bg-emerald-50 text-emerald-600'
                          : 'bg-slate-100 text-slate-500'
                      }`}
                    >
                      置信 {Math.round(item.score * 100)}%
                    </span>
                  </p>
                  <p className="mt-1 text-[11px] text-slate-600">{item.answer}</p>
                </div>
              ))}
          </div>
        )}

        {tab === 'gaps' && (
          <div className="p-4">
            <p className="mb-3 rounded-lg bg-slate-50 p-2.5 text-[11px] text-slate-500">
              用户可能问、但当前系统未覆盖的诉求（P9+ 候选池；真实行为 = 低置信兜底 → 转人工 → 本页沉淀）
            </p>
            <table className="w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-2 py-2 font-medium">用户可能问</th>
                  <th className="px-2 py-2 font-medium">当前行为</th>
                  <th className="px-2 py-2 font-medium">缺失能力</th>
                  <th className="px-2 py-2 font-medium">建议优先级</th>
                </tr>
              </thead>
              <tbody>
                {GAP_LIST.map((row) => (
                  <tr key={row.question} className="border-t border-slate-100">
                    <td className="px-2 py-2 font-medium text-slate-900">
                      {row.question}
                    </td>
                    <td className="px-2 py-2 text-slate-500">{row.behavior}</td>
                    <td className="px-2 py-2 text-slate-600">{row.missing}</td>
                    <td className="px-2 py-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-[10px] ${row.priorityClass}`}
                      >
                        {row.priority}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
