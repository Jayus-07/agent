# 第 6 课：报告生成系统

> 读完这篇你能回答：
> 1. 报告生成的 6 步流程是什么？每步解决什么问题？
> 2. LLM 润色的"提取→锁定→校验"硬机制是怎么防止篡改数据的？
> 3. 面试官问"如何保证 LLM 润色报告时不改变数据"怎么答？

---

## 1. 模块职责（Why）

### 一句话概括

**从数据库/API 拉取结构化数据 → Jinja2 模板渲染为 Markdown 初稿 → matplotlib 嵌入图表 → LLM 润色语言（硬校验保证数据不改）→ 输出专业报告。**

### 解决什么问题

| 问题 | 没有报告系统时 | 有报告系统后 |
|---|---|---|
| 重复劳动 | 每次手动查 SQL → 复制到 Excel → 写报告 | 一句话生成："生成本月销售日报" |
| 格式不统一 | 不同人写的报告风格各异 | 统一模板，统一格式 |
| 数据容易错 | 手动复制粘贴可能少一行 | 数据直接来自数据库，不错不漏 |
| 语言不专业 | 数据分析师写出干巴巴的表格 | LLM 润色语言，专业+流畅 |
| 无法回溯 | 上个月的数据查不到了 | 每次生成自动存档 JSON 快照 |

### 6 步流水线

```
① 用户偏好 → 选模板 + 图表类型
② DataFetcher → 拉取 SQL/API 数据 → JSON
③ Snapshot → 保存 JSON 快照（可回溯）
④ TemplateEngine → Jinja2 渲染 → Markdown 初稿
⑤ ChartGenerator → matplotlib → base64 嵌入
⑥ LLMPolisher → 润色 + 数值锁定校验 → 最终报告
```

---

## 2. 整体流程（Flow）

```mermaid
sequenceDiagram
    participant User as Agent/用户
    participant Gen as ReportGenerator
    participant Pref as PreferenceStore
    participant Fetch as SQLFetcher
    participant PG as PostgreSQL
    participant Snap as Snapshot
    participant Tmpl as TemplateEngine
    participant Chart as ChartGenerator
    participant Polisher as LLMPolisher
    participant LLM as LLM

    User->>Gen: generate("daily_sales", {month: "2026-07"})

    Note over Gen,Pref: ① 查询偏好
    Gen->>Pref: get(user_id, "daily_sales")
    Pref-->>Gen: {last_template: "daily_sales.j2"}

    Note over Gen,PG: ② 拉取数据
    Gen->>Fetch: fetch(source_config, filters)
    Fetch->>PG: SELECT ... FROM orders WHERE ...
    PG-->>Fetch: 查询结果
    Fetch-->>Gen: {data: [...], metadata: {row_count: 15, elapsed_ms: 120}}

    Note over Gen,Snap: ③ 保存快照
    Gen->>Snap: save_snapshot("daily_sales", result, filters)

    Note over Gen,Tmpl: ④ 模板渲染
    Gen->>Tmpl: render("daily_sales", result, template="daily_sales.j2")
    Tmpl->>Tmpl: Jinja2 SandboxedEnvironment
    Tmpl-->>Gen: Markdown 初稿

    Note over Gen,Chart: ⑤ 图表嵌入
    Gen->>Chart: generate(chart_configs, data)
    Chart->>Chart: matplotlib bar/line/pie → base64
    Chart-->>Gen: ![图表](data:image/png;base64,...)
    Gen->>Gen: _insert_charts(draft, chart_md)

    Note over Gen,LLM: ⑥ LLM 润色 + 硬校验
    Gen->>Polisher: polish(draft)
    Polisher->>Polisher: 提取原始数值 token
    Polisher->>LLM: POLISH_SYSTEM + draft
    LLM-->>Polisher: 润色后的 Markdown
    Polisher->>Polisher: 提取润色后数值 → 逐条比对
    alt 校验通过
        Polisher-->>Gen: 润色完成
    else 校验失败
        Polisher->>LLM: 重试（最多 2 次）
        Polisher-->>Gen: 校验失败 → 安全回退原稿
    end

    Gen-->>User: 最终 Markdown 报告
```

---

## 3. 技术选型（Why This Tech）

### 为什么用 Jinja2 而不是手拼字符串？

| 方案 | 优点 | 缺点 |
|---|---|---|
| 手拼字符串 | 简单 | 模板和数据混在一起，改格式要改代码 |
| **Jinja2** | 模板与数据分离，设计师可以独立改模板 | 多一层学习成本 |
| WeasyPrint | 直接生成 PDF | 太重，只需要 Markdown |

**选择 Jinja2 的原因：**
- **SandboxedEnvironment** — 沙箱模式，模板不能执行任意 Python 代码
- **自定义过滤器** — `money`、`percent`、`date_cn` 等，模板里直接 `{{ value | money }}`
- **内置模板兜底** — 即使 `templates/` 目录为空，6 种报告类型都有内置默认模板

### 为什么 LLM 润色要加硬校验？

**问题：** LLM 润色时可能"顺手"修改数据：

```
润色前: "销售额为 1,234.56 万元"
润色后: "销售额约为 1,200 万元"  ← LLM "美化"了数字！
```

**解决方案：提取→锁定→校验**

```python
# ① 润色前：提取所有数值 token
original_numbers = {"1,234.56", "2026-07-10", "85.5%", "¥1000"}

# ② LLM 润色

# ③ 润色后：再次提取数值 token
polished_numbers = {"1,234.56", "2026-07-10", "85.5%", "¥1000"}

# ④ 逐条比对
if missing := original_numbers - polished_numbers:
    logger.warning(f"丢失数值: {missing}")
    return draft  # 安全回退到原稿
```

**这不是"建议"，是硬拦截。** 丢了数字的直接拒绝。

### 为什么用 matplotlib 而不是 ECharts？

| 方案 | 优点 | 缺点 |
|---|---|---|
| **matplotlib** | Python 原生，无外部依赖 | 静态图，不能交互 |
| ECharts | 交互式，好看 | 需要前端渲染，不适合纯 Markdown |
| Plotly | 交互式 | 输出 HTML，Markdown 不兼容 |

**选择 matplotlib 的原因：** 报告输出是 Markdown，只能嵌入静态图片（base64 PNG）。matplotlib 直接生成 PNG → base64 → `![](data:image/png;base64,...)` 一行搞定。

### 为什么需要数据快照？

| 场景 | 价值 |
|---|---|
| 用户说"上次报告的数据不对" | 找到当时的 JSON，对比现在 |
| 审计"谁查了什么数据" | 快照文件名含时间戳 + UUID |
| 报表趋势分析 | 每周的快照可以做趋势图 |

快照自动 30 天清理（`REPORT_SNAPSHOT_DAYS=30`），防止磁盘爆满。

---

## 4. 核心源码解析（How）

### 阶段 1：报告注册表（data_fetcher.py:32-230）

```python
# data_fetcher.py:32-60 — 每种报告类型集中定义
REPORT_REGISTRY = {
    "daily_sales": {
        "name": "销售日报",
        "source": {"type": "sql", "sql": "SELECT ... FROM orders ..."},
        "templates": ["daily_sales.j2"],
        "charts": [
            {"type": "line", "x": "日期", "y": "销售额", "title": "近7日销售额趋势"},
        ],
    },
    # ... 共 6 种报告类型
}
```

**这是报告系统的"唯一真相源"** — 新增报告类型只需在这里加一个条目，不改任何业务代码。

### 阶段 2：6 步生成流水线（report_generator.py:89-203）

```python
# report_generator.py:89-203 — generate() 主入口
def generate(self, report_type, filters, user_id, polish=True):
    # ① 查询用户偏好 → 选模板
    user_pref = preference_store.get(user_id, report_type)
    preferred_template = user_pref.get("last_template")

    # ② 拉取数据
    fetcher = get_fetcher(report_config["source"], db_config=self.db_config)
    result = fetcher.fetch(report_config["source"], filters)

    # ③ 保存快照
    if self.snapshot_enabled:
        save_snapshot(report_type, result, filters)

    # ④ 模板渲染
    draft = self.template_engine.render(report_type, result,
                                        template_name=preferred_template)

    # ⑤ 图表嵌入（插入到第一个 ## 标题前）
    if chart_configs and result["data"]:
        chart_md = chart_generator.generate(chart_configs, result["data"])
        draft = self._insert_charts(draft, chart_md)

    # ⑥ LLM 润色 + 硬校验
    if polish:
        final = llm_polisher.polish(draft)

    # ⑦ 记录偏好
    preference_store.record(user_id, report_type,
                            template_name=..., chart_type=...)

    return final
```

### 阶段 3：SQLFetcher 数据获取（data_fetcher.py:261-323）

```python
# data_fetcher.py:261-323
class SQLFetcher(DataFetcher):
    def fetch(self, config, filters=None):
        filters = filters or {}
        sql = config["sql"]

        conn = psycopg2.connect(**self.db_config)
        conn.set_session(readonly=True, autocommit=True)  # 只读

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 只注入 SQL 中实际用到的占位符
            used_params = set(re.findall(r"%\((\w+)\)s", sql))
            params = {k: v for k, v in filters.items() if k in used_params}

            cur.execute(sql, params)
            data = [dict(r) for r in cur.fetchall()]

        return {"data": data, "metadata": {"row_count": len(data), ...}}
```

**`set_session(readonly=True)` — 报告查询也是只读的。** 和 SQL Agent 的 executor 一样的安全实践。

**`only_used_params` 过滤 —** 防止 psycopg2 报"参数多余"的错误。如果 filters 传了 `{"month": "2026-07", "channel": "Amazon"}` 但 SQL 只用了 `%(month)s`，`channel` 不会被传进去。

### 阶段 4：Jinja2 渲染 + 列名校验（template_engine.py:367-442）

```python
# template_engine.py:367-442
def render(self, report_type, result, template_name=None):
    template_str = self.get_template(report_type, preferred=template_name)

    # 列名校验：缺列时降级到 fallback
    data = result.get("data", [])
    if data:
        cols_ok, missing = self._check_required_columns(template_name, data)
        if not cols_ok:
            return self._fallback_render(result, f"缺少列: {missing}")

    # Jinja2 沙箱渲染
    context = {"data": data, "metadata": result.get("metadata", {})}
    jinja_template = _jinja_env.from_string(template_str)
    rendered = jinja_template.render(**context)
    return rendered
```

**列名校验解决什么问题？**

数据获取返回的列名可能是中文（"销售额"）但模板期望英文（"sales_amount"）。`_REQUIRED_COLUMNS` 定义了每组的中英别名，OR 语义匹配——有一个就行。缺列时降级到自动表格渲染（`_fallback_render`），不会直接报错。

### 阶段 5：LLM 润色的 3 段硬校验（llm_polisher.py:178-244）

```python
# llm_polisher.py:178-244
def polish(self, draft):
    # ① 提取阶段
    original_numbers = _extract_numerical_tokens(draft)  # {"1,234.56", "2026-07-10", ...}
    original_facts = _extract_key_facts(draft)            # 表格数据行指纹

    for attempt in range(self.max_retries + 1):
        polished = self._call_llm(draft, attempt)

        # 空响应保护
        if not polished or len(polished) < 50:
            continue

        # ② 校验数值
        polished_numbers = _extract_numerical_tokens(polished)
        nums_ok, missing = _verify_numbers(original_numbers, polished_numbers)

        # ③ 校验事实行
        polished_facts = _extract_key_facts(polished)
        facts_ok, missing_count = _verify_facts(original_facts, polished_facts)

        if nums_ok and facts_ok:
            return polished  # ✅ 通过

        # 重试时追加提醒
        if attempt < self.max_retries:
            continue

    return draft  # 安全回退：返回原稿
```

**三重防护：**
1. **数值 token 全量匹配** — 丢了任何一个数字都拒绝
2. **事实行指纹匹配** — 表格数据行的数字指纹必须 ≥90% 保留
3. **空响应保护** — LLM 返回空白或太短直接重试

### 阶段 6：模板选择优先级（template_engine.py:269-306）

```python
# template_engine.py:269-306
def get_template(self, report_type, preferred=None):
    # 优先级: 显式指定 > 目录文件 > 内置默认 > 兜底
    if preferred:
        if preferred in self._template_cache:     # 1. 磁盘
            return self._template_cache[preferred]
        if preferred in self._builtin_templates:  # 2. 内置
            return self._builtin_templates[preferred]

    # 3. 注册表默认模板
    if available_templates:
        for tpl in available_templates:
            if tpl in self._builtin_templates:
                return self._builtin_templates[tpl]

    # 4. 最后兜底
    return "## 报告\n\n{{ data }}\n"
```

---

## 5. 涉及的知识点（Knowledge）

| 知识点 | 基础概念 | 为什么这里用到 | 企业用法 |
|---|---|---|---|
| **Jinja2** | Python 模板引擎 | 模板与数据分离，设计师可独立改模板 | HTML 邮件、报表、配置文件生成 |
| **SandboxedEnvironment** | 限制模板能力的安全模式 | 防止模板中执行 Python 代码 | 用户自定义模板、CMS |
| **DataFetcher 抽象** | 策略模式：SQL/API 统一接口 | 新增数据源只需加一个 Fetcher 子类 | Repository 模式、数据中台 |
| **Snapshot** | 数据快照存档 | 回溯 + 审计 | 配置版本管理、数据库备份 |
| **LLM 硬校验** | 提取→锁定→比对 | 防止 LLM 润色时修改数据 | AI 辅助编辑、机器翻译后校验 |
| **Preference Learning** | 记住用户的选择 | 下次自动用上次的模板+图表 | 推荐系统、个性化配置 |
| **matplotlib** | Python 绑图库 | 生成 PNG 嵌入 Markdown | 数据可视化、科学计算 |
| **base64 嵌入** | 图片编码为文本内嵌 | Markdown 不支持外部图片引用 | 邮件内嵌图片、单文件 HTML |
| **列名校验** | 数据 schema 校验 | 中英文列名自动适配 | ETL 数据校验、API 响应校验 |

---

## 6. 企业级实现

### 当前实现评级：**中小型项目 — 功能完整，架构清晰**

| 维度 | 当前状态 | 企业级 |
|---|---|---|
| 数据获取 | SQL + API，统一接口 | 多数据源联邦查询 |
| 模板引擎 | Jinja2 沙箱 + 内置兜底 | 模板版本管理 + A/B 测试 |
| LLM 润色 | 3 段硬校验 | 外加人工审核流程 |
| 快照 | JSON 文件（30天） | 对象存储（S3）+ 长期保留 |
| 图表 | matplotlib 静态图 | Apache ECharts 交互式 + 导出 |

### 企业一般加什么

1. **定时报告** — cron/Celery Beat 自动生成日报/周报并发送邮件
2. **多格式导出** — Markdown → PDF（WeasyPrint）/ Excel（openpyxl）
3. **报告模板市场** — 用户自定义模板，上传分享
4. **数据对比** — 自动对比本期 vs 上期，标注涨跌

---

## 7. 可以优化的地方

### 性能
- [ ] **SQL 查询无缓存** — 相同筛选条件 5 分钟内重复查询应该缓存
- [ ] **LLM 润色慢** — 大报告可能需要 10s+，考虑流式输出

### 可扩展性
- [ ] **REPORT_REGISTRY 硬编码** — 应该支持从 YAML/JSON 配置文件加载
- [ ] **图表类型只有 3 种** — bar/line/pie，应支持更多（散点图、热力图）

### 可测试性
- [ ] **没有报告生成集成测试** — 应该 mock DB 验证 6 种报告都能渲染

---

## 8. 面试角度

**Q1: 报告生成的完整流程是什么？**

> 标准答案：6 步 — ① 查询用户偏好选模板，② DataFetcher 拉数据（SQL/API），③ 保存 JSON 快照，④ Jinja2 模板渲染为 Markdown，⑤ matplotlib 生成图表嵌入，⑥ LLM 润色（带硬校验）。

**Q2: 如何防止 LLM 润色时篡改数据？**

> 标准答案：提取→锁定→校验。润色前提取所有数值 token（数字、日期、百分比、金额），润色后再次提取，逐条比对。丢失任何一个数字都拒绝，最多重试 2 次，全失败则返回原始初稿。这不是建议，是硬拦截。

**Q3: 为什么用 Jinja2 的 SandboxedEnvironment？**

> 标准答案：防止模板执行任意 Python 代码。如果用户上传自定义模板（未来功能），沙箱模式保证模板不能读写文件、不能调系统命令、不能访问敏感变量。

**Q4: SQL 查询的参数注入如何防止 SQL 注入？**

> 标准答案：DataFetcher 用的也是参数化查询。`cur.execute(sql, params)` 将 SQL 和参数分离。外加 `conn.set_session(readonly=True)` 防止误操作写入。

**Q5: 模板中英列名不匹配怎么办？**

> 标准答案：`_REQUIRED_COLUMNS` 定义了每组中英别名（如 `{"销售额", "sales_amount"}`），OR 语义匹配——有一个就行。缺列时降级到自动表格渲染，不会直接报错。

**Q6: 为什么用 matplotlib 而不是 echarts？**

> 标准答案：报告输出是 Markdown，只能嵌入静态图片。matplotlib 生成 PNG → base64 → 一行 Markdown 搞定。ECharts 是 JS 交互式图表，在纯 Markdown 中无法渲染。

**Q7: 快照有什么用？**

> 标准答案：三个用途 — 1) 报告回溯（对比不同时间的数据），2) 数据审计（谁在什么时间用了什么筛选条件），3) 30 天自动清理防止磁盘爆满。

**Q8: 用户偏好系统是怎么工作的？**

> 标准答案：每次生成报告后记录用户选的模板和图表类型。下次生成同类型报告时自动使用上次的偏好。简单的"记住选择"——不需要复杂推荐算法。

**Q9: `_insert_charts` 为什么插入到第一个 `##` 标题前？**

> 标准答案：报告结构是 `# 标题` → `## 概述` → `## 数据表格` → `## 分析`。图表放在概述和数据表格之间，既有总览又有细节。放在 `##` 标题前意味着图表在第一个数据分析章节之前，和概述形成视觉总览。

**Q10: 如何新增一种报告类型？**

> 标准答案：三步 — 1) 在 `REPORT_REGISTRY` 添加条目（SQL + 模板名 + 图表配置），2) 在 `_builtin_templates` 或 `templates/` 目录添加 Jinja2 模板，3) `_REQUIRED_COLUMNS` 添加列名映射（如需要）。不改任何业务逻辑代码。

---

## 9. 学习总结

### 最重要的知识点

1. **6 步流水线** — 偏好→数据→快照→模板→图表→润色
2. **LLM 润色的硬校验** — 提取→锁定→校验，不是建议是硬拦截
3. **REPORT_REGISTRY 集中配置** — 新增报告类型只加配置不改代码
4. **SandboxedEnvironment** — 安全第一

### 必须掌握的源码

1. `report_generator.py:89-203` — 6 步 generate() 主入口
2. `llm_polisher.py:178-244` — polish() 的提取→校验→回退
3. `data_fetcher.py:32-230` — REPORT_REGISTRY 集中配置
4. `template_engine.py:367-442` — render() + 列名校验 + fallback

### 最容易踩坑的地方

1. **Jinja2 沙箱不解析 Markdown** — 输出就是纯文本，不需要 HTML 转义
2. **matplotlib 中文乱码** — Windows 下需要配置中文字体
3. **base64 图片太大** — 高 DPI 图表可能 500KB+，影响 Markdown 体积

### 面试必须会讲的内容

> "我设计了一个报告生成系统，6 步流水线：从 REPORT_REGISTRY 集中配置出发 → SQL/API 数据获取 → JSON 快照存档 → Jinja2 沙箱模板渲染 → matplotlib 图表嵌入 → LLM 润色。LLM 润色是亮点——提取→锁定→校验硬机制，润色前提取所有数值 token，润色后逐条比对，丢了任何数字都拒绝。新增报告类型只加配置不改代码。"

---

> **下一课：种子数据框架** — 9 领域跨境电商模拟数据生成
