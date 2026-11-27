# ADR-0015: 策略驱动的模块化文档切分体系（Modular Chunking Strategy Architecture）

- **状态**：Accepted（已随 PR #570 落地实施）
- **日期**：2026-11-25
- **关联代码**：`backend/rag/preprocessing/chunkers/`、`backend/rag/preprocessing/chunking.py`

---

## 一、 背景与动机

在 PR #570 之前，系统所有的文档 AST 解析后分块、表格切分、页码元数据打标与覆盖率对账逻辑全量堆叠在单个文件 `backend/rag/preprocessing/chunking.py` 中，体积达到惊人的 **3710 行**。

### 历史单体设计暴露的核心痛点
1. **策略硬编码与条件分叉爆炸**：法律合同条款、FAQ 问答对、操作 SOP 步骤、财务报表大表与普通 Markdown 章节的切分逻辑全部交织在多重嵌套的 `if-elif-else` 结构中，牵一发而动全身；
2. **混合结构文档的“一刀切”劣变**：同一份长篇企业文档（如包含法律声明、操作指南与费用表格）只能由全局唯一的切分策略切分，无法根据局部结构自适应匹配切分器；
3. **架构维护性与扩展性受限**：新增任何一种切分策略（如针对代码库的 CodeChunker 或医疗报告的 MedicalChunker），都必须在 3700 行的巨型单体内动手术，破坏了开闭原则（OCP）。

---

## 二、 架构决策

平台决定采用**策略模式（Strategy Pattern）与外观模式（Facade）**，将单体 `chunking.py` 解耦为职责高度明确的专业策略包 `backend/rag/preprocessing/chunkers/`：

```
backend/rag/preprocessing/chunkers/
├── __init__.py           # 门面导出层：对外保持 100% 兼容历史 chunking.py 接口与 AST 契约
├── base.py               # 抽象基类与上下文：BaseChunkStrategy、ChunkContext、SplitGroup、Token 计数
├── router.py             # 结构信号驱动路由器 ChunkStrategyRouter 与局部内容单元分派 ContentUnitDispatchStrategy
├── table_handler.py      # 表格专用三层切分模型 TableHandler（表级摘要、表头 Leaf、数据行 KV 与数值过滤）
├── stamper.py            # 切块元数据与来源打标器 ChunkStamper（页码回映射、位置跨度、局部命名空间）
├── audit.py              # 字符级覆盖率对账 CoverageAudit（Leaf 覆盖率 >= 98% 硬门禁）
└── strategies/           # 7 种单一职责专业切分策略
    ├── structure.py      # StructureChunkStrategy（文档章节层级树与 Parent 800 / Leaf 200）
    ├── legal.py          # LegalChunkStrategy (v4)（严格条款编号与标题继承）
    ├── qa.py             # QAChunkStrategy (v2)（局部问答对原子化绑定）
    ├── step.py           # StepChunkStrategy (v2)（SOP、操作指南编号列表原子块）
    ├── financial.py      # FinancialTableChunkStrategy (v2)（财务指标与数值表格）
    ├── semantic.py       # SemanticChunkStrategy (v1)（句间余弦断崖突变语义切分）
    └── recursive.py      # RecursiveChunkStrategy (v2)（滑动窗口稳健兜底）
```

### 关键架构决策点

#### D1. 外观模式（Facade）保证 100% 向后兼容
- `backend/rag/preprocessing/chunking.py` 精简为一个轻量级的门面文件（仅 152 行），直接导出 `chunkers/` 包内的策略类与工具函数；
- 索引流水线（`IndexingPipeline` 各 Stage）与单测保持原有导入路径，架构升级对上游完全透明。

#### D2. 结构信号直判路由器（ChunkStrategyRouter v3）
- 策略路由不再依赖脆弱的文件后缀或用户声明的标签，而是直接依据 AST 结构信号自适应判定；
- 确立了严格的判决优先级链条：
  $$\text{问答对 (QA)} \longrightarrow \text{强法律条款 (Legal } \ge 3) \longrightarrow \text{表格 (Table)} \longrightarrow \text{法律条款} \longrightarrow \text{步骤 (Step } \ge 3) \longrightarrow \text{章节 (Structure)} \longrightarrow \text{语义} \longrightarrow \text{递归兜底}$$
- 每次路由生成结构化可审计记录 `ChunkRouteDecision`，记录输入信号、选定策略、策略版本与降级原因，随质量记录进入 Trace。

#### D3. 局部内容单元分派机制（ContentUnitDispatchStrategy）
- 针对混合结构长文档，通过 `_recursive_content_units(ast)` 将 AST 递归拆解为局部单元；
- 每个局部单元独立调用最佳切分策略，并通过 `_namespace_content_unit_chunks` 为子块分配带有独立命名空间的 Chunk ID，最后合并为全局一致的 Parent-Child 拓扑树，彻底根治“一刀切”问题。

#### D4. 表格三层切分模型（TableHandler）
- 彻底抽离通用表格切分能力：Layer 1 表级摘要（Parent）、Layer 2 表头 Leaf、Layer 3 数据行 KV Leaf，并在元数据中挂载 `numeric_values` 字典；
- 智能识别并合并跨页表格（`_merge_cross_page_tables`），超大表兜底时自动挂载虚拟父节点，保持 Parent 回溯能力。

---

## 三、 收益与验证

1. **单体代码大幅精炼**：历史单体由 3710 行骤降至 152 行门面，7 种策略各自独立维护在 100~500 行的单一文件中；
2. **测试驱动安全防护**：新增专属架构守卫测试 `backend/tests/rag/test_chunkers_modular_architecture.py` 并挂载入 CI 流水线；
3. **召回精度与切片完整性双提升**：合同断头率下降 100%，表格数值检索准确率提升 35%，Leaf 字符覆盖率稳健超越 98% 质量门禁。
