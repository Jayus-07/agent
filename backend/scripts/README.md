# backend/scripts/ — 一次性运维脚本目录

> 这里放**不进入生产代码路径**的运维脚本：
> 数据清理、迁移、批量修复、人工触发的诊断脚本等。
>
> 区别于 `backend/scripts/` 的"应用脚本"：
> - 这些脚本**直接调项目内部模块**（如 `doc_registry`、`chroma` 客户端），不做 HTTP API 调用
> - 不进入 `requirements.txt`（脚本本身就是 entry point）
> - 不写单测（一次性的，但必须留痕 + 幂等）

---

## 当前脚本

| 脚本 | 用途 | 是否幂等 |
|---|---|---|
| `cleanup_tmpnl_residuals.py` | 清理 GBK 编码残留的 `tmpnl*_测评上传入库_*.md` 文件 + registry + ChromaDB + chunk_store | ✅ 重复运行 no-op |

---

## 添加新脚本的规范

1. **必须有 `if __name__ == "__main__"` 入口**（便于 `python -m` 调用）
2. **必须留痕**：用 `backend.shared.logger.logger.info/warning/error`，不要 print
3. **必须幂等**：重复运行不会出错或堆积
4. **必须说明退出码**：0 = 成功 / 无需处理；非 0 = 失败（让 CI / 定时任务能检测）
5. **不要硬编码路径**：用 `backend.config.database.*` 等配置常量，不要写死 `D:\...`
6. **必须处理 except**：`except Exception: pass` 禁止（项目核心原则）。任何 except 必须有日志或重新 raise

## 模板

```python
"""脚本名 — 一句话说明做什么。

背景:为什么需要这个脚本,何时使用。
幂等:重复运行 no-op / 不会损坏数据。
"""
from __future__ import annotations

import sys
sys.path.insert(0, '.')

from backend.shared.logger import logger
from backend.config.database import DOCS_DIRECTORY
# ... 其他依赖

def main() -> int:
    # 业务逻辑
    return 0  # 成功

if __name__ == "__main__":
    sys.exit(main())
```

## 调用方式

```bash
# 从项目根目录
cd D:\Program Files\workplace\agent
python backend\scripts\cleanup_tmpnl_residuals.py

# 配合 sys.path
PYTHONPATH=. python backend/scripts/cleanup_tmpnl_residuals.py
```

## 何时不应该放到这里

- **频繁调用的逻辑**：放进 `backend/<module>/` 成为正式 API
- **HTTP 接口能完成的**：直接用 API，不要绕道
- **生产 cron 调度的**：考虑放进 `backend/app/api/routes/` 暴露一个 trigger 接口，或用独立 worker