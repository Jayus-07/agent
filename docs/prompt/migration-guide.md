# Prompt Migration Guide

将业务代码中的硬编码 prompt 迁移到 `prompt_service` 集中管理。

## Why

- **版本管理**: 通过 UI 编辑、发布、回滚，无需改代码重新部署
- **审计追踪**: 每次变更有完整审计日志
- **A/B 测试**: 快速切换不同 prompt 版本对比效果
- **评估关联**: 评估报告自动记录使用的 prompt 版本

## Migration Patterns

### Pattern 1: Full Render (替换 f-string / .format())

适用于：直接构建完整 prompt 文本的场景。

**Before:**
```python
SYSTEM_PROMPT = "分析以下数据并给出建议：{data}"

def analyze(data: str):
    messages = [SystemMessage(content=SYSTEM_PROMPT.format(data=data))]
    response = llm.invoke(messages)
```

**After:**
```python
from backend.prompts.service import prompt_service

def analyze(data: str):
    result = prompt_service.render_sync("capability.inventory_analyzer", data=data)
    messages = [SystemMessage(content=result.text)]
    response = llm.invoke(messages)
```

`render_sync()` 返回 `RenderResult`：
- `.text` — 渲染后的完整文本
- `.version` — 使用的版本号（None 表示 YAML default）
- `.source` — 来源（"snapshot" / "default"）

### Pattern 2: Raw Template (LangChain ChatPromptTemplate)

适用于：需要 LangChain 自行渲染变量的场景。

**Before:**
```python
_VOTE_SYSTEM = "作为 {role} 评审，重点关注 {focus}。"

def build_chain():
    prompt = ChatPromptTemplate.from_messages([
        ("system", _VOTE_SYSTEM),
        ("human", "{input}"),
    ])
```

**After:**
```python
from backend.prompts.service import prompt_service

def build_chain():
    template = prompt_service.get_template_sync("selection.panel.vote")
    prompt = ChatPromptTemplate.from_messages([
        ("system", template),
        ("human", "{input}"),
    ])
```

`get_template_sync()` 返回原始模板文本，保留 `{variable}` 占位符。

### Pattern 3: Async Render (需要 DB 回退的场景)

适用于：异步上下文，且需要确保获取最新 DB 版本。

**Before:**
```python
async def judge(question: str, answer: str):
    template = EVAL_TEMPLATE.format(question=question, answer=answer)
    result = await llm.ainvoke(template)
```

**After:**
```python
from backend.prompts.service import prompt_service

async def judge(question: str, answer: str):
    result = await prompt_service.render(
        "evaluation.judge.user",
        question=question,
        answer=answer,
    )
    response = await llm.ainvoke(result.text)
```

## Step-by-Step Checklist

对每个迁移点：

1. **确认 registry key 存在**
   ```bash
   grep -r "key=\"your.prompt.key\"" backend/prompts/registry.py
   ```

2. **确认 YAML default 文件存在**
   ```bash
   ls backend/prompts/defaults/your_prompt_key.yaml
   ```

3. **替换硬编码常量**
   - 删除模块级的 `SYSTEM_PROMPT` / `_TEMPLATE` 等常量
   - 替换为 `prompt_service.render_sync()` 或 `get_template_sync()` 调用

4. **验证变量名一致**
   - 原 f-string 中的变量名必须与 YAML 模板中的 `{variable}` 一致
   - 与 `PromptSpec.variables` 中声明的变量名一致

5. **运行测试**
   ```bash
   pytest backend/tests/ -k "your_module"
   ruff check backend/your_module.py
   ```

## Variable Naming Convention

变量名使用 snake_case，与 Python 标识符一致：

- `context` — RAG 检索到的上下文
- `input` — 用户输入
- `query` — 查询文本
- `safe_text` — 清洗后的文本内容
- `top_k` — 关键词数量
- `conversation` — 对话历史

## Common Pitfalls

### 1. 变量名不匹配

YAML 模板中的 `{context}` 必须与 `render_sync(context=...)` 的 keyword arg 完全一致。检查 `PromptSpec.variables` 中的声明。

### 2. 同步 vs 异步

- `render_sync()` — 仅查 snapshot + defaults，不查 DB。适合高频同步调用。
- `render()` — 查 snapshot → cache → DB → defaults。适合异步上下文。

如果需要在同步路径获取最新 DB 版本，先调用 `await prompt_service.refresh_snapshot()`。

### 3. 删除硬编码后忘记 import

迁移后确保添加：
```python
from backend.prompts.service import prompt_service
```

### 4. LangChain ChatPromptTemplate 双重渲染

使用 `get_template_sync()` 获取原始模板时，让 LangChain 自行渲染变量。不要先 `render_sync()` 再传给 `ChatPromptTemplate`，否则 `{variable}` 已被替换。

## Registry 新增 Prompt

如果业务代码使用了尚未注册的新 prompt：

1. 在 `backend/prompts/registry.py` 中添加 `_register(PromptSpec(...))`
2. 在 `backend/prompts/defaults/` 下创建对应的 YAML 文件
3. 运行测试验证：
   ```bash
   pytest backend/tests/prompts/test_registry_defaults.py
   ```
