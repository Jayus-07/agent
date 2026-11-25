# 任务契约：RAG Chunking 核心切分模块策略化重构 (Tier 1)

## 1. 目标与背景
`backend/rag/preprocessing/chunking.py` 单文件长达 3587 行，内聚了 8 种以上的异构职责（递归切分、法律条款、表格提取、步骤切分、QA问答、语义切分、覆盖率审计与结构打标）。
本次重构严格遵循单一职责原则（SRP）与马斯克五步骤，通过 **Strategy（策略模式）+ Facade（门面模式）** 对其进行物理模块化拆解，消除单体巨兽，同时保证存量接口 100% 向后兼容，所有回归单测与 CI 门禁全绿。

## 2. 改造范围与职责划分
建立专职子包 `backend/rag/preprocessing/chunkers/`：
- `base.py`：基础协议、预算硬切工具与公共结构，提供 `get_leaf_chunk_tokens` 等动态 monkeypatch 传播探测器；
- `audit.py`：覆盖率审计器 `coverage_audit` 独立（416行）；
- `stamper.py`：结构化打标与页码回填 `stamp_chunk_structure`、`stamp_chunk_pages` 独立（311行）；
- `table_handler.py`：表格常量、内联拆分与虚拟父节点处理；
- `strategies/`：
  - `structure.py`：结构化与非结构化通用分块；
  - `step.py`：步骤操作文档分块；
  - `legal.py`：合同法规分块（含 400+ 行的合同表格内联）；
  - `qa.py`：问答对感知分块；
  - `semantic.py`：语义相似度分块；
  - `financial.py`：财务报表多维结构分块；
  - `recursive.py`：传统固定长度与递归分块；
- `router.py`：路由决策器 `ChunkStrategyRouter` 与局部内容单元分发策略 `ContentUnitDispatchStrategy`；
- `chunking.py`：轻量门面类（Facade），代码严格控制在 152 行（缩减 95.8%），re-export 保持对存量调用完全兼容，保留单事实源 AST 常量定义。

## 3. 验收标准
1. `chunking.py` 单文件行数从 3587 行下降 90% 以上（实际达成 152 行，缩减 95.8%）；
2. 拆分出的各策略及功能模块单文件行数均控制在 150~450 行之间；
3. 架构守卫单测 `test_chunkers_modular_architecture.py` 通过；
4. 纯单元测试套件全部通过（核心单测、集成单测、企业级单测与质量门禁 180+ 项断言）；
5. 通过 PR CI 门禁（RAG Smoke preflight, secret scan）并完成合并。
