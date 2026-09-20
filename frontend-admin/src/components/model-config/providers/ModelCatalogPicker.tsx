/** 模型名输入 + 「从目录选」（B4 拆分迁出）。
 *
 *  两条硬约束：
 *
 *  1. **输入框永远可以自由手打**。大量中转站 / 编码套餐不实现 `/models`，目录会是空的；
 *     把这里做成强制下拉就等于让这些供应商没法用。
 *  2. **目录只是提示，不是保证**。上游目录可能列着该 Key 其实调不通的模型名（中转站
 *     惯用手法），也可能包含别的账号才有的模型。选中只代表「少打几个字」，**能不能用
 *     仍然只由「测试连接」的 L2 说了算**。
 */
import { useState } from 'react'
import { ListChecks } from 'lucide-react'
import { modelKindLabel, type ModelCatalogResponse, type ModelKind } from '@/types/modelConfig'
import ErrorNote from './ErrorNote'
import { catalogKindMismatch, catalogSections, type CatalogSection } from './catalogSections'

export default function ModelCatalogPicker({
  value,
  modelKind,
  disabled,
  catalog,
  loading,
  error,
  open,
  onToggle,
  onLoad,
  onChange,
}: {
  value: string
  modelKind: ModelKind
  disabled: boolean
  catalog: ModelCatalogResponse | null
  loading: boolean
  error: string | null
  open: boolean
  onToggle: (next: boolean) => void
  onLoad: () => void
  onChange: (next: string) => void
}) {
  const [filter, setFilter] = useState('')
  const { sections, matched, searching } = catalog
    ? catalogSections(catalog.items, filter, modelKind)
    : { sections: [] as CatalogSection[], matched: 0, searching: false }
  const mismatch = catalog ? catalogKindMismatch(value.trim(), modelKind, catalog.items) : null

  function toggle() {
    if (!open) {
      onToggle(true)
      setFilter('')
      // 每次打开都重新请求：服务端有短 TTL 缓存，重复打开不会真的反复打上游。
      onLoad()
    } else {
      onToggle(false)
    }
  }

  return (
    <div>
      <div className="flex items-end gap-2">
        <label className="block flex-1 text-xs text-text-secondary">模型名称
          <input
            data-testid="provider-model-name"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            className="mt-1 w-full rounded-lg border border-black/10 px-3 py-2 text-sm"
            placeholder="例如：deepseek-v4-pro、qwen3.7-plus"
            autoComplete="off"
          />
        </label>
        <button
          type="button"
          data-testid="model-catalog-toggle"
          aria-expanded={open}
          disabled={disabled}
          onClick={toggle}
          className="flex shrink-0 items-center gap-1 rounded-lg border border-black/10 px-2.5 py-2 text-[11px] text-text-secondary hover:bg-slate-50 disabled:opacity-50"
        >
          <ListChecks size={12} />{open ? '收起清单' : '从目录选'}
        </button>
      </div>

      {mismatch && (
        <div data-testid="model-kind-mismatch" className="mt-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
          「{value.trim()}」在上游目录里是{modelKindLabel(mismatch)}，与当前「模型用途」不一致 —— 测试多半会失败，请一并调整用途。
        </div>
      )}

      {open && (
        <div data-testid="model-catalog-panel" className="mt-2 rounded-lg border border-slate-200 bg-white p-2 shadow-sm">
          {loading && <div role="status" className="px-1 py-1.5 text-[11px] text-text-muted">正在读取上游模型清单…</div>}
          {!loading && error && <ErrorNote message={error} />}
          {!loading && !error && catalog && (
            <>
              <div className={`px-1 text-[11px] ${catalog.shape_ok === false ? 'text-amber-800' : 'text-text-secondary'}`}>
                {catalog.summary}
                {catalog.cached && <span className="ml-1 text-text-muted">（缓存）</span>}
              </div>

              {catalog.ok && catalog.items.length > 0 ? (
                <>
                  <input
                    autoFocus
                    data-testid="model-catalog-filter"
                    value={filter}
                    onChange={(event) => setFilter(event.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-black/10 px-2.5 py-1.5 font-mono text-[11px]"
                    placeholder="输入关键字筛选，例如 qwen / bge / embedding"
                    autoComplete="off"
                  />
                  <div className="mt-1 max-h-56 overflow-y-auto">
                    {sections.map((section) => (
                      <CatalogSectionList
                        key={section.kind}
                        section={section}
                        onPick={(name) => { onChange(name); onToggle(false) }}
                      />
                    ))}
                    {searching && matched === 0 && (
                      <div className="px-1 py-2 text-[11px] text-text-muted">没有匹配的模型名 —— 可以直接手打，清单不代表全部。</div>
                    )}
                  </div>
                </>
              ) : (
                <div className="mt-1 px-1 text-[11px] text-text-muted">
                  拿不到清单不影响使用，直接手打模型名即可 —— 「测试连接」照样能验证。
                </div>
              )}

              <div className="mt-1.5 border-t border-slate-100 px-1 pt-1.5 text-[10px] text-text-muted">
                清单由上游返回，可能包含该 Key 调不通、或只在别处可用的模型名；选中后仍需「测试连接」通过才能真正使用。
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** 清单里的一个用途分组。非「当前用途」组默认折叠，避免 507 个名字一次铺出来。 */
function CatalogSectionList({
  section,
  onPick,
}: {
  section: CatalogSection
  onPick: (name: string) => void
}) {
  const list = (
    <div className="mt-1 flex flex-wrap gap-1">
      {section.items.map((item) => (
        <button
          key={item.id}
          type="button"
          data-testid={`catalog-item-${item.id}`}
          onClick={() => onPick(item.id)}
          className="max-w-full truncate rounded border border-black/10 px-1.5 py-0.5 font-mono text-[10px] text-text-secondary hover:border-accent hover:bg-accent/5 hover:text-accent"
          title={item.id}
        >
          {item.id}
        </button>
      ))}
    </div>
  )

  if (section.primary) {
    return (
      <div data-testid={`catalog-section-${section.kind}`} className="px-1 py-1">
        <div className="text-[10px] text-text-muted">{modelKindLabel(section.kind)}（{section.total}）</div>
        {list}
        {section.total > section.items.length && (
          <div className="mt-1 text-[10px] text-text-muted">还有 {section.total - section.items.length} 个，输入关键字可继续收窄。</div>
        )}
      </div>
    )
  }

  return (
    <details data-testid={`catalog-section-${section.kind}`} className="px-1 py-1">
      <summary className="cursor-pointer select-none text-[10px] text-text-muted">
        其他用途 · {modelKindLabel(section.kind)}（{section.total}）
      </summary>
      {list}
    </details>
  )
}
