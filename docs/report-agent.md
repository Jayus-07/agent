# 报告生成 Agent

> 数据获取 + 模板渲染 + 图表 + LLM 润色。**数字/事实通过硬校验锁定，不依赖 LLM 承诺**。

## 1. 总览

```
report_agent/
├── report_generator.py    # 主编排器
├── template_engine.py     # Jinja2 模板渲染（含内置模板）
├── chart_generator.py     # matplotlib 图表 → base64
├── llm_polisher.py        # LLM 润色（带数字/事实硬校验）
├── data_fetcher.py        # 数据获取（SQLFetcher / APIFetcher）
├── preference.py          # 用户偏好学习（template / chart 选择）
└── snapshot.py            # 报告快照持久化
```

**设计原则**：
- LLM 仅做"语言润色"，数字和事实由 Python 硬校验锁定
- 模板用 Jinja2，图表用 matplotlib
- 用户偏好记录（"上次用什么模板/图表"）异步持久化

## 2. 流程

```
ReportGenerator.generate(report_type, filters, user_id, polish)
  1. _get_data()                  # data_fetcher.py: SQL/API 取数
  2. preference_store.get()        # 读用户偏好
  3. _render_template(data)        # template_engine.py: Jinja2 渲染
  4. _insert_charts(rendered)      # chart_generator.py: 插入图表
  5. _polish(rendered)             # llm_polisher.py: LLM 润色（可选）
  6. _verify_numbers(polished)     # llm_polisher.py: 数字硬校验
  7. save_snapshot()                # snapshot.py: 持久化
  8. preference.record()            # 记录用户选择
  9. cleanup_old_snapshots()        # 清理过期快照
  → Markdown 报告
```

## 3. 数据获取 (`data_fetcher.py`)

`REPORT_REGISTRY` 注册表 + 工厂模式：

```python
REPORT_REGISTRY = {
    "monthly_sales": {...},   # 内置报告类型
    "project_progress": {...},
    "dept_summary": {...},
    "budget_usage": {...},
}
```

每条注册项包含：
- `fetcher`: `SQLFetcher` / `APIFetcher`
- `template`: 模板名
- `chart_type`: 默认图表类型

### 3.1 SQLFetcher vs APIFetcher

```python
class SQLFetcher:
    def fetch(self, sql: str, params: dict = None) -> list[dict]:
        # 用 psycopg2 执行 SQL
        ...

class APIFetcher:
    def fetch(self, url: str, params: dict = None) -> list[dict]:
        # 用 requests 调外部 API
        ...
```

## 4. 模板渲染 (`template_engine.py`)

Jinja2 + 自定义过滤器：

| 过滤器 | 作用 |
|---|---|
| `money` | 数字 → 货币格式（¥1,234.56） |
| `percent` | 数字 → 百分比（12.3%） |
| `date_cn` | 日期 → 中文（2026-01-15 → 2026年1月15日） |
| `status_cn` | 状态码 → 中文（active → 进行中） |
| `truncate` | 文本截断 |
| `default_dash` | None → "—" |

**内置模板**（200+ 行硬编码 Markdown）：`monthly_sales.j2` / `project_progress.j2` / `dept_summary.j2` / `budget_usage.j2` / `sales_detail.j2`。

**列名校验**：渲染前检查数据是否包含必需列，缺失则用 `_fallback_render` 兜底。

## 5. 图表 (`chart_generator.py`)

matplotlib → base64 → 嵌入 Markdown 报告：

| 图表 | 触发条件 |
|---|---|
| `bar` | 数值对比（销售额、预算） |
| `pie` | 占比分析（部门占比、品类占比） |
| `line` | 趋势（时间序列） |

中文字体：`_setup_chinese_font` 自动选择系统字体（Microsoft YaHei / SimHei / Noto Sans CJK）。

## 6. LLM 润色 (`llm_polisher.py`)

**关键安全机制**：LLM 改写文本后，**硬校验数字/事实必须与原版一致**。

```python
# 1. 提取原文中的所有数字 + 关键事实
original_numbers = _extract_numerical_tokens(original)
original_facts = _extract_key_facts(original)

# 2. LLM 润色
polished = llm.invoke(POLISH_SYSTEM.format(content=original))

# 3. 硬校验
if not _verify_numbers(original_numbers, polished):
    polished = original  # 不通过则用原文
    logger.warning("[LLMPolisher] 数字校验失败，回退原文")
```

**失败重试**：最多 `max_retries` 次，每次重新提示 LLM "保持数字不变"。

**数字格式容忍**：`1,234.56` 和 `1234.56` 等价（正则 + 标准化后比对）；`10%` 容忍度（避免小数点末位差异）。

## 7. 用户偏好 (`preference.py`)

`PreferenceStore` JSON 持久化（路径 `data/report_preferences.json`）：

```json
{
  "user_001": {
    "monthly_sales": {
      "last_template": "sales_detail.j2",
      "last_chart_type": "bar",
      "usage_count": 12,
      "last_used": "2026-05-24T14:30:00"
    }
  }
}
```

- `get(user_id, report_type)`: 读偏好（默认空）
- `record(user_id, report_type, template, chart_type)`: 异步保存
- `reset(user_id, report_type)`: 重置

**注意**：
- `get_template_preference` / `get_chart_preference` 是便利方法（仅测试覆盖）
- 异步写用 `_save_async`（线程 daemon）

## 8. 快照 (`snapshot.py`)

报告生成后保存为 JSON 到 `data/snapshots/`，文件名 `{report_type}_{ts}.json`：

```json
{
  "report_type": "monthly_sales",
  "user_id": "user_001",
  "content": "...",
  "filters": {"month": "2026-05"},
  "saved_at": "2026-05-24T14:30:00"
}
```

- `save_snapshot(...)`: 保存
- `load_snapshot(snapshot_id)`: 读取
- `list_snapshots(report_type)`: 列出（仅 demo 消费）
- `cleanup_old_snapshots()`: 清理超过 `SNAPSHOT_RETENTION_DAYS` 的快照

**注意**：
- `load_latest_snapshot` 仅 demo 消费，生产不读快照
- 写失败记录 warning，不影响主报告生成

## 9. 关键函数

| 函数 | 作用 |
|---|---|
| `ReportGenerator.generate(...)` | 主编排入口 |
| `REPORT_REGISTRY` | 报告类型注册表 |
| `SQLFetcher.fetch(sql, params)` | 同步 SQL 数据获取 |
| `TemplateEngine.render(template, data)` | Jinja2 渲染 |
| `ChartGenerator.generate(chart_type, data, title)` | matplotlib → base64 |
| `LLMPolisher.polish(content)` | LLM 润色 + 硬校验 |
| `PreferenceStore.get/record` | 用户偏好读写 |
| `save_snapshot/load_snapshot` | 快照持久化 |

## 10. 修改指南

- **加新报告类型**：
  1. 在 `data_fetcher.py:REPORT_REGISTRY` 加条目（含 sql / template / chart_type）
  2. 在 `template_engine.py:_load_builtin_templates` 加 Jinja2 模板
  3. 在 `chart_generator.py` 加图表生成函数（如需要新类型）
- **改润色提示**：编辑 `llm_polisher.py:POLISH_SYSTEM`
- **改数字校验容忍度**：编辑 `llm_polisher.py:_verify_numbers`
- **改快照保留天数**：`snapshot.py:SNAPSHOT_RETENTION_DAYS`

## 11. 已知问题 / 待优化

- `report_generator.py` ~300 行，混 7 步流程（plan/待拆分）
- `template_engine.py` 464 行，混 Jinja2 过滤器 + 内置模板 + 目录扫描 + 列名校验 + 兜底渲染
- LLM 润色会阻塞 FastAPI handler（同步调用），未异步化
- 图表用 matplotlib（重，启动慢），生产可考虑 `pyecharts` 静态导出
- 用户偏好 JSON 文件读写有竞态风险（多进程）
