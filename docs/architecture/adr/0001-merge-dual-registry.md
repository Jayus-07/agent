# ADR-0001: 合并 Skill 双注册表

| 项 | 值 |
|----|----|
| **状态** | Accepted |
| **日期** | 2026-08-03 |
| **作者** | wh |
| **影响范围** | `backend/orchestration/` |

---

## 背景

项目当前存在 **2 套并行的 Skill 注册表**，维护成本高且容易失同步：

### 注册表 A：`backend/orchestration/tool_registry.py`

```python
# 静态字典
CAPABILITY_MAP: Dict[str, str] = {
    "sql.query":       "sql_skill",
    "rag.search":      "rag_skill",
    "report.generate": "report_skill",
    # ...
}

CAPABILITY_SCHEMA: Dict[str, dict] = {
    "rag.search": {
        "description": "从跨境电商知识库中检索...",
        "params": {"question": "检索问题"},
        "示例": {"question": "Amazon FBA发货..."},
    },
    # ...
}
```

### 注册表 B：`backend/orchestration/skills/registry.py`

```python
# 动态实例
_instances = [SQLSkill(), RAGSkill(), ReportSkill()]
_registry: dict[str, BaseSkill] = {}  # capability → Skill 实例
```

### 问题

加一个新 Skill 要 **改 3 处**：

| 位置 | 改动 |
|------|------|
| `skills/<name>/skill.py` | 新建 `BaseSkill` 子类 + `capabilities = [...]` |
| `skills/registry.py` | 加 `import` + `_instances.append(...)` |
| `tool_registry.py` | 加 `CAPABILITY_MAP` + `CAPABILITY_SCHEMA` 两条 |

**风险**：
- capability 名称打错 → Planner 调度时找不到 Skill，运行时才报错
- 删除 Skill → 两个字典都要移除，容易遗漏
- description 与实现不同步 → Planner 拆错能力

---

## 决策

**将 `tool_registry.py` 的静态字典改为从 `skills/registry.py` 实例动态派生，建立单一事实来源（Single Source of Truth）。**

### 核心原则

1. **Skill 类自带元数据**（description、params_schema、examples）
2. **ToolRegistry 改为派生视图**，不再硬编码
3. **新增 Skill 只改 1 处**（skill.py + registry.py import 一行）

---

## 备选方案

### 备选 A：维持双注册表 + CI 检查一致性
- 加 pre-commit hook，校验两边一致
- **否决理由**：治标不治本，CI 增加负担，新增 Skill 仍要改 2 处

### 备选 B：合并为单个 Registry 类
- 把两个文件合并成一个 `registry.py`
- **否决理由**：耦合 LangGraph 节点名与 Skill 实例，未来分布式部署时不好拆分

### 备选 C：动态派生（采用方案）
- ToolRegistry 作为派生视图，读取 Skill 实例的元数据
- **优点**：单一事实来源；元数据贴近实现；删除 Skill 自动失效

---

## 设计细节

### Step 1：Skill 类扩展元数据字段

```python
# skills/base.py — BaseSkill 新增 3 个类属性
class BaseSkill(ABC):
    name: str = ""
    capabilities: ClassVar[list[str]] = []
    
    # 新增：
    description: str = ""      # Planner prompt 用
    params_schema: dict = {}   # Planner prompt 用
    examples: list[dict] = []  # Planner prompt 用
```

### Step 2：Skill 子类填元数据

```python
# skills/rag/skill.py
class RAGSkill(BaseSkill):
    name = "rag"
    capabilities = ["rag.search"]
    description = "从跨境电商知识库中检索 SOP/规范/FAQ/Listing指南等非结构化内容"
    params_schema = {"question": "检索问题"}
    examples = [{"question": "Amazon FBA发货的标准操作流程SOP"}]
    
    @property
    def _tool_fn(self): return search_knowledge_tool
```

### Step 3：ToolRegistry 改为派生

```python
# tool_registry.py
class ToolRegistry:
    def _get_skill_registry(self) -> dict:
        from backend.orchestration.skills.registry import _registry as skills
        return skills
    
    @property
    def CAPABILITY_MAP(self) -> dict[str, str]:
        reg = self._get_skill_registry()
        return {cap: f"{inst.name}_skill" for cap, inst in reg.items()}
    
    @property
    def CAPABILITY_SCHEMA(self) -> dict[str, dict]:
        reg = self._get_skill_registry()
        return {cap: {
            "description": inst.description,
            "params": inst.params_schema,
            "示例": inst.examples[0] if inst.examples else {},
        } for cap, inst in reg.items()}
    
    def get_node(self, capability: str) -> Optional[str]:
        return self.CAPABILITY_MAP.get(capability)  # 兼容旧调用
```

### Step 4：BaseSkill 校验元数据必填

```python
# skills/base.py — __init_subclass__ 校验
def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)
    if not cls.description:
        raise TypeError(f"{cls.__name__}.description 必填（Planner prompt 需要）")
    if not cls.capabilities:
        raise TypeError(f"{cls.__name__}.capabilities 必填（注册需要）")
```

---

## 影响

### 代码改动

| 文件 | 改动类型 | 行数 |
|------|---------|------|
| `skills/base.py` | 加 3 个类属性 + `__init_subclass__` | +15 行 |
| `skills/rag/skill.py` | 加 description/params_schema/examples | +3 行 |
| `skills/sql/skill.py` | 同上 | +3 行 |
| `skills/report/skill.py` | 同上 | +3 行 |
| `skills/email/`, `web_search/`, `web_crawl/`, `data_export/` | 同上 | ~+12 行 |
| `tool_registry.py` | 改 `CAPABILITY_MAP`/`CAPABILITY_SCHEMA` 为 property | 改 ~10 行 |
| `data_collection/skill.py` | 加元数据 | +3 行 |
| `backend/tests/` | 新增测试 | +50 行 |

### 性能

- `CAPABILITY_MAP` 从类属性改为 property，每次访问会重新计算
- Planner prompt 生成在每次请求时调用 → 增加 ~1ms（7 个 capability × dict comprehension）
- **缓解**：加 `@cached_property` 装饰器（实例级缓存）

### 兼容性

- `get_node(capability)` / `get_schema(capability)` / `get_available_capabilities()` 接口不变
- `builder.py` / `planner.py` / `supervisor.py` 调用点不变
- LangGraph 图编译结果不变（节点名约定 `{name}_skill`）

### 风险

| 风险 | 缓解 |
|------|------|
| Skill 加载顺序导致 CAPABILITY_MAP 为空 | `builder.py:26` 已有 `import backend.orchestration.skills` 强制加载 |
| description 写得太抽象 | Code Review 检查 + Planner 拆错能力时 Trace 可定位 |
| 外部 Skill（DataCollection）漏改 | 加 ListMcpResources 风格的自检脚本 |

---

## 验证标准

### 功能验证

- [ ] `pytest backend/tests/test_orchestration_skills.py -v` 全绿
- [ ] `pytest backend/tests/test_tool_registry.py -v` 全绿
- [ ] `python -c "from backend.orchestration.tool_registry import tool_registry; print(len(tool_registry.CAPABILITY_MAP))"` 输出 8（所有 capability）
- [ ] `curl -X POST http://localhost:8000/chat -d '{"question":"上周销量"}'` 返回 SSE 流
- [ ] Trace 包含 `tool_call` span 且 capability 名正确

### 一致性验证

- [ ] 加一个临时 Skill（不上线），验证只改 `skill.py` + `registry.py` import 即可被识别
- [ ] 改 `description` 后 Planner 生成的 plan 节点描述同步更新

### 回归验证

- [ ] 现有 7 个 Skill 全部可调用（sql/rag/report/email/web_search/web_crawl/data_export）
- [ ] Planner prompt 中的 capability 描述无变化（diff 后端 Prompt 模板）

---

## 实施步骤

1. **写 ADR**（本文档） ✅
2. **写测试**（TDD）：先写期望行为，让测试失败
3. **改 `skills/base.py`**：加元数据 + `__init_subclass__`
4. **改各 Skill 子类**：填元数据
5. **改 `tool_registry.py`**：改为派生 property + cached
6. **跑测试 + 端到端验证**
7. **写 CHANGELOG / 更新架构文档**

---

## 后续工作

- 长期：考虑把 `description/params_schema/examples` 抽取到独立 `manifest.yaml`，与代码解耦
- 长期：加 CLI 工具 `python -m orchestration.skill_info` 列出所有 capability，方便调试