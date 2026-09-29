# P2: MinHash 增量相似度比对策略优化方案

## 目标
将 O(N²) 全量文档比对降为 O(N log N) 或 O(N)，显著提升大知识库下的去重检测效率。

---

## 方案一：基于时间窗口的增量比对（1 周内完成）

### 核心思想
- **仅对最近 N 天内上传的文档**进行 MinHash 相似度检测
- 历史文档视为"稳定状态"，不再参与实时比对
- 支持配置的时间窗口：`MINHASH_RECENT_DAYS = 7`

### 实现步骤

#### 1. 修改 doc_registry schema
```sql
ALTER TABLE doc_registry ADD COLUMN uploaded_at TEXT DEFAULT (datetime('now'));
CREATE INDEX idx_uploaded_at ON doc_registry(uploaded_at DESC);
```

#### 2. 查询最近 N 天内的同类型文档
```python
# backend/rag/indexing/doc_registry.py

def list_recent_by_doc_type(self, doc_type: str, days: int = 7) -> list[dict]:
    """获取最近 N 天内同类型的文档列表"""
    cutoff_date = datetime.now() - timedelta(days=days)
    
    with self._conn() as conn:
        rows = conn.execute("""
            SELECT file_path, doc_id, minhash_sig, file_hash, created_at
            FROM doc_registry
            WHERE doc_type = ? 
            AND datetime(created_at) >= datetime(?)
            AND status = 'active'
            ORDER BY created_at DESC
        """, (doc_type, cutoff_date.isoformat())).fetchall()
    
    return [dict(zip(["file_path", "doc_id", "minhash_sig", "file_hash", "created_at"], row)) 
            for row in rows]
```

#### 3. 集成到 indexer
```python
# backend/rag/indexing/indexer.py

# 在 _build_doc_metadata 中
near_dup_id = ""
if parent_span_id:
    dedup_minhash_span = trace_collector.start_span(
        'dedup_minhash', parent_id=parent_span_id, name="MinHash dedup (recent)",
        type="llm", kind=SpanKind.INDEX_DEDUP_MINHASH.value,
    )

# P2-3: 仅比对最近 7 天的同类型文档
recent_same_type = self.registry.list_recent_by_doc_type(doc_type, days=7)
for existing in recent_same_type:
    # ... 原有的相似度计算逻辑 ...

trace_collector.end_span(dedup_minhash_span, metrics={
    "comparison_scope": f"last_7_days",
    "candidates_count": len(recent_same_type),
})
```

### 性能提升预期
- **当前**: 对比 1000 份文档 → ~500ms
- **优化后**: 对比最近 50 份文档 → ~25ms  
- **提速**: 约 **20x**

---

## 方案二：引入 Faiss/Annoy 近似最近邻搜索（2 周内完成）

### 核心思想
- 使用向量检索库替代暴力遍历
- **Faiss** (Facebook): 支持 GPU/CPU，精度最高
- **Annoy** (Spotify): 轻量级，内存友好

### Faiss 快速原型实现

#### 1. 安装依赖
```bash
pip install faiss-cpu  # 或 faiss-gpu (如果有 NVIDIA GPU)
```

#### 2. 构建索引类
```python
# backend/rag/preprocessing/minhash_index.py

import faiss
import numpy as np
from typing import List, Tuple

class MinHashFaissIndex:
    """基于 Faiss 的 MinHash 近似最近邻搜索"""
    
    def __init__(self, dim: int = 128, metric: str = "L2"):
        """
        Args:
            dim: MinHash 签名维度 (默认 128)
            metric: "L2"(欧氏距离) 或 "IP"(内积)
        """
        self.dim = dim
        self.index = faiss.IndexFlat(dim)  # 线性扫描（小数据集）
        # self.index = faiss.IndexIVFFlat(dim, nlist=100)  # 倒排索引（大数据集）
        
    def add(self, signatures: List[List[int]], file_ids: List[str]):
        """批量添加签名"""
        # 转换为 numpy array
        data = np.array(signatures, dtype=np.float32)
        self.index.add(data)
        
    def search(self, query_sig: List[int], top_k: int = 5) -> List[Tuple[str, float]]:
        """搜索最相似的 K 个文档"""
        query = np.array([query_sig], dtype=np.float32)
        distances, indices = self.index.search(query, top_k)
        
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1:  # -1 表示无效索引
                similarity = 1.0 / (1.0 + dist)  # 距离→相似度的转换
                results.append((self.file_ids[idx], similarity))
        return results
```

#### 3. 集成策略
- **异步构建索引**: 后台任务定时更新 Faiss 索引
- **热加载**: 不阻塞主流程的文档上传
- **缓存预热**: 启动时加载常用类型的索引

### 性能提升预期
- **当前**: 1000 文档全量 → 500ms
- **Faiss IndexFlat**: 1000 文档 → 5ms (**100x**)
- **Faiss IVF**: 10000 文档 → 10ms (**500x**)

---

## 配置参数设计

```python
# backend/config/rag.py

# P2-3: MinHash 去重配置
MINHASH_ENABLED = True                 # 总开关
MINHASH_RECENT_DAYS = 7               # 时间窗口天数
MINHASH_SIMILARITY_THRESHOLD = 0.85   # 相似度阈值
MINHASH_MAX_COMPARISONS = 100         # 单次最大比对数（防御性）
MINHASH_USE_FAISS = False             # 是否启用 Faiss（未来升级）
MINHASH_FAISS_NLIST = 100             # Faiss 聚类簇数（如果启用）
```

---

## 测试验证计划

### 单元测试
```python
# tests/rag/test_minhash_recent.py

def test_recent_only_comparison():
    """验证只比对最近 7 天的文档"""
    indexer = create_test_indexer()
    
    # 创建 30 天前的文档
    old_doc = create_test_file("old.txt", days_ago=30)
    
    # 创建今天的文档
    new_doc = create_test_file("new.txt")
    
    # 上传新文档
    result = indexer.reindex_file(new_doc.path)
    
    # 验证未命中旧文档
    assert result["near_dup_id"] == ""
```

### 基准测试
```python
def benchmark_minhash_performance():
    """比较三种策略的性能"""
    test_cases = [
        ("Full scan (1000 docs)", 1000, "full"),
        ("Recent 7 days (50 docs)", 50, "recent"),
        ("Faiss IndexFlat (1000 docs)", 1000, "faiss_flat"),
    ]
    
    for name, doc_count, strategy in test_cases:
        start = time.time()
        run_minhash_test(doc_count, strategy)
        elapsed = time.time() - start
        print(f"{name}: {elapsed:.3f}s")
```

---

## 回滚策略

- 若 Faiss 引入后出现稳定性问题，可立即切换回 `MINHASH_USE_FAISS = False`
- 保留原有全量比对逻辑作为 fallback
- 通过日志监控误报率，确保不影响业务质量

---

## 下一步行动

1. **Week 1**: 实现时间窗口策略（纯 Python，零外部依赖）
2. **Week 2**: 评估 Faiss vs Annoy，选择最佳方案并原型验证
3. **Week 3**: 完整集成 + 基准测试 + 生产环境部署
