// Mock — Data Pipeline + Assets
// 迁移自 lib/mock-data-source.ts

export interface PipelineJob {
  id: string; name: string; inputRows: number; outputRows: number
  errors: number; quality: number; status: string; elapsed: string
  stages: { name: string; status: string; rows: number; removed?: number }[]
}

export interface DataAsset {
  id: string; name: string; source: string; rows: number; fields: number
  field_names?: string[]; quality: number; status: string; updatedAt: string
}

export const PIPELINE_JOBS: PipelineJob[] = [
  { id: 'p1', name: '商品数据清洗', inputRows: 100000, outputRows: 98500, errors: 1500, quality: 96, status: 'done', elapsed: '12s',
    stages: [{ name: '字段检测', status: 'done', rows: 100000 }, { name: '缺失值处理', status: 'done', rows: 98500 }, { name: '去重', status: 'done', rows: 98500 }, { name: '格式转换', status: 'done', rows: 98500 }, { name: '入库', status: 'done', rows: 98500 }] },
  { id: 'p2', name: '订单数据清洗', inputRows: 50000, outputRows: 49200, errors: 800, quality: 94, status: 'running', elapsed: '8s',
    stages: [{ name: '字段检测', status: 'done', rows: 50000 }, { name: '缺失值处理', status: 'done', rows: 49200 }, { name: '去重', status: 'running', rows: 0 }] },
  { id: 'p3', name: '评论数据清洗', inputRows: 200000, outputRows: 188000, errors: 12000, quality: 88, status: 'done', elapsed: '35s',
    stages: [{ name: '字段检测', status: 'done', rows: 200000 }, { name: '缺失值处理', status: 'done', rows: 188000 }, { name: '去重', status: 'done', rows: 188000 }, { name: '格式转换', status: 'done', rows: 188000 }, { name: '入库', status: 'done', rows: 188000 }] },
]

export const DATA_ASSETS: DataAsset[] = [
  { id: 'a1', name: 'stg_products', source: '数据采集中心', rows: 12, fields: 9, quality: 100, status: '就绪', updatedAt: '2026-07-14' },
  { id: 'a2', name: 'stg_orders', source: '数据采集中心', rows: 15, fields: 10, quality: 95, status: '就绪', updatedAt: '2026-07-14' },
  { id: 'a3', name: 'stg_shops', source: '数据采集中心', rows: 8, fields: 8, quality: 100, status: '就绪', updatedAt: '2026-07-14' },
  { id: 'a4', name: 'stg_inventory', source: '数据采集中心', rows: 12, fields: 8, quality: 90, status: '就绪', updatedAt: '2026-07-14' },
  { id: 'a5', name: 'stg_suppliers', source: '数据采集中心', rows: 10, fields: 10, quality: 100, status: '就绪', updatedAt: '2026-07-14' },
  { id: 'a6', name: 'cleaned_products', source: '数据处理中心', rows: 98500, fields: 12, quality: 96, status: '就绪', updatedAt: '2026-07-13' },
  { id: 'a7', name: 'cleaned_orders', source: '数据处理中心', rows: 49200, fields: 14, quality: 94, status: '就绪', updatedAt: '2026-07-13' },
]
