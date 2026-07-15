// Mock — RAG Knowledge Center
// 迁移自 lib/mock-knowledge.ts

export interface KbDoc {
  id: string; name: string; type: 'pdf' | 'md' | 'txt' | 'docx'
  size: number; chunks: number; status: 'done' | 'processing' | 'error'; updatedAt: string
}
export interface KbChunk { id: string; docId: string; seq: number; content: string; tokenCount: number; metadata: Record<string, string> }

export const KB_STATS = { kbCount: 3, docCount: 68, chunkCount: 320, embeddingModel: 'BAAI/bge-small-zh-v1.5', vectorDb: 'Chroma' }

export const KB_DOCS: KbDoc[] = [
  { id: 'd1', name: '商品运营规则.md', type: 'md', size: 45, chunks: 35, status: 'done', updatedAt: '2026-07-10' },
  { id: 'd2', name: '供应链管理手册.pdf', type: 'pdf', size: 820, chunks: 50, status: 'done', updatedAt: '2026-07-09' },
  { id: 'd3', name: 'FBA发货SOP.docx', type: 'docx', size: 156, chunks: 28, status: 'done', updatedAt: '2026-07-08' },
  { id: 'd4', name: 'Listing优化指南.md', type: 'md', size: 62, chunks: 42, status: 'done', updatedAt: '2026-07-07' },
  { id: 'd5', name: '售后处理流程.txt', type: 'txt', size: 12, chunks: 8, status: 'done', updatedAt: '2026-07-06' },
  { id: 'd6', name: '广告投放策略.pdf', type: 'pdf', size: 340, chunks: 55, status: 'processing', updatedAt: '2026-07-13' },
  { id: 'd7', name: '库存管理规范.md', type: 'md', size: 38, chunks: 30, status: 'done', updatedAt: '2026-07-05' },
  { id: 'd8', name: '供应商评估标准.docx', type: 'docx', size: 95, chunks: 25, status: 'done', updatedAt: '2026-07-04' },
  { id: 'd9', name: '客户服务FAQ.md', type: 'md', size: 28, chunks: 22, status: 'done', updatedAt: '2026-07-03' },
  { id: 'd10', name: '电商税务合规.pdf', type: 'pdf', size: 520, chunks: 25, status: 'error', updatedAt: '2026-07-02' },
]

export const KB_CHUNKS: KbChunk[] = [
  { id: 'c1', docId: 'd1', seq: 23, content: '商品库存低于安全库存时，需在24小时内发起补货流程。补货量 = 安全库存 × 2 - 当前库存。', tokenCount: 42, metadata: { doc_type: 'operation', source: '商品运营规则.md', category: '库存' } },
  { id: 'c2', docId: 'd1', seq: 5, content: '新品上架前需完成：标题优化、五点描述、A+页面设计、关键词研究四项工作。', tokenCount: 35, metadata: { doc_type: 'operation', source: '商品运营规则.md', category: '上架' } },
  { id: 'c3', docId: 'd3', seq: 12, content: 'FBA发货标准流程：创建发货计划 → 打印箱标 → 装箱 → 预约入仓 → 跟踪上架。', tokenCount: 38, metadata: { doc_type: 'sop', source: 'FBA发货SOP.docx', category: '物流' } },
  { id: 'c4', docId: 'd4', seq: 8, content: 'Listing标题公式：品牌 + 核心关键词 + 特性 + 规格 + 适用场景，控制在200字符以内。', tokenCount: 30, metadata: { doc_type: 'guide', source: 'Listing优化指南.md', category: '上架' } },
  { id: 'c5', docId: 'd6', seq: 15, content: 'ACoS（广告销售成本）= 广告花费/广告销售额×100%。健康ACoS应在15%-25%之间。', tokenCount: 36, metadata: { doc_type: 'strategy', source: '广告投放策略.pdf', category: '广告' } },
  { id: 'c6', docId: 'd7', seq: 3, content: '叶菜类生鲜商品建议当天销售完毕，次日需降价30%处理。根茎类可保存3-5天。', tokenCount: 35, metadata: { doc_type: 'operation', source: '库存管理规范.md', category: '生鲜' } },
  { id: 'c7', docId: 'd8', seq: 7, content: '供应商年度评审标准：不良率<1%为A级，1%-3%为B级，>3%触发质量审查。', tokenCount: 28, metadata: { doc_type: 'standard', source: '供应商评估标准.docx', category: '供应商' } },
  { id: 'c8', docId: 'd9', seq: 18, content: '客户退换货政策：7天内无条件退货，30天内质量问题换货。退货需保留原包装。', tokenCount: 32, metadata: { doc_type: 'faq', source: '客户服务FAQ.md', category: '售后' } },
  { id: 'c9', docId: 'd2', seq: 22, content: '海运头程时效：中国→美西约15-18天，中国→欧洲约25-30天。旺季需预留额外7天。', tokenCount: 34, metadata: { doc_type: 'sop', source: '供应链管理手册.pdf', category: '物流' } },
  { id: 'c10', docId: 'd1', seq: 30, content: '价格调整策略：竞品降价5%以内不跟进，5%-15%降价3%，15%以上需开会评估。', tokenCount: 30, metadata: { doc_type: 'strategy', source: '商品运营规则.md', category: '定价' } },
  { id: 'c11', docId: 'd5', seq: 2, content: '客户投诉处理时效：一般投诉24h内回复，紧急投诉4h内回复，需记录处理过程。', tokenCount: 28, metadata: { doc_type: 'sop', source: '售后处理流程.txt', category: '售后' } },
  { id: 'c12', docId: 'd10', seq: 10, content: '跨境电商VAT注册要求：英国年销售额超£85,000、德国超€100,000需注册VAT。', tokenCount: 26, metadata: { doc_type: 'policy', source: '电商税务合规.pdf', category: '合规' } },
]

export const RETRIEVAL_RESULT = {
  query: '叶菜第二天还能销售吗？', elapsed: 0.23,
  stages: [{ name: 'Query Embedding', status: 'done', elapsed: 0.05 }, { name: 'Vector Search', status: 'done', elapsed: 0.08 }, { name: 'BM25 Search', status: 'done', elapsed: 0.04 }, { name: 'Rerank', status: 'done', elapsed: 0.06 }],
  results: [
    { chunkId: 'c1', score: 0.86, content: '叶菜类生鲜商品建议当天销售完毕，次日需降价30%处理。根茎类可保存3-5天。' },
    { chunkId: 'c2', score: 0.72, content: '生鲜类商品上架需标注保质期和生产日期，临期商品（24h内到期）需下架处理。' },
    { chunkId: 'c3', score: 0.65, content: '商品库存低于安全库存时，需在24小时内发起补货流程。' },
  ],
}
