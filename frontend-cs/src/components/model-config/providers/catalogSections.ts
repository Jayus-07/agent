/** 上游模型清单的筛选与分组（纯函数，供「从目录选」使用；B4 拆分迁出）。 */
import type { ModelCatalogItem, ModelKind } from '@/types/modelConfig'

/** 搜索态每组上限；浏览态「当前用途」组与其余组各自的条数上限。
 *
 *  实测某厂商一次返回 **507** 个模型名 —— 一次铺出来等于让用户滚着找，所以浏览态
 *  只给「与当前用途一致」的那组 30 条，其余收进折叠区；一旦开始输入就转为跨全部
 *  分组搜索（这时才可能命中向量/重排模型）。
 */
export const CATALOG_SEARCH_LIMIT = 25
export const CATALOG_BROWSE_PRIMARY = 30
export const CATALOG_BROWSE_OTHER = 10

export const CATALOG_KIND_ORDER: ModelKind[] = ['chat', 'embedding', 'rerank', 'vision', 'speech']

export interface CatalogSection {
  kind: ModelKind
  /** 该组命中的总数（可能大于 `items.length`） */
  total: number
  /** 实际要渲染的条目 */
  items: ModelCatalogItem[]
  /** 是否为「当前用途」那一组 —— 组件据此决定默认展开 */
  primary: boolean
}

/** 按「与当前用途一致优先」分组，并做输入即筛。
 *
 *  主分组取**当前「模型用途」**而不是固定 chat：用户把用途改成 embedding 时他要找的
 *  就是向量模型，此时还先给一堆对话模型是反直觉的。
 */
export function catalogSections(
  items: ModelCatalogItem[],
  query: string,
  primaryKind: ModelKind,
): { sections: CatalogSection[]; matched: number; searching: boolean } {
  const q = query.trim().toLowerCase()
  const matched = q ? items.filter((item) => item.id.toLowerCase().includes(q)) : items
  const order = [primaryKind, ...CATALOG_KIND_ORDER.filter((kind) => kind !== primaryKind)]

  const sections: CatalogSection[] = []
  for (const kind of order) {
    const group = matched.filter((item) => item.kind === kind)
    if (!group.length) continue
    const primary = kind === primaryKind
    const limit = q
      ? CATALOG_SEARCH_LIMIT
      : (primary ? CATALOG_BROWSE_PRIMARY : CATALOG_BROWSE_OTHER)
    sections.push({ kind, total: group.length, items: group.slice(0, limit), primary })
  }
  return { sections, matched: matched.length, searching: Boolean(q) }
}

/** 该名字在上游目录里属于别的用途吗（**仅提示，不拦截**）。 */
export function catalogKindMismatch(
  name: string,
  kind: ModelKind,
  items: ModelCatalogItem[],
): ModelKind | null {
  const hit = items.find((item) => item.id === name)
  return hit && hit.kind !== kind ? hit.kind : null
}
