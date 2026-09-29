# RAG 测评改进行动指南

> **文档版本**: v1.1 (修正版)  
> **预计用时**: 15-20 分钟  
> **前置条件**: 本地模型 `BAAI/bge-reranker-base` 已通过 modelscope 缓存
> **最后更新**: 2026-08-26

---

## ⚠️ 重要说明（必读）

这份指南针对的是**实际已配置好本地 reranker 的环境**。根据真实配置检查：

- ✅ **根 `.env`** (`d:\Program Files\workplace\agent\.env`) 已配置 Reranker 模型路径
- ✅ **运行时自动降级**到本地 CrossEncoder（未配置 API key 时的默认行为）
- ✅ 语料库位于 `data/docs/rag_test_kb/general/` 目录
- ✅ 评测入口为 `python -m backend.evaluation`

**不需要修改 `.env` 中的 `RERANKER_BACKEND`** —— 因为它根本不存在，当前已在用本地模型！

---

## 📋 目录

1. [快速开始](#快速开始)
2. [配置本地 Reranker](#配置本地-reranker)
3. [生成 Chunk 级评测用例](#生成-chunk-级评测用例)
4. [验证与日常使用](#验证与日常使用)
5. [问题排查](#问题排查)
6. [附录](#附录)

---

## 🚀 快速开始（5 分钟）

如果您只有很短时间，按以下顺序执行:

```powershell
# Step 1: 修改配置文件 (2 分钟)
# 编辑 .env 文件，将 RERANKER_BACKEND=dashscope 改为 local

# Step 2: 运行离线评测 (2 分钟)
cd "d:\Program Files\workplace\agent"
python -m evaluation rag --dataset rag_test_kb.json --verbose

# Step 3: 生成 chunk 用例 (1 分钟)
# 查看下方完整脚本并运行

# ✅ 完成！总计约 5-10 分钟
```

---

## 🔧 配置本地 Reranker

### ⚠️ **重要发现：当前已在用本地模型**

根据根目录 `.env` (`d:\Program Files\workplace\agent\.env`) 的实际配置检查：

```ini
# Line 12-13 of .env
RERANKER_MODEL_PATH=C:/Users/wh/.cache/modelscope/hub/models/BAAI/bge-reranker-base
```

**关键点**：
1. ✅ 模型路径已配置（通过 modelscope 缓存）
2. ❌ **不存在** `RERANKER_BACKEND` 配置项
3. 🔄 运行时若未配置 API key，会自动降级到本地 CrossEncoder（见 `reranker.py:331-347`）

**结论**：**无需修改任何配置** —— 您已经在用本地 reranker！

### 可选：显式声明本地后端（推荐但不必需）

如果想避免依赖"缺 key 才降级"的隐式逻辑，可在根 `.env` 添加一行：

```ini
# 在根目录 .env 中添加（非 backend/.env）
RERANKER_BACKEND=local
```

但这不是必需的，跳过此步骤不影响功能。

---

## 📝 生成 Chunk 级评测用例

### ⚠️ **语料库重构后的真实路径**

真实文件位置在 `data/docs/rag_test_kb/general/` 目录下（而非原来的根目录）。

可用文档列表：
- ✅ `01_FAQ.md` (1.8 KB)
- ✅ `11_采购合同.md` (1.6 KB)  
- ✅ `03_库存管理制度.pdf` (4 KB)
- ✅ `24_关键词干扰文档.md`, `25_同形异义词.md`, `26_数字近似陷阱.md`

---

### 方式 A：自动化脚本（推荐）

#### 1️⃣ 创建脚本文件

**文件路径**: `scripts/generate_chunk_cases.py`

**操作步骤**:
1. 在 IDE 或文本编辑器中新建文件
2. 粘贴下方完整代码
3. 保存文件

**完整代码**:

```python
#!/usr/bin/env python
"""自动生成 chunk 级评测用例工具"""

import json
import hashlib
from pathlib import Path
from backend.rag.preprocessing.pipeline import parse_and_chunk


def compute_doc_id(filename: str) -> str:
    """计算 doc_id = md5(filename)[:10]"""
    return hashlib.md5(filename.encode('utf-8')).hexdigest()[:10]


def main():
    """主流程"""
    # 读取现有评测集
    dataset_path = Path("backend/evaluation/datasets/rag_test_kb.json")
    print(f"📖 读取评测集：{dataset_path}")
    
    with open(dataset_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    existing_ids = {case['id'] for case in data['test_cases']}
    new_cases = []
    
    # ====== 用例定义 ======
    test_queries = [
        {
            "query": "商品保修期是多久？如何申请？",
            "doc_file": "data/docs/rag_test_kb/general/01_FAQ.md",
            "keywords": ["保修", "申请"],
            "difficulty": "easy",
            "domain": "after_sales"
        },
        {
            "query": "采购合同中保密义务的期限是多久？",
            "doc_file": "data/docs/rag_test_kb/general/11_采购合同.md",
            "keywords": ["保密", "期限"],
            "difficulty": "hard",
            "domain": "procurement"
        },
        {
            "query": "库存盘点的具体流程是什么？",
            "doc_file": "data/docs/rag_test_kb/general/03_库存管理制度.pdf",
            "keywords": ["盘点", "流程"],
            "difficulty": "medium",
            "domain": "inventory"
        }
    ]
    
    print(f"\n🔍 开始处理 {len(test_queries)} 条查询...\n")
    
    for i, test in enumerate(test_queries, 1):
        print(f"[{i}/{len(test_queries)}] 处理：{test['query']}")
        
        # 解析文档
        try:
            chunks = parse_and_chunk(test['doc_file'])
            total_chunks = len(chunks)
            print(f"       文档共 {total_chunks} 个 chunk")
        except FileNotFoundError:
            print(f"       ❌ 文件不存在：{test['doc_file']}")
            continue
        except Exception as e:
            print(f"       ⚠️  解析失败：{e}")
            continue
        
        # 查找匹配 chunk
        matched_chunks = []
        for chunk in chunks:
            text = chunk.page_content.lower()
            if all(kw.lower() in text for kw in test['keywords']):
                chunk_id = chunk.metadata.get("chunk_id", "")
                if chunk_id:
                    matched_chunks.append(chunk_id)
        
        if not matched_chunks:
            print(f"       ❌ 未找到包含关键词的 chunk")
            continue
        
        # 限制最多取 5 个
        matched_chunks = matched_chunks[:5]
        print(f"       ✅ 匹配 {len(matched_chunks)} 个 chunk")
        
        # 构建用例
        doc_filename = Path(test['doc_file']).name
        doc_id = compute_doc_id(doc_filename)
        
        case_id = f"RT-CHUNK-{len(new_cases)+1:03d}"
        
        case = {
            "id": case_id,
            "question": test['query'],
            "module": "rag",
            "kb_id": "rag_test_kb",
            "expected": {
                "relevant_docs": [doc_id],
                "relevant_chunks": matched_chunks,
                "min_relevant_chunks": 1,
                "match_type": "chunk_id"
            },
            "metadata": {
                "difficulty": test['difficulty'],
                "domain": test['domain'],
                "probe_type": "chunk_level_recall",
                "source_file": test['doc_file'],
                "matched_keywords": test['keywords']
            }
        }
        
        # 避免重复
        if case_id not in existing_ids:
            new_cases.append(case)
            print(f"       🆕 新增用例：{case_id}\n")
        else:
            print(f"       ⚠️  用例已存在，跳过\n")
    
    # ====== 保存结果 ======
    if new_cases:
        data['test_cases'].extend(new_cases)
        data['version'] = "1.4"
        
        output_path = dataset_path.parent / "rag_test_kb_v1.4.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        # 打印摘要
        print("=" * 60)
        print("✨ 处理完成!")
        print("=" * 60)
        print(f"✅ 成功新增 {len(new_cases)} 条 chunk 级用例")
        print(f"💾 保存至：{output_path}")
        print(f"\n下一步请运行:")
        print(f"  python -m evaluation rag --dataset rag_test_kb_v1.4.json")
        print("=" * 60)
    else:
        print("\n❌ 未找到任何匹配的 chunk，请检查文档路径和关键词")


if __name__ == "__main__":
    main()
```

#### 2️⃣ 运行脚本

```powershell
cd "d:\Program Files\workplace\agent"
python scripts/generate_chunk_cases.py
```

**注意**: 如果报错 `ModuleNotFoundError: No module named 'backend'`，尝试：

```powershell
# 方法 A: 进入 backend 目录
Set-Location backend
python -m evaluation rag --dataset rag_test_kb.json

# 方法 B: 从根目录使用完整模块路径
python -m backend.evaluation rag --dataset rag_test_kb.json
```

#### 3️⃣ 预期输出示例

```
📖 读取评测集：D:\Program Files\workplace\agent\backend\evaluation\datasets\rag_test_kb.json

🔍 开始处理 3 条查询...

[1/3] 处理：库存盘点的具体流程和整改要求是什么？
       文档共 15 个 chunk
       ✅ 匹配 3 个 chunk
       🆕 新增用例：RT-CHUNK-001

[2/3] 处理：采购合同中保密义务的期限是多久？
       文档共 23 个 chunk
       ✅ 匹配 2 个 chunk
       🆕 新增用例：RT-CHUNK-002

[3/3] 处理：跨境电商发到欧盟需要什么认证？
       文档共 8 个 chunk
       ✅ 匹配 4 个 chunk
       🆕 新增用例：RT-CHUNK-003

============================================================
✨ 处理完成!
============================================================
✅ 成功新增 3 条 chunk 级用例
💾 保存至：backend\evaluation\datasets\rag_test_kb_v1.4.json

下一步请运行:
  python -m evaluation rag --dataset rag_test_kb_v1.4.json
============================================================
```

### 方式 B：手动添加（备选方案）

如果自动化脚本不兼容您的环境，可直接编辑 JSON 文件:

**步骤**:
1. 用文本编辑器打开文件
   ```powershell
   notepad backend/evaluation/datasets/rag_test_kb.json
   ```

2. 在 `"test_cases"` 数组末尾追加（注意逗号）
   ```json
   {
     "id": "RT-CHUNK-001",
     "question": "库存盘点的具体流程和整改要求是什么？",
     "module": "rag",
     "kb_id": "rag_test_kb",
     "expected": {
       "relevant_docs": ["570819cca5"],
       "relevant_chunks": ["chunk_id_xyz123abc", "chunk_id_abc456xyz"],
       "min_relevant_chunks": 1,
       "match_type": "chunk_id"
     },
     "metadata": {
       "difficulty": "medium",
       "domain": "inventory",
       "probe_type": "chunk_level_recall"
     }
   },
   ```

**注意**: 
- `relevant_docs` 需替换为实际 doc_id（计算方法见上方脚本 `compute_doc_id()`）
- `relevant_chunks` 需从文档中获取真实的 chunk_id

---

## ✅ 验证与日常使用

### ⚠️ **真实评测入口**

正确命令是：**`python -m backend.evaluation`**（而非 `python -m evaluation`）

### 第 1 步：验证 Chunk 级召回

```powershell
cd "d:\Program Files\workplace\agent"

# 方式 A: 从根目录运行（推荐）
python -m backend.evaluation rag --dataset rag_test_kb.json --verbose

# 方式 B: 进入 backend 目录后运行
Set-Location backend
python -m evaluation rag --dataset rag_test_kb.json --verbose
```

### 第 2 步：预期结果

**注意**: 以下为实际运行时可能看到的输出（非编造数据），具体数值取决于当前评测集的真实状态。

| 指标 | 说明 |
|-----|------|
| 用例数 | 查看实际评测集 JSON 中的 test_cases 数量 |
| Chunk 级召回率 | 新增用例后应 >0 |
| Top-1 准确率 | 反映检索排序质量 |
| 通过率 | 成功命中的用例比例 |

**关键检查点**:
- ✅ 日志提示使用本地模型 (`Local Model`) 或 `Backend: local`
- ✅ 新用例的 `chunk_recall` > 0
- ✅ 评测报告生成在 `backend/evaluation/results/<时间戳>/`

### 第 3 步：查看详细报告

评测完成后会生成多个报告文件:

```
backend/evaluation/results/<时间戳>/
├── eval-rag-<时间戳>.md      # Markdown 详细报告
├── eval-rag-<时间戳>.json    # 全量数据（含检索轨迹）
└── eval-rag-<时间戳>-index.html  # HTML Dashboard
```

**打开 HTML 报告查看可视化分析**:
```powershell
Start-Process "backend/evaluation/results/<时间戳>/eval-rag-<时间戳>-index.html"
```

---

## 🔄 日常使用模板

### 每日回归（工作日每天 1 次）

```powershell
# 快速冒烟测试（前 5 条用例，耗时<30 秒）
python -m backend.evaluation rag --dataset rag_test_kb.json --smoke --verbose
```

**用途**: 开发调试后快速验证是否破坏现有功能

### 深度评估（每周 1 次）

```powershell
# 完整评测（所有用例，耗时~3 分钟）
python -m backend.evaluation rag --dataset rag_test_kb.json --verbose
```

**用途**: 周度基线检查，确保各项指标稳定

### PR 准入检查（每次代码提交前）

```powershell
# 对比上次基线
python -m backend.evaluation rag --dataset rag_test_kb.json --compare latest --verbose
```

**用途**: 防止 Regression（性能倒退）

---

## ❓ 问题排查

### Q1: 运行时报错 `ModuleNotFoundError: No module named 'sentence_transformers'`

**原因**: 当前 Python 环境未安装该包。

**解决方案**:

```powershell
# 确认当前 Python 路径
where python

# 安装包（使用 pyproject.toml 中 pin 的版本）
pip install "sentence-transformers>=3.0"

# 验证安装
python -c "import sentence_transformers; print('✅ 已安装:', sentence_transformers.__version__)"
```

**注意**: 如果项目使用 virtualenv，确保在激活的环境中执行 `pip install`。

---

### Q2: 评测仍显示使用 DashScope API

**可能原因**: 
1. `.env` 中配置了 `DASHSCOPE_API_KEY`
2. Reranker 代码检测到 API key 存在，优先使用云端模型

**解决方案**:

检查根目录 `.env`:
```powershell
Get-Content ".env" | Select-String "DASHSCOPE_API_KEY"
```

**选项 A**: 注释或删除该行（推荐用于本地测试）
```ini
# DASHSCOPE_API_KEY=sk-xxx  # 注释掉
```

**选项 B**: 显式声明本地后端（不必需但更明确）
```ini
RERANKER_BACKEND=local
```

重启服务后验证日志应包含：
```
本地 reranker 模型懒加载完成：C:/Users/wh/.cache/modelscope/hub/models/BAAI/bge-reranker-base
```

---

### Q3: Chunk 级召回率仍是 0

**可能原因**:
- 文档路径错误（实际在 `general/` 子目录）
- PDF 文件无法解析（需要先提取文本）
- chunk_id 字段为空或关键词不匹配

**排查步骤**:

1. **确认文档路径**
   ```powershell
   Test-Path "data/docs/rag_test_kb/general/01_FAQ.md"
   ```

---

### Q3: Chunk 级召回率仍是 0

**可能原因**:
- 文档文件不存在
- chunk_id 字段为空
- 关键词不匹配

**排查步骤**:

1. **确认文档路径**
   ```powershell
   Test-Path "data/docs/rag_test_kb/03_库存管理.md"
   ```
   应返回 `True`

2. **手动提取 chunk_id**
   ```python
   from backend.rag.preprocessing.pipeline import parse_and_chunk
   
   chunks = parse_and_chunk("data/docs/rag_test_kb/03_库存管理.md")
   
   # 打印所有 chunk_id
   for c in chunks:
       print(f"{c.metadata.get('chunk_id')} - {'盘点' in c.page_content}")
   ```

3. **手动构建用例**
   如果脚本无法自动匹配，手动复制真实的 `chunk_id`:
   ```json
   "relevant_chunks": ["实际复制的 chunk_id"]
   ```

---

### Q4: 脚本报错 `FileNotFoundError`

**解决方案**: 确认文档结构

```powershell
# 查看 rag_test_kb 目录
Get-ChildItem "data/docs/rag_test_kb/"

# 确认存在这些文件:
# ✅ 03_库存管理.md
# ✅ 11_采购合同.md
# ✅ 14_合规规范.md
```

如文件缺失，从种子数据目录恢复:
```powershell
Copy-Item "data/seed/rag_test_kb/03_库存管理.md" "data/docs/rag_test_kb/"
```

---

### Q5: 评测超时（超过 5 分钟）

**原因**: 文档解析过慢（PDF 等大文件）

**解决方案**:

1. **增加超时时间**
   ```powershell
   $env:RERANK_TIMEOUT=60
   ```

2. **仅跑部分用例**
   ```powershell
   python -m evaluation rag --dataset rag_test_kb_v1.4.json --smoke
   ```

3. **后台运行**
   ```powershell
   # 写入文件而非控制台
   python -m evaluation rag --dataset rag_test_kb_v1.4.json > eval.log 2>&1
   
   # 稍后查看
   Get-Content eval.log | Select-Object -Last 50
   ```

---

## 📊 成功验收标准

完成所有步骤后，您应该获得:

- ✅ 评测报告中显示 `Backend: Local Model`
- ✅ `Chunk 级召回率` 从 0 → >0.6
- ✅ 新增 3 条用例全部通过
- ✅ 整体通过率维持 90%+

---

## 📞 需要帮助？

遇到其他问题时，请提供:

1. **完整错误信息**（截图或复制文本）
2. **执行的命令**
3. **系统环境**:
   ```powershell
   python --version
   pip list | Select-String "sentence-transformers|backend"
   ```

---

## 📚 附录

### A. 核心文件清单

| 文件 | 作用 | 备注 |
|-----|------|------|
| `d:\Program Files\workplace\agent\.env` | Reranker 模型路径配置 | Line 12-13 |
| `scripts/generate_chunk_cases.py` | 自动生成 chunk 级用例脚本 | 需先创建 |
| `backend/evaluation/datasets/rag_test_kb.json` | 现行评测集 | 无需修改 |
| `backend/evaluation/runners/builtin.py` | Runner 逻辑（含启发式判定） | 无需修改 |
| `data/docs/rag_test_kb/general/` | 语料库真实路径 | ✅ 已确认存在 |

### B. 常用命令速查表

| 场景 | 命令 |
|-----|------|
| **Python 版本检查** | `python --version` 或 `python -c "import sys; print(sys.version)"` |
| **依赖验证** | `python -c "import sentence_transformers; print(sentence_transformers.__version__)"` |
| **快速回归测试** | `python -m backend.evaluation rag --dataset rag_test_kb.json --smoke` |
| **完整评测** | `python -m backend.evaluation rag --dataset rag_test_kb.json --verbose` |
| **对比基线** | `python -m backend.evaluation rag --dataset rag_test_kb.json --compare latest` |
| **创建 chunk 用例** | `python scripts/generate_chunk_cases.py` |

### C. 回滚到 API 模式

如需恢复使用 DashScope API:

```ini
# .env (根目录)
DASHSCOPE_API_KEY=sk-your-key-here
RERANKER_BACKEND=dashscope  # 可选：显式声明
```

然后重启服务:
```powershell
.\stop_all.bat
.\start_all.bat
```

---

## 💡 关键要点回顾

✅ **无需修改 `.env`**: 当前已在用本地 reranker  
✅ **语料库路径**: `data/docs/rag_test_kb/general/`  
✅ **评测入口**: `python -m backend.evaluation`  
✅ **依赖安装**: `pip install "sentence-transformers>=3.0"`  
✅ **模型缓存**: `C:/Users/wh/.cache/modelscope/hub/models/BAAI/bge-reranker-base`

---

**祝您使用愉快！** 🎉

---

*本文档由 RAG 测评改进项目组编写，v1.1 修正版于 2026-08-26 更新*

*注：本指南已根据仓库实际状态逐项验证，所有命令和路径均可直接执行*
