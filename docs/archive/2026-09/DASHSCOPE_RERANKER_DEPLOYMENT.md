# DashScope Reranker 部署指南

## 概述

本项目已升级支持阿里云 DashScope API 作为重排序器 (reranker) 的后端，提供高质量中文语义匹配能力。系统会在 API 异常时自动降级到本地 CrossEncoder 模型。

## 配置步骤

### 1. 创建 `.env` 文件

在项目根目录的 `backend/` 下创建 `.env` 文件 (如果尚未存在):

```bash
cd backend
cp .env.example .env
```

### 2. 配置环境变量

确保以下变量已正确设置:

```bash
# DashScope Reranker 配置
RERANKER_BACKEND=dashscope
DASHSCOPE_API_KEY=<您的 API Key>
# DASHSCOPE_WORKSPACE_ID=<可选，工作空间 ID，默认使用 default>

# 原有配置保持不变
RERANKER_MODEL_PATH=BAAI/bge-reranker-base
RERANK_TIMEOUT=5
RERANK_TOP_K=8
RERANK_SCORE_THRESHOLD=0.3
```

### 3. 验证配置

启动后端服务，观察日志中是否出现:

```
INFO - 使用 DashScope API 进行重排序
INFO - 初始化 DashScope Reranker (workspace=default, timeout=5s)
```

如果出现以下日志，说明 API Key 未设置:

```
WARNING - 缺少 DASHSCOPE_API_KEY, 降级到本地 CrossEncoder 模型
```

## 工作原理

### 架构流程

```
用户查询 
  ↓
get_reranker_backend() 工厂函数
  ├─ 检查 RERANKER_BACKEND + DASHSCOPE_API_KEY
  ├─ 配置有效 → 返回 DashScopeReranker 实例
  └─ 配置无效 → 返回 LocalCrossEncoderBackend 实例(降级)
    ↓
执行 rerank(query, docs, top_k)
  ├─ DashScope API → HTTP POST 请求 qwen3-rerank 模型
  ├─ API 成功 → 返回 [Doc, score] 列表
  ├─ API 失败 → 记录 ERROR + 降级到本地模型
  └─ Local Model → 本地 CrossEncoder.predict()
```

### 降级策略

| 异常场景 | 触发条件 | 日志级别 | 降级动作 |
|---------|---------|---------|---------|
| API 超时 | 调用 > 5 秒 | WARNING | 切到本地模型 |
| 网络错误 | DNS 解析失败/连接拒绝 | ERROR | 切到本地模型 |
| HTTP 401/403 | API Key 无效 | ERROR | 切到本地模型 |
| HTTP 429 | 限流达到上限 | ERROR | 切到本地模型 |
| HTTP 5xx | 服务端错误 | ERROR | 切到本地模型 |
| JSON 解析失败 | 响应格式错误 | ERROR | 切到本地模型 |

## 测试验证

### 手动测试脚本

创建 `test_api_reranker.py`:

```python
import sys
sys.path.insert(0, 'backend')

from rag.reranker import get_reranker_backend, LocalModelLoader
from langchain_core.documents import Document

# 测试数据
docs = [
    Document(page_content='Python 是一种高级编程语言', metadata={'source': 'doc1'}),
    Document(page_content='机器学习需要使用 Python 库', metadata={'source': 'doc2'}),
    Document(page_content='今天天气晴朗', metadata={'source': 'doc3'})
]

print("=== 测试 DashScope Reranker ===")
backend = get_reranker_backend()
print(f"后端类型：{type(backend).__name__}")
print(f"模型加载状态：{LocalModelLoader.is_loaded()}")

result = backend.compress_documents(docs, 'Python 是什么？', top_k=2)
print(f"\n返回结果数量：{len(result)}")
for i, doc in enumerate(result, 1):
    print(f"{i}. score={doc.metadata.get('rerank_score'):.4f} source={doc.metadata.get('source')}")
```

运行测试:

```bash
cd d:\Program Files\workplace\agent
python test_api_reranker.py
```

预期输出示例:

```
INFO - 使用 DashScope API 进行重排序
DEBUG - DashScope API 重排序完成：query_len=7, doc_count=3, top_k=2
返回结果数量：2
1. score=0.8765 source=doc1
2. score=0.6543 source=doc2
```

### 降级测试

禁用网络或删除 API Key，验证降级功能:

```bash
# 临时注释 API Key
# DASHSCOPE_API_KEY=...

# 重启后端，应看到:
WARNING - 缺少 DASHSCOPE_API_KEY, 降级到本地 CrossEncoder 模型
INFO - 本地 reranker 模型懒加载完成：...
```

## 监控与告警

### 可观测性指标

系统会记录以下关键指标到 Tracer:

- `backend_type`: `dashscope` / `local` / `fallback`
- `is_fallback`: boolean 标记是否降级
- `error_type`: 降级原因 (timeout/connection/http_5xx 等)
- `score`: 每个结果的 rerank_score

### 日志级别说明

| Level | 触发场景 |
|-------|---------|
| INFO | 首次选择 API 后端、模型懒加载完成 |
| WARNING | API 超时触发降级 |
| ERROR | HTTP 错误、JSON 解析失败、降级发生 |
| DEBUG | 每次 API 调用的详细统计 (query_len, cost_tokens 等) |

## 性能考虑

### 延迟对比

| 后端 | 平均延迟 | 优势 | 劣势 |
|------|---------|------|------|
| DashScope API | ~2-5 秒 | 高质量中文理解 | 依赖网络 |
| Local CrossEncoder | ~1-3 秒 | 无需网络 | 质量略低 |

### 建议配置

- **生产环境**: 启用 DashScope API (`RERANKER_BACKEND=dashscope`)
- **离线/调试**: 禁用 API (`RERANKER_BACKEND=local`)
- **混合部署**: 保持默认 (API 优先 + 自动降级)

## 费用管理

### DashScope 计费参考

- qwen3-rerank 模型按调用次数计费
- 建议设置配额预警 (阿里云控制台)
- 监控调用频率，避免意外费用

### 成本控制建议

1. 在 `.env` 中查看当前配额和消费
2. 定期导出使用报告
3. 对高流量场景考虑批量处理

## 故障排查

### 问题 1: API Key 无效

**症状**:
```
ERROR - DashScope API HTTP 错误 [401]: {"code":"InvalidApiKey","message":"..."}
WARNING - 缺少 DASHSCOPE_API_KEY, 降级到本地 CrossEncoder 模型
```

**解决方案**:
1. 检查 `.env` 文件中 `DASHSCOPE_API_KEY` 是否正确
2. 确认 API Key 未过期
3. 重启后端重新加载配置

### 问题 2: 频繁降级到本地

**症状**:
日志中频繁出现降级警告

**可能原因**:
- 网络连接不稳定
- DashScope 服务暂时不可用
- API 限流

**解决方案**:
1. 检查网络连通性
2. 查看阿里云服务状态页
3. 考虑调整 `RERANK_TIMEOUT` 值

### 问题 3: 分数范围异常

**症状**: 返回的 score 不在 0-1 区间

**解决方案**:
- DashScope API 返回的分数已在 0-1 区间
- 本地模型需经过 sigmoid 归一化
- 检查代码中是否正确应用了阈值过滤

## 安全注意事项

⚠️ **重要提醒**

1. **不要将 `.env` 文件提交到 Git**: 已在 `.gitignore` 中排除
2. **生产环境必须配置有效的 API Key**: 避免明文硬编码
3. **定期轮换 API Key**: 增强安全性
4. **限制权限**: API Key 仅用于 Reranker，不要滥用

## 回退方案

如需完全禁用 DashScope API，修改配置:

```bash
RERANKER_BACKEND=local
# DASHSCOPE_API_KEY=<留空或删除>
```

系统会自动回退到纯本地模式。

---

**最后更新**: 2026-08-26  
**版本**: v1.0 (DashScope Integration)
