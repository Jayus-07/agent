type LabelMap = Record<string, string>

const DOCUMENT_TYPE_LABELS: LabelMap = {
  listing: '商品上架',
  sop: '操作流程',
  ad_policy: '广告政策',
  faq: '常见问题',
  product_spec: '商品规格',
  training: '培训资料',
  policy: '制度文件',
  compliance: '合规文件',
  legal: '法律文件',
  security: '安全文件',
  financial: '财务文件',
  customer_data: '客户资料',
  contract_template: '合同模板',
  travel_guide: '旅游指南',
  general: '通用文档',
}

const BUSINESS_DOMAIN_LABELS: LabelMap = {
  product: '商品',
  order: '订单',
  inventory: '库存',
  logistics: '物流',
  advertising: '广告',
  customer: '客户服务',
  supplier: '供应商',
  analytics: '经营分析',
  data: '数据治理',
  financial: '财务',
  marketing: '营销',
  hr: '人事',
  travel: '旅游',
  general: '通用',
}

const DEPARTMENT_LABELS: LabelMap = {
  warehouse: '仓储部',
  supply_chain: '供应链部',
  order_dept: '订单部',
  customer: '客服部',
  product_dept: '商品部',
  hr: '人事部',
  finance: '财务部',
  admin: '行政部',
  general: '通用',
}

const PROCESSING_STAGE_LABELS: LabelMap = {
  load: '读取文件',
  loading: '读取文件',
  parser: '解析文档',
  parse: '解析文档',
  ocr: '文字识别',
  clean: '文本清洗',
  cleaning: '文本清洗',
  classify: '文档分类',
  document_classify: '文档分类',
  metadata: '元数据处理',
  metadata_extract: '元数据抽取',
  metadata_summary: '摘要生成',
  metadata_classify: '元数据分类',
  question_gen: '模拟问题生成',
  table_describe: '表格描述',
  chunk: '文本切分',
  chunking: '文本切分',
  semantic_chunk: '语义切分',
  chunking_llm_fallback: '大模型切分边界兜底',
  embedding: '向量生成',
  vector_write: '写入向量库',
  bm25_write: '写入关键词索引',
  publish: '发布索引',
}

const PROCESSING_STATUS_LABELS: LabelMap = {
  active: '已入库',
  done: '已完成',
  deleted: '已删除',
  uploading: '上传中',
  parsing: '解析中',
  embedding: '向量生成中',
  processing: '处理中',
  indexing: '索引中',
  pending_review: '待审核',
  published: '已发布',
  superseded: '已被新任务取代',
  partial: '部分完成',
  partially_failed: '部分失败',
  success: '成功',
  skipped: '跳过',
  cached: '缓存命中',
  fallback: '已回退到规则处理',
  failed: '失败',
  error: '失败',
  running: '处理中',
  pending: '等待处理',
  queued: '排队中',
  timeout: '超时',
  cancelled: '已取消',
  duplicate: '内容重复，未重复入库',
}

const PROCESSING_ROUTE_LABELS: LabelMap = {
  rule_only: '规则处理',
  unified_extract: '统一元数据抽取',
  question_gen: '模拟问题生成',
  metadata_llm: '大模型元数据增强',
  none: '未调用模型',
}

const FALLBACK_TYPE_LABELS: LabelMap = {
  no_recognized_structure: '规则未识别出文档结构',
  rule_coverage_gap: '规则切分覆盖不足',
}

const CHUNK_STRATEGY_LABELS: LabelMap = {
  ManualPolicyChunkStrategy: '制度规范文档切分策略',
  ProjectReportChunkStrategy: '项目报告切分策略',
  GeneralChunkStrategy: '通用文本切分策略',
  ManualChunkStrategy: '操作手册切分策略',
  ContractChunkStrategy: '合同条款切分策略',
  RecursiveChunkStrategy: '递归切分策略',
  StructureChunkStrategy: '结构切分策略',
  StepChunkStrategy: '步骤切分策略',
  LegalChunkStrategy: '条款切分策略',
  QAChunkStrategy: '问答切分策略',
  SemanticChunkStrategy: '语义切分策略',
  FinancialTableChunkStrategy: '财务表格切分策略',
  FixedSizeChunkStrategy: '定长切分策略',
  ContentUnitDispatchStrategy: '内容单元路由策略',
  pipeline: '统一处理流水线',
  deterministic_fallback: '规则兜底',
}

const CHUNK_TYPE_LABELS: LabelMap = {
  text: '正文',
  section: '章节',
  faq: '问答',
  step: '操作步骤',
  legal_clause: '法律条款',
  contract_context: '合同上下文',
  table_row: '表格数据行',
  table_summary: '表格摘要',
  table_fallback: '表格兜底内容',
  table_header: '表头',
  table_inline: '正文内表格',
  table_row_group: '表格行组',
}

const FILTER_REASON_LABELS: LabelMap = {
  empty: '内容为空',
  too_short: '内容过短',
  all_symbols: '仅包含符号',
  low_chinese_ratio: '中文内容占比偏低',
  duplicate: '内容重复',
}

const GRANULARITY_LABELS: LabelMap = {
  parent: '父块',
  leaf: '检索块',
}

const PARENT_SCOPE_LABELS: LabelMap = {
  document: '整份文档',
  section: '当前章节',
}

const METADATA_LABELS: LabelMap = {
  source_format: '原文件格式',
  parser_version: '解析器版本',
  chunk_policy_version: '切分规则版本',
  index_generation: '索引代次',
  source_locators_json: '原文位置',
  source_spans_json: '原文范围',
  source_locations_json: '来源定位',
  page: '页码',
  pages: '页数',
  table_id: '表格编号',
  row_index: '表格行号',
  row_part_index: '行分片号',
  row_count: '表格行数',
  col_count: '表格列数',
  split_group_id: '拆分组',
  split_part: '拆分序号',
  split_total: '拆分总数',
  content_hash: '分块内容校验值',
  quality_flags: '质量标记',
  filter_status: '过滤状态',
  filter_reason: '过滤原因',
  section_path: '章节路径',
  section_level: '章节层级',
  section_id: '章节编号',
  parent_section_id: '父章节编号',
  parent_scope: '父块范围',
  content_unit: '内容单元',
  content_unit_id: '内容单元编号',
  content_unit_strategy: '内容单元切分方式',
  chunk_tokens: '分块 Token 数',
  embedding_model: '向量模型',
  embedding_version: '向量版本',
  embedding_fingerprint: '向量配置指纹',
  chunk_type: '分块类型',
  granularity: '分块层级',
  doc_type: '文档类型',
  business_domain: '业务领域',
  department: '所属部门',
  kb_audience: '知识库用途',
}

const OPERATION_LABELS: LabelMap = {
  upload: '上传',
  reindex: '重新解析并索引',
  delete: '删除',
}

const FILE_FORMAT_LABELS: LabelMap = {
  pdf: 'PDF',
  md: 'Markdown',
  markdown: 'Markdown',
  txt: 'TXT 文本',
  docx: 'Word 文档',
  xlsx: 'Excel 表格',
  csv: 'CSV 表格',
  html: 'HTML 网页',
  htm: 'HTML 网页',
  pptx: 'PowerPoint 演示文稿',
  json: 'JSON 数据',
  xml: 'XML 数据',
}

const MODEL_ROLE_LABELS: LabelMap = {
  main: '主模型',
  doc: '文档处理模型',
  ocr: '文字识别模型',
  embedding: '向量模型',
  metadata_extract: '元数据抽取模型',
  question_gen: '模拟问题生成模型',
  table_describe: '表格描述模型',
  reranker: '重排模型',
}

const ENGINE_TYPE_LABELS: LabelMap = {
  llm: '大模型',
  embedding: '向量模型',
  ocr: '文字识别',
  chunker: '切分器',
  deterministic: '规则处理',
  rule: '规则处理',
}

const CACHE_STATUS_LABELS: LabelMap = {
  hit: '命中缓存',
  miss: '未命中缓存',
  cached: '命中缓存',
  bypass: '未使用缓存',
}

const PROVIDER_LABELS: LabelMap = {
  dashscope: '阿里云百炼',
  aliyun: '阿里云',
  openai: 'OpenAI',
  azure_openai: 'Azure OpenAI',
  ollama: '本地模型服务',
  vllm: '本地模型服务',
  siliconflow: '硅基流动',
  volcengine: '火山引擎',
}

const KNOWN_KB_LABELS: LabelMap = {
  biz_inventory: '库存业务库',
  biz_order: '订单业务库',
  biz_product: '商品业务库',
  policy_hr: '人事制度库',
  policy_finance: '财务制度库',
  policy_general: '企业公共制度库',
  travel: '旅游知识库',
  rag_eval_kb: '统一 RAG 评测知识库',
}

const QUALITY_CODE_LABELS: LabelMap = {
  legacy_quality_record_incomplete: '历史质量记录不完整，需要重新核验',
  table_truncated: '表格内容触发资源上限，未完整解析',
  coverage_gap: '原文内容未完整进入最终分块',
  low_chinese_ratio: '中文内容占比偏低',
  empty: '内容为空',
  too_short: '内容过短',
  all_symbols: '仅包含符号',
  duplicate: '内容重复',
}

function labelFor(
  value: string | null | undefined,
  labels: LabelMap,
  unknown: string,
): string {
  if (!value) return '未记录'
  return labels[value] || unknown
}

export function getDocumentTypeLabel(value?: string | null): string {
  return labelFor(value, DOCUMENT_TYPE_LABELS, '未识别文档类型')
}

export function getBusinessDomainLabel(value?: string | null): string {
  return labelFor(value, BUSINESS_DOMAIN_LABELS, '未识别业务领域')
}

export function getDepartmentLabel(
  value?: string | null,
  options: readonly { id: string; label: string }[] = [],
): string {
  if (!value) return '未记录'
  return options.find((option) => option.id === value)?.label
    || DEPARTMENT_LABELS[value]
    || '未登记部门'
}

export function getKnowledgeBaseLabel(
  value?: string | null,
  options: readonly { id: string; name: string }[] = [],
): string {
  if (!value) return '未记录'
  if (value === 'default') return '默认知识库'
  return options.find((option) => option.id === value)?.name
    || KNOWN_KB_LABELS[value]
    || '未登记知识库'
}

export function getProcessingStageLabel(value?: string | null): string {
  return labelFor(value, PROCESSING_STAGE_LABELS, '其他处理阶段')
}

export function getProcessingStatusLabel(value?: string | null): string {
  return value
    ? labelFor(value.toLowerCase(), PROCESSING_STATUS_LABELS, '状态未记录')
    : '状态未记录'
}

export function getModelRoleLabel(value?: string | null): string {
  if (!value) return '未记录'
  return MODEL_ROLE_LABELS[value] || ENGINE_TYPE_LABELS[value] || '其他处理角色'
}

export function getCacheStatusLabel(value?: string | null): string {
  return value
    ? labelFor(value.toLowerCase(), CACHE_STATUS_LABELS, '其他缓存状态')
    : '状态未记录'
}

export function getProviderLabel(value?: string | null): string {
  if (!value) return '未记录'
  return PROVIDER_LABELS[value.toLowerCase()] || '其他模型服务'
}

export function getAdminErrorLabel(value?: string | null): string {
  if (!value) return '未记录错误详情'
  if (/[一-龥]/.test(value)) return value

  const normalized = value.toLowerCase()
  if (normalized.includes('too large')) return '文件超过服务端大小上限'
  if (normalized.includes('empty file') || normalized.includes('file is empty')) return '文件内容为空'
  if (normalized.includes('unsupported') && normalized.includes('ext')) return '文件格式不受支持'
  if (normalized.includes('mime')) return '文件声明类型与文件格式不匹配'
  if (normalized.includes('magic check') || normalized.includes('corrupted')) return '文件内容与所选格式不符或已损坏'
  if (normalized.includes('ocr') && normalized.includes('page')) return '文字识别页数超出限制或识别不完整'
  if (normalized.includes('timeout')) return '处理超时'
  if (normalized.includes('network') || normalized.includes('fetch')) return '网络连接失败，请检查服务状态后重试'
  if (/^name ['"].+['"] is not defined$/i.test(value)) return '系统程序错误：缺少必要的内部状态变量，任务未完成'
  return '处理失败，系统已记录错误详情，请查看运行记录'
}

export function getProcessingRouteLabel(value?: string | null): string {
  return labelFor(value, PROCESSING_ROUTE_LABELS, '其他处理流程')
}

export function getFallbackTypeLabel(value?: string | null): string {
  return labelFor(value, FALLBACK_TYPE_LABELS, '其他兜底类型')
}

export function getChunkStrategyLabel(value?: string | null): string {
  return labelFor(value, CHUNK_STRATEGY_LABELS, '其他切分策略')
}

export function getChunkTypeLabel(value?: string | null): string {
  if (!value) return '未记录'
  if (value.startsWith('table_') && !CHUNK_TYPE_LABELS[value]) return '表格内容'
  return labelFor(value, CHUNK_TYPE_LABELS, '其他分块类型')
}

export function getGranularityLabel(value?: string | null): string {
  return labelFor(value, GRANULARITY_LABELS, '未识别分块层级')
}

export function getParentScopeLabel(value?: string | null): string {
  return labelFor(value, PARENT_SCOPE_LABELS, '未识别父块范围')
}

export function getChunkFilterReasonLabel(value?: string | null): string {
  return labelFor(value, FILTER_REASON_LABELS, '其他过滤原因')
}

export function getChunkMetadataLabel(value: string): string {
  return METADATA_LABELS[value] || '其他索引元数据'
}

export function getOperationLabel(value?: string | null): string {
  return labelFor(value, OPERATION_LABELS, '其他操作')
}

export function getFileFormatLabel(value?: string | null): string {
  return labelFor(value?.toLowerCase(), FILE_FORMAT_LABELS, '其他文件格式')
}

export function getQualityIssueLabel(value?: string | null): string {
  if (!value) return ''
  const translated = QUALITY_CODE_LABELS[value]
  if (translated) return translated

  const filtered = value.match(/^filtered_chunks:(\d+)\(([^)]+)\)$/)
  if (filtered) {
    const reasons = filtered[2].split(',').map((item) => {
      const [code, count] = item.split('=')
      return getChunkFilterReasonLabel(code) + ' ' + (count || '')
    })
    return '过滤了 ' + filtered[1] + ' 个分块：' + reasons.join('、')
  }

  if (/[一-龥]/.test(value)) return value
  return '存在质量提醒，请查看处理运行详情'
}

export function getProcessingCodeLabel(value?: string | null): string {
  if (!value) return '未记录'
  const normalized = value.toLowerCase()
  const mapped = PROCESSING_STAGE_LABELS[value]
    || PROCESSING_STATUS_LABELS[value]
    || PROCESSING_STATUS_LABELS[normalized]
    || PROCESSING_ROUTE_LABELS[value]
    || CHUNK_STRATEGY_LABELS[value]
    || FALLBACK_TYPE_LABELS[value]
    || MODEL_ROLE_LABELS[value]
    || ENGINE_TYPE_LABELS[value]
    || CACHE_STATUS_LABELS[value]
    || CACHE_STATUS_LABELS[normalized]
    || FILTER_REASON_LABELS[value]
    || CHUNK_TYPE_LABELS[value]
    || GRANULARITY_LABELS[value]
    || PARENT_SCOPE_LABELS[value]
    || FILE_FORMAT_LABELS[value.toLowerCase()]
  return mapped || '其他处理信息'
}

export function formatChunkMetadataValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)

  if (typeof value !== 'string') return JSON.stringify(value)
  if (key === 'quality_flags') return getQualityIssueLabel(value)
  if (key === 'source_format') return getFileFormatLabel(value)
  if (key === 'doc_type') return getDocumentTypeLabel(value)
  if (key === 'business_domain') return getBusinessDomainLabel(value)
  if (key === 'department') return getDepartmentLabel(value)
  if (key === 'filter_reason') return getChunkFilterReasonLabel(value)
  if (key === 'chunk_type') return getChunkTypeLabel(value)
  if (key === 'parent_scope') return getParentScopeLabel(value)
  if (key === 'granularity') return getGranularityLabel(value)
  if (key === 'content_unit_strategy') return getChunkStrategyLabel(value)
  if (key === 'strategy') return getChunkStrategyLabel(value)
  if (key === 'filter_status') {
    return labelFor(value, {
      kept: '保留',
      accepted: '保留',
      filtered: '已过滤',
      rejected: '已拒绝',
    }, '其他状态')
  }
  return value
}
