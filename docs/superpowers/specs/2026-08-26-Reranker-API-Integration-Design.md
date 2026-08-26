# Reranker API Integration Design

**Date:** 2026-08-26  
**Status:** Approved  
**Author:** Qoder Agent  

---

## Executive Summary (执行摘要)

本方案设计将项目当前使用的本地 CrossEncoder 重排序模型（BAAI/bge-reranker-base）切换到阿里云 DashScope 在线 API（qwen3-rerank），同时保留本地模型作为降级 fallback。核心目标是提升中文检索效果、降低本地资源占用、实现完善的网络异常处理和透明降级机制。

实施完成后，系统默认使用 DashScope API 进行重排序，仅在 API 超时/错误时自动切换到本地模型，对用户完全透明。环境配置通过 `.env` 文件管理 API Key，无需硬编码在代码中。

---

## Current State Analysis (现状分析)

### 1. 当前架构痛点

- **模型加载方式**：模块级全局实例 `reranker = CrossEncoder(RERANKER_MODEL_PATH)`，导入时立即加载到内存
- **无后端切换能力**：仅支持本地 CrossEncoder，无法切换到其他后端
- **分数处理硬编码**：直接调用 `.predict()` 输出 logit，通过 `_sigmoid()` 归一化
- **降级机制缺失**：timeout 失败后仅返回原始文档，不尝试 fallback 到其他 reranker

### 2. 现有代码结构（`backend/rag/reranker.py`）

```python
# 问题：模块级导入即加载
reranker = CrossEncoder(RERANKER_MODEL_PATH)

# 固定返回 (doc, score) 元组
def rerank(query, docs, top_k=3, debug=0):
    scores = safe_call_with_timeout(reranker.predict, ...)
    # sigmoid 归一化后过滤
```

### 3. 配置依赖

| 环境变量 | 默认值 | 作用 |
|---------|--------|------|
| `RERANKER_MODEL_PATH` | `BAAI/bge-reranker-base` | 本地模型路径 |
| `RERANK_TIMEOUT` | `15` 秒 | 重排序超时 |
| `RERANK_TOP_K` | `8` | 返回文档数 |
| `RERANK_SCORE_THRESHOLD` | `0.3` | 分数阈值 |

---

## Target Architecture (目标架构)

### 1. 新架构图

```
┌──────────────────────────────────────────────────────┐
│              Reranker Backend Selector                │
├──────────────────────────────────────────────────────┤
│                                                       │
│  ┌─────────────────────┐      ┌──────────────────┐   │
│  │  DashScopeReranker  │      │ LocalModelLoader │   │
│  │  - HTTP POST call   │      │ - Lazy load      │   │
│  │  - Timeout: 5s      │      │ - Singleton      │   │
│  │  - Normalized 0-1   │      │ - Cache instance │   │
│  │  - Fallback handler │      │                  │   │
│  └─────────────────────┘      └──────────────────┘   │
│            ▲                        ▲                │
│            │                        │                │
│            │        Selection       │                │
│            └────────────────────────┘                │
│                                                      │
│       Environment: RERANKER_BACKEND                 │
│       (local | dashscope)                           │
└──────────────────────────────────────────────────────┘
```

### 2. 关键设计决策

#### 2.1 懒加载模式

- **本地模型**：不再在模块导入时加载，改为首次调用 `rerank()` 时才加载（如果 `RERANKER_BACKEND=local`）
- **DashScope API**：无本地加载成本，直接通过网络调用

#### 2.2 分数归一化一致性

| 后端类型 | 原始分数来源 | 归一化处理 | 最终范围 |
|---------|------------|----------|---------|
| DashScope API | `relevance_score` (0-1) | 直接使用 | [0, 1] |
| 本地 CrossEncoder | logit (-10~+10) | `_sigmoid()` 映射 | [0, 1] |

#### 2.3 工厂模式选择

```python
# 伪代码示例
def get_reranker() -> BaseReranker:
    backend = os.getenv("RERANKER_BACKEND", "dashscope")
    api_key = os.getenv("DASHSCOPE_API_KEY")
    
    if backend == "dashscope" and api_key:
        return DashScopeReranker(api_key=api_key)
    else:
        return LocalCrossEncoderReranker()
```

#### 2.4 降级策略（符合"完全透明降级"需求）

- **API 正常** → 使用 DashScope，INFO 日志记录
- **API 超时（>5s）** → WARNING 日志 + 降级到本地模型
- **HTTP 错误（4xx/5xx）** → ERROR 日志 + 降级到本地模型
- **网络不可达** → ERROR 日志 + 降级到本地模型
- **本地模型未加载** → INFO 日志 + 懒加载

---

## Implementation Plan (实施计划)

### 1. 任务分解

#### Task 1: 新增 DashScopeReranker 类

**文件**: `backend/rag/reranker.py`

- 定义 `class DashScopeReranker`，继承自 `BaseDocumentCompressor` 或独立类
- 实现 `__init__(self, api_key: str, timeout: int = 5)` 
- 实现 `rank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]`
  - 构造 HTTP POST 请求体
  - 设置 headers: `Authorization: Bearer <api_key>`
  - 端点：`https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks`
  - 解析响应 `results` 字段，提取 `(index, relevance_score)`
  - 返回 normalized scores (already 0-1, no sigmoid needed)
- 集成详细日志：记录 `model`, `query_length`, `doc_count`, `cost_tokens`

#### Task 2: 重构本地模型为懒加载单例

**文件**: `backend/rag/reranker.py`

- 移除模块级 `reranker = CrossEncoder(...)`
- 新增 `class LocalModelLoader`:
  ```python
  class LocalModelLoader:
      _instance = None
      
      @classmethod
      def get_instance(cls) -> CrossEncoder:
          if cls._instance is None:
              cls._instance = CrossEncoder(RERANKER_MODEL_PATH)
              logger.info(f"本地重排序模型懒加载完成：{RERANKER_MODEL_PATH}")
          return cls._instance
  ```
- 更新 `rerank()` 函数调用 `LocalModelLoader.get_instance().predict()`

#### Task 3: 实现双后端工厂函数

**文件**: `backend/rag/reranker.py`

- 新增 `def get_reranker_backend() -> BaseReranker`
- 逻辑：
  ```python
  backend = os.getenv("RERANKER_BACKEND", "dashscope")
  api_key = os.getenv("DASHSCOPE_API_KEY")
  
  if backend == "dashscope" and api_key:
      return DashScopeReranker(api_key=api_key)
  else:
      logger.warning("RERANKER_BACKEND not set or missing DASHSCOPE_API_KEY, using local model")
      return LocalModelLoader()
  ```

#### Task 4: 网络异常处理与降级逻辑

**文件**: `backend/rag/reranker.py`

- 在 `DashScopeReranker.rank()` 中捕获以下异常：
  - `requests.exceptions.Timeout` → 记录"API 超时" + 降级
  - `requests.exceptions.ConnectionError` → 记录"网络不可达" + 降级
  - `requests.exceptions.RequestException` → 记录"请求失败" + 降级
  - `ValueError/JSONDecodeError` → 记录"响应解析失败" + 降级
- 降级方法：
  ```python
  try:
      return self._call_api(query, documents, top_k)
  except Exception as e:
      logger.error(f"API error: {e}, falling back to local model")
      return self._fallback_to_local(query, documents, top_k)
  ```

#### Task 5: 统一 rerank() 接口

**文件**: `backend/rag/reranker.py`

- 修改全局 `rerank()` 函数签名保持不变（向后兼容）
- 内部根据 `get_reranker_backend()` 选择调用的后端
- 确保返回值始终为 `list[tuple[Document, float]]`

#### Task 6: 增强日志追踪

**文件**: `backend/rag/reranker.py`

- 每次调用记录：
  ```
  INFO: 使用 DashScope API 重排序 (query_len=XXX, doc_count=XXX, top_k=XXX)
  WARNING: API 超时 (5s), 降级到本地模型
  ERROR: API 返回 503, 降级到本地模型
  INFO: 本地模型懒加载完成
  DEBUG: 重排序结果 - [1] score=0.93 source=... chunk_id=...
  ```

#### Task 7: 环境配置迁移

**文件**:
- `backend/.env.example`
- `backend/.env` (if exists locally)

**改动**:
- 添加两行：
  ```bash
  RERANKER_BACKEND=dashscope
  DASHSCOPE_API_KEY=sk-ws-H.EYXEYIX.jEsu.MEQCIHMC54vqDxdqwgpKa4qgzLtZ6ANUgAIjPJvlUzorsDv1AiAfJJ2JrFjcdyVUWG77YJR9NMiITpaXTGdJcyjCp69bKw
  ```

### 2. 依赖检查

#### 是否已有 requests 库？

**检查项**:
- 查看 `pyproject.toml` 或 `requirements-dev.txt` 是否包含 `requests`
- 如果没有，需要在安装依赖时添加

**建议**:
```bash
pip install requests
```

或在 `pyproject.toml` 中添加：
```toml
dependencies = [
    "requests>=2.28.0",
    ...
]
```

---

## Error Handling Strategy (错误处理策略)

### 1. 降级触发条件表

| 异常类型 | 触发条件 | 日志级别 | 降级动作 |
|---------|---------|---------|---------|
| `Timeout` | API 调用 > 5 秒 | WARNING | 切到本地模型 |
| `ConnectionError` | DNS 解析失败/连接拒绝 | ERROR | 切到本地模型 |
| `HTTP 401` | API Key 无效 | ERROR | 切到本地模型 + 告警 |
| `HTTP 429` | 限流达到上限 | ERROR | 切到本地模型 + 告警 |
| `HTTP 5xx` | 服务端错误 | ERROR | 切到本地模型 |
| `JSONDecodeError` | 响应格式错误 | ERROR | 切到本地模型 |
| `IndexError` | results 数组越界 | ERROR | 切到本地模型 |

### 2. 降级后的 fallback 行为

```python
def _fallback_to_local(self, query, documents, top_k):
    """降级到本地模型的实现"""
    from sentence_transformers import CrossEncoder
    
    pairs = [(query, doc.page_content) for doc in documents]
    scores = self.local_model.predict(pairs)  # 已加载的 CrossEncoder
    
    # 后续处理与原来相同：sigmoid + 过滤 + 排序
    ...
    return scored_docs[:top_k]
```

### 3. 可观测性增强

- **Tracer 记录**：在 `tracer.start_span("rerank")` 中加入 `backend_type` 指标（`dashscope` / `local` / `fallback`）
- **Metrics 计数器**：记录 `rerank_api_errors_total{error_type="timeout|connection|http_401|http_429|http_5xx"}`
- **日志采样**：避免频繁重复日志（如连续 10 次 API 超时），使用 `logger.warning_once()` 类似的节流机制

---

## Configuration Requirements (配置要求)

### 1. 环境变量清单

| 变量名 | 默认值 | 用途 | 示例 |
|-------|--------|------|------|
| `RERANKER_BACKEND` | `dashscope` | 选择 reranker 后端 | `dashscope` 或 `local` |
| `DASHSCOPE_API_KEY` | N/A | DashScope API Key | `sk-ws-...` |
| `RERANK_TIMEOUT` | `5` 秒 | API 调用超时 | `5` |
| `RERANK_TOP_K` | `8` | 返回文档数 | `8` |
| `RERANK_SCORE_THRESHOLD` | `0.3` | 分数过滤阈值 | `0.3` |

### 2. 优先级顺序

1. **最高优先级**：`DASHSCOPE_API_KEY`存在且有效 → 使用 DashScope API
2. **第二优先级**：`RERANKER_BACKEND=local` → 强制使用本地模型
3. **兜底方案**：缺少 API Key 或后端配置 → 降级到本地模型

### 3. 安全注意事项

- **不要提交 `.env` 到 Git**：已在 `.gitignore` 中排除
- **开发环境**：可在本地调试时临时设置为 `local` 模式
- **生产环境**：必须配置有效的 `DASHSCOPE_API_KEY`

---

## File Modification List (文件修改清单)

| 文件 | 修改类型 | 改动行数估计 | 说明 |
|------|---------|------------|------|
| `backend/rag/reranker.py` | Modify | ~80 行新增 | 新增两个 reranker 类 + 工厂函数 + 降级逻辑 |
| `backend/.env.example` | Add | 2 行 | 新增环境变量模板 |
| `backend/.env` | Add | 2 行 | 实际配置（需手动添加 API Key） |

---

## Implementation Sequence (实施顺序建议)

### Phase 1: 依赖准备与测试环境搭建（10 分钟）

1. 检查并安装 `requests` 依赖
2. 验证 API Key 有效性（可用 curl 或 Postman 测试）
3. 创建备份：`cp backend/rag/reranker.py backend/rag/reranker.py.bak`

### Phase 2: 核心功能实现（30 分钟）

4. 实现 `LocalModelLoader` 懒加载单例
5. 实现 `DashScopeReranker` 类（基础调用）
6. 实现工厂函数 `get_reranker_backend()`
7. 集成到全局 `rerank()` 函数

### Phase 3: 异常处理与降级（20 分钟）

8. 添加 `try-catch` 包裹 API 调用
9. 实现 `fallback_to_local()` 方法
10. 完善日志输出（INFO/WARNING/ERROR）

### Phase 4: 配置与测试（15 分钟）

11. 更新 `.env.example` 和 `.env`
12. 运行单元测试（如果有）
13. 执行端到端测试：上传文档 → 查询 → 观察日志中的 rerank 行为

### Total Estimated Time: ~75 minutes (约 1.25 小时)

---

## Testing Checklist (测试验证项)

- [ ] API Key 配置正确，能成功调用 DashScope API
- [ ] API 超时模拟（可通过网络延迟脚本），触发降级并记录 WARNING 日志
- [ ] 断网场景，触发降级并记录 ERROR 日志
- [ ] 本地模型懒加载：重启服务后，首次调用才加载模型
- [ ] 分数格式验证：返回的 `score` 始终是 0-1 区间
- [ ] Top-K 限制验证：返回文档数不超过 `RERANK_TOP_K`
- [ ] 日志完整性：每次调用都有后端类型、是否降级、错误原因的记录

---

## Risk Assessment (风险评估)

| 风险点 | 可能性 | 影响程度 | 缓解措施 |
|-------|-------|---------|---------|
| API 不可用导致降级频繁 | 低 | 中 | 实现优雅降级，保证基本可用性 |
| 网络延迟增加响应时间 | 中 | 低 | 5 秒超时限制，快速回退到本地 |
| API Key 泄露风险 | 低 | 高 | 使用环境变量隔离，不提交到 Git |
| DashScope 费用意外激增 | 低 | 中 | 监控调用次数，设置配额预警 |

---

## Next Steps (下一步行动)

设计文档已完成。推荐采用**子代理驱动开发模式**（Subagent-Driven Development）执行实施计划：

1. 每个任务由一个独立子代理负责
2. 每阶段完成后进行 Code Review
3. 逐步迭代，及时发现问题

预计完整实施时间为 75 分钟。

---

**END OF DESIGN DOCUMENT**
