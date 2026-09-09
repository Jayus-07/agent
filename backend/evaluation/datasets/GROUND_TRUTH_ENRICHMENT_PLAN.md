# V2 数据集 ground_truth_context 充实计划

## 当前状态

### 已完成的核心修复（阻塞性问题）
- ✅ Task #18: 修复 metrics.py 空期望集默认返回 1.0 的 bug
- ✅ Task #19: 核心指标切换到语义层（ground_truth_context 驱动）
- ✅ Task #22: 报告分层（检索指标与生成指标分离呈现）

### 待完成的数据质量改进
- ⏳ Task #20: 充实 V2 数据集的 ground_truth_context 为完整段落
- ⏳ Task #21: 统一 baseline 和 V2 数据集格式

## 数据统计

| 数据集文件 | 用例数 | 已有完整 ground_truth_context |
|-----------|--------|------------------------------|
| generation.json | 10 | 0 |
| retrieval_adversarial.json | 10 | 0 |
| retrieval_basic.json | 20 | 0 |
| retrieval_department.deprecated.json | 6 | 4 |
| retrieval_hard_negative.json | 12 | 4 |
| retrieval_multi_doc.json | 12 | 4 |
| retrieval_table.json | 10 | 4 |
| **总计** | **80** | **16 (20%)** |

## 问题描述

当前 V2 数据集的 ground_truth_context 字段存在以下问题：

1. **文本过短**: 仅包含关键词片段（如 "24小时"、"支付宝"），而非完整段落
2. **元数据缺失**: source_doc 和 section 字段为空
3. **语义评估受限**: 虽然语义指标可以计算，但缺乏完整上下文会影响评估质量

### 示例对比

**当前 V2 格式（不完整）**:
```json
"ground_truth_context": [
  {
    "text": "24小时",
    "source_doc": "",
    "section": ""
  }
]
```

**目标格式（参考 rag_test_kb.json）**:
```json
"ground_truth_context": [
  {
    "text": "Q：订单可以修改地址吗?\nA：待发货状态可自行修改；已发货状态请联系客服申请拦截改址。",
    "source_doc": "rag_test_kb",
    "section": "订单可以修改地址吗?"
  }
]
```

## 充实方案

### 方案 A: 手动充实（推荐，质量最高）
1. 对每个测试用例，找到对应的源文档（通过 relevant_docs）
2. 在源文档中定位 relevant_snippets 所在的段落
3. 提取完整段落，填充 source_doc 和 section
4. 预计工作量：80 个用例 × 5 分钟 = 约 7 小时

### 方案 B: 半自动充实（脚本辅助）
1. 编写脚本从源文档中自动提取包含 snippet 的段落
2. 需要访问 doc_registry 获取 doc_id 到文件路径的映射
3. 人工审核和修正自动提取的结果
4. 预计工作量：脚本开发 2 小时 + 人工审核 4 小时 = 约 6 小时

### 方案 C: 保持现状（可接受）
- 当前评估系统已可正常工作
- 语义指标（sem_context_recall 等）可以正常计算
- 数据质量改进可以延后处理
- 建议在新用例标注时直接采用完整格式

## 建议

鉴于：
1. 核心评估功能已正常运作（测试全部通过）
2. 数据充实工作量大（80 个用例）
3. 不影响评估系统的正确性

**建议**: 将 Task #20 和 #21 标记为低优先级，在新用例标注或数据集重构时逐步完成。

## 参考工具

已创建辅助脚本：`backend/scripts/enrich_ground_truth_context.py`
- 可分析当前数据集的 ground_truth_context 完整度
- 可扩展为自动充实工具（需集成 doc_registry）
