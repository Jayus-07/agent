/**
 * 数据查询 service（2026-10-01）
 *
 * 后端为 backend/app/api/routes/sql.py（前缀 /sql，经 BFF /api/sql 代理）：
 *  - getCatalog / browseTable → 管理端表浏览通道（服务端拼装只读 SQL，
 *    白名单 + SQLPolicyGuard + 只读连接池 + 列级脱敏）
 *  - runQuery → 复用 NL2SQL 结构化端点 POST /sql/query
 * 403 = 无 sql.read 权限（viewer）；503 = SQL_AGENT_ENABLED 关闭。
 */

import { fetchRaw } from '@/api/client'

export interface BrowseColumn {
  name: string
  comment: string
}

export interface BrowseTable {
  schema_name: string
  name: string
  qualified_name: string
  description: string
  columns: BrowseColumn[]
}

export interface TableCatalogResponse {
  tables: BrowseTable[]
}

export interface TableBrowseResponse {
  qualified_name: string
  status: string
  page: number
  page_size: number
  total: number
  columns: string[]
  rows: Record<string, unknown>[]
  elapsed_sec: number
  error: string | null
  error_type: string | null
}

export interface SqlQueryResponse {
  status: string
  answer: string
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  elapsed_sec: number
  error: string | null
  error_type: string | null
  /** 实际执行的 SQL（核对 NL2SQL 结果用） */
  sql: string | null
}

const BASE = '/api/sql'

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetchRaw(url, init)
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${url} 失败 (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`)
  }
  return res.json() as Promise<T>
}

export const dataQueryService = {
  /** 表目录（白名单全量，含可见列与业务说明） */
  getCatalog: () => api<TableCatalogResponse>(`${BASE}/tables`),

  /** 表分页浏览（确定性只读 SELECT） */
  browseTable: (qualifiedName: string, params: { page?: number; pageSize?: number; sort?: string; order?: 'asc' | 'desc' }) => {
    const q = new URLSearchParams({
      page: String(params.page ?? 1),
      page_size: String(params.pageSize ?? 20),
      order: params.order ?? 'asc',
    })
    if (params.sort) q.set('sort', params.sort)
    // 后端路由是两段式 /tables/{schema}/{table}；qualified_name 必须拆开
    const dot = qualifiedName.indexOf('.')
    if (dot <= 0 || dot === qualifiedName.length - 1) {
      return Promise.reject(new Error(`非法表标识: ${qualifiedName}`))
    }
    const schema = qualifiedName.slice(0, dot)
    const table = qualifiedName.slice(dot + 1)
    return api<TableBrowseResponse>(`${BASE}/tables/${schema}/${table}?${q}`)
  },

  /** 自然语言 → SQL 查询（NL2SQL） */
  runQuery: (question: string) =>
    api<SqlQueryResponse>(`${BASE}/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    }),
}
