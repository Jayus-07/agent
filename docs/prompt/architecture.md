# Prompt Management Architecture

Prompt 是系统中的**可版本化、可测试、可发布、可回滚、可审计的配置资源**。本模块提供完整的 prompt 生命周期管理。

## Overview

```
backend/prompts/
  registry.py      # 36 个 prompt 的静态元数据注册表
  renderer.py      # 模板渲染引擎 (str.format 语义)
  loader.py        # YAML defaults 加载 + DB seed
  service.py       # 核心服务 (snapshot/cache/DB/defaults 4 层读取)
  defaults/        # 35 个 YAML 默认模板文件
```

## Prompt Registry

`PROMPT_REGISTRY` 是所有 prompt 的单一元数据源，在 `registry.py` 中以代码方式定义。

每个 prompt 由 `PromptSpec` 描述：

| 字段 | 说明 |
|------|------|
| `key` | 点分隔标识符，如 `rag.qa` |
| `name` | 中文显示名 |
| `category` | 分类：planner / rag / router / sql / memory / security / selection / evaluation 等 |
| `risk_level` | critical / high / medium / low |
| `variables` | `VarSpec` 元组，声明模板中使用的变量 |
| `code_controlled` | 为 True 时禁止 DB 编辑（仅 `security.input_guard`） |
| `required_substrings` | 渲染后必须包含的子串（用于 contract 校验） |
| `default_file` | 对应 YAML 文件名 |

当前共 **36 个注册项**，1 个 code-controlled（`security.input_guard`），按风险等级分布：

- critical: 1
- high: 5
- medium: 14
- low: 16

## 3-Tier Read Path

读取 prompt 时按以下优先级回退：

```
1. In-process snapshot (dict, RLock 保护)
     ↓ miss
2. Two-tier cache (L1 memory 30s + L2 Redis TTL 300s)
     ↓ miss
3. PostgreSQL (PromptRepository)
     ↓ miss
4. YAML defaults (backend/prompts/defaults/*.yaml)
     ↓ miss
5. KeyError
```

### Snapshot

- `_snapshot: dict[str, _SnapshotEntry]` — 内存中的活跃版本缓存
- 由 `refresh_snapshot()` 从 DB 全量加载
- `publish()` / `rollback()` 后立即更新对应 key 的 snapshot entry
- `epoch` 计数器在每次 snapshot 变更时递增

### Sync Path

`render_sync()` 和 `get_template_sync()` 仅查询 snapshot + defaults，不触碰 DB：

```
snapshot → defaults → KeyError
```

这保证了同步调用路径（如 LangChain chain 内部）不会引入异步开销。

## Write Path

```
create_draft → publish → rollback
```

### create_draft

1. 校验 key 非 code-controlled
2. 在 DB 中创建新版本（status=draft）
3. 写入审计日志

### publish

1. 校验 key 非 code-controlled
2. 获取指定版本，运行 `PromptRenderer.validate()` 校验变量一致性
3. 设置 active_version，更新版本 status 为 published
4. 清除 cache（`prompt:{key}`）
5. 更新 in-process snapshot + 递增 epoch
6. 写入审计日志
7. 触发 reload hooks

### rollback

内部直接调用 `publish(key, version)`，复用相同的激活逻辑。

## Template Rendering

`PromptRenderer` 使用 `str.format()` 语义（非 Jinja2），通过 `string.Formatter` 解析模板变量。

### 渲染流程

1. `extract_variables(template)` — 提取 `{variable}` 占位符
2. 严格模式下校验：缺失变量 → `PromptRenderError`
3. `template.format(**safe_vars)` — 执行替换
4. 校验 `required_substrings`（如 `rag.qa` 必须包含 `<!--META`）

### 验证

`PromptRenderer.validate(template, spec)` 在 publish 时调用：

- spec 声明的变量必须在模板中出现
- 模板中的变量必须在 spec 中声明
- 返回错误列表（不抛异常）

## Permission Model

基于 `risk_level` + `role` 的权限矩阵：

| risk_level | read | draft | publish | rollback |
|------------|------|-------|---------|----------|
| critical | 所有人 | 禁止 | 禁止 | 禁止 |
| high | 所有人 | editor, admin | admin | admin |
| medium | 所有人 | editor, admin | editor, admin | editor, admin |
| low | 所有人 | editor, admin | editor, admin | editor, admin |

角色通过 `X-Operator-Role` header 传入（viewer / editor / admin）。

## YAML Defaults

每个非 code-controlled prompt 在 `backend/prompts/defaults/` 下有对应的 YAML 文件：

```yaml
key: rag.qa
template: |
  <!--META version=1-->
  基于以下上下文回答问题。
  上下文: {context}
  问题: {input}
```

`loader.py` 在启动时：
1. 解析所有 YAML 文件
2. 校验 key 与 registry 匹配
3. 校验模板变量与 spec 一致
4. 加载到 `PromptService._defaults` 作为最终回退

## Trace Integration

每次 `render()` / `render_sync()` 调用通过 ContextVar 记录 `{key, version, source}`。

`tracer.finish()` 在 trace 结束时调用 `collect_prompt_usage()` 将 prompt 使用记录附加到 span metadata，实现 prompt 版本与 trace 的关联。

## Evaluation Integration

`EvalReport.prompt_versions` 字段记录评估时活跃的 prompt 版本快照。

`baseline.diff()` 在检测到回归时，自动对比 baseline 与当前 prompt 版本差异，生成信息性警告帮助定位是否为 prompt 变更导致。

## Reload Hooks

业务模块可注册 reload hook，在 prompt 发布后执行自定义逻辑：

```python
prompt_service.register_reload_hook("rag.qa", lambda: rebuild_chain())
```

Hook 在 `publish()` 完成后同步触发，异常不会传播（仅 warning 日志）。
