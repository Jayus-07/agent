# RAG Agent 渐进式重构计划

> 每个 Phase 独立提交，完成后可正常运行。不改业务逻辑，只搬家和拆职责。

---

## Phase 1：根目录分离

**目标**：企业级项目骨架，不改业务逻辑

**当前结构**：
```
agent/
├── web/           # Next.js
├── api/           # FastAPI
├── retrieval/     # RAG 核心
├── preprocessing/ # 文档预处理
├── multi_agent/   # 多Agent核心
├── memory/        # 记忆模块
├── sql_agent/     # SQL模块
├── report_agent/  # 报告模块
├── llm/           # LLM模块
├── data_collection/# 数据采集
├── evaluation/    # 评测
├── seed_data/     # 种子数据
├── utils/         # 工具函数
├── config.py      # 配置文件散落
├── start_all.bat  # 启动脚本
├── docs/          # 文档
├── data/          # 数据存储
├── tests/         # 测试
└── .env           # 环境
```

**目标结构**：
```
agent/
├── frontend/                    # Next.js（原 web/）
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── hooks/
│   │   ├── lib/
│   │   ├── store/
│   │   └── styles/
│   ├── public/
│   ├── tests/
│   ├── package.json
│   └── next.config.js
│
├── backend/                     # FastAPI（原 project root）
│   ├── app/                     # API 入口
│   │   ├── api/                 # 原 api/
│   │   │   ├── routes/          # 原 api/routes/
│   │   │   └── deps.py          # 原 api/deps.py
│   │   └── server.py            # 原 api/server.py
│   │
│   ├── rag/                     # 原 retrieval/ + preprocessing/
│   │   ├── pipeline.py          # 原 retrieval/pipeline.py
│   │   ├── chain.py             # 原 retrieval/chain.py
│   │   ├── multi_query.py
│   │   ├── retrievers.py
│   │   ├── reranker.py
│   │   ├── hybrid.py
│   │   ├── bm25_store.py
│   │   ├── base.py
│   │   ├── context.py
│   │   ├── knowledge_store.py
│   │   ├── doc_registry.py
│   │   ├── indexer.py
│   │   ├── query_analyzer.py
│   │   ├── tracer.py
│   │   ├── metrics.py
│   │   └── preprocessing/      # 原 preprocessing/
│   │       ├── loader.py
│   │       ├── chunking.py
│   │       ├── metadata.py
│   │       ├── keyword.py
│   │       ├── entity.py
│   │       ├── cleaner.py
│   │       └── filter.py
│   │
│   ├── agent/                   # 原 multi_agent/
│   │   ├── graph/
│   │   ├── planner/
│   │   ├── supervisor/
│   │   ├── reporter/
│   │   ├── tools.py
│   │   ├── observability.py
│   │   ├── tool_registry.py
│   │   └── skills/
│   │
│   ├── llm/                     # 原 llm/
│   │   ├── proxy.py
│   │   ├── factory.py
│   │   ├── models.py
│   │   └── providers/
│   │
│   ├── sql/                     # 原 sql_agent/
│   ├── report/                  # 原 report_agent/
│   ├── memory/                  # 原 memory/
│   ├── data_collection/         # 原 data_collection/
│   ├── evaluation/              # 原 evaluation/
│   ├── seed/                    # 原 seed_data/
│   ├── utils/                   # 原 utils/
│   ├── config.py                # 原 config.py
│   ├── config/                  # 拆分配置（可选）
│   │   ├── llm.py
│   │   ├── rag.py
│   │   └── settings.py
│   ├── data/                    # 原 data/（符号链接）
│   ├── tests/                   # 原 tests/
│   ├── requirements.txt
│   └── pyproject.toml
│
├── docker/
│   ├── backend.Dockerfile
│   ├── frontend.Dockerfile
│   └── docker-compose.yml
│
├── deployment/
│   └── production.env.example
│
├── .github/
│   └── workflows/
│
├── docs/                        # 不动
├── .env                         # 移到 backend/
├── README.md
└── start_all.bat                # 更新路径
```

### Task 1.1：建立 frontend/ 目录

```
1. mkdir frontend/src
2. 移动 web/ 下所有内容到 frontend/
   ├── app/     → frontend/src/app/
   ├── components/ → frontend/src/components/
   ├── hooks/   → frontend/src/hooks/
   ├── lib/     → frontend/src/lib/
   ├── store/   → frontend/src/store/
   ├── styles/  → frontend/src/styles/
   ├── public/  → frontend/public/
   ├── package.json → frontend/
   ├── next.config.js → frontend/
   └── tsconfig.json → frontend/
3. 更新 frontend/package.json 中 start/build 脚本路径
4. npm install（重新安装依赖）
5. npm run dev 验证前端正常启动
```

### Task 1.2：建立 backend/ 目录

```
1. mkdir -p backend/app/api/routes backend/app/api/schemas
2. 移动目录:
   retrieval/   → backend/rag/
   preprocessing/ → backend/rag/preprocessing/
   multi_agent/ → backend/agent/
   llm/         → backend/llm/
   sql_agent/   → backend/sql/
   report_agent/→ backend/report/
   memory/      → backend/memory/
   data_collection/ → backend/data_collection/
   evaluation/  → backend/evaluation/
   seed_data/   → backend/seed/
   utils/       → backend/utils/
   api/routes/  → backend/app/api/routes/
   api/schemas.py → backend/app/api/schemas.py
   api/server.py  → backend/app/server.py
   api/deps.py    → backend/app/deps.py
   config.py    → backend/config.py
   tests/       → backend/tests/
   data/        → backend/data/（或符号链接）
3. 全局替换 import：
   grep -rn "from retrieval\." backend/ → 替换为 "from backend.rag."
   grep -rn "from preprocessing\." backend/ → 替换为 "from backend.rag.preprocessing."
   grep -rn "from multi_agent\." backend/ → 替换为 "from backend.agent."
   ... 依此类推所有模块
4. pip install -e .（以 backend/ 为包根目录）
5. uvicorn backend.app.server:app 验证后端正常启动
```

### Task 1.3：更新启动脚本和文档

```
1. 更新 start_all.bat
   ├── 前端: cd frontend && npm run dev
   └── 后端: cd backend && uvicorn app.server:app
2. 更新 CLAUDE.md 中的文件路径引用
3. 移动 .env 到 backend/
4. 更新 .gitignore
5. 全链路测试: 前端发消息 → 后端回复 → Agent Trace 显示
```

**验收标准**：
- [ ] 前端 `npm run dev` 正常（端口 3000）
- [ ] 后端 `uvicorn app.server:app` 正常（端口 8000）
- [ ] Agent 对话页 提问 → 正常回复
- [ ] Agent Trace 页面 → 正常显示步骤
- [ ] LLM 切换前端按钮 → 切换生效
- [ ] 知识库文档页 → 文档列表正常

---

## Phase 2：Backend 模块化

**目标**：每个子模块有清晰的公开接口

### Task 2.1：rag/ 模块接口

```
backend/rag/__init__.py 新增:
  from backend.rag.pipeline import RAGPipeline
  from backend.rag.chain import RAGChain
  from backend.rag.multi_query import MultiQueryRetriever, need_multi_query
  from backend.rag.tracer import trace_collector

所有外部调用改为:
  from backend.rag import RAGPipeline  # 替代 from backend.rag.pipeline import RAGPipeline
```

### Task 2.2：llm/ 模块接口

```
backend/llm/__init__.py:
  from backend.llm.proxy import llm, get_llm
  from backend.llm.factory import get_llm_factory
  from backend.llm.models import AVAILABLE_MODELS

外部调用:
  from backend.llm import llm  # 替代 from backend.llm.proxy import llm
```

### Task 2.3：agent/ 模块接口

```
backend/agent/__init__.py:
  from backend.agent.graph import MultiAgentSystem

外部调用:
  from backend.agent import MultiAgentSystem
```

### Task 2.4：统一所有 import

```
逐文件修复所有 import，确保:
- 模块内部 import 使用相对路径 (from .xxx import)
- 跨模块 import 使用公开接口 (from backend.rag import RAGPipeline)
- 无循环依赖
- pytest backend/tests/ 全部通过
```

**验收标准**：
- [ ] 所有 import 走模块级 `__init__.py`
- [ ] 无跨模块直接引用私有模块
- [ ] 现有 API 端点全部正常

---

## Phase 3：职责拆分

**目标**：大函数拆小，单一职责

### Task 3.1：拆 RAGPipeline._init()

```
当前 _init() 干了:
  load_and_chunk → build_doc_index → init_embedding
  → init_vector_dbs → sync_registry → build_metadata
  → init_retrievers

拆成:
  _prepare_documents()
    ├── _load_and_chunk()
    └── _build_doc_index()

  _prepare_vector_store()
    ├── _init_embedding()
    ├── _init_vector_dbs_incremental() 或
    ├── _build_metadata() + _init_vector_dbs_full()
    └── _sync_registry_after_full_rebuild()

  _prepare_index()
    ├── BM25Store.load() 或 build()
    ├── _build_person_index()
    └── CustomRetriever

  _prepare_retrievers()
    └── RAGChain(doc_db, vectordb, ...)

  _init() 变成:
    self._prepare_documents()
    self._prepare_vector_store()
    self._prepare_index()
    self._prepare_retrievers()
```

### Task 3.2：拆 RAGPipeline.ask()

```
当前 ask() 干了:
  kb隔离 → 资源监控 → chain调用 → 耗时统计 → 异常处理 → context清除

拆成:
  _prepare_context(kb_id)
    → set_context(ctx) 或 跳过

  _execute_chain(question, session_id)
    → self.lc_chain.ask(question, session_id)

  _cleanup()
    → clear_context()

  ask() 变成:
    self._prepare_context(kb_id)
    try:
      if resource_check: return busy
      return self._execute_chain(question, session_id)
    finally:
      self._cleanup()
```

### Task 3.3：_check_db_version 副作用移除

```
当前: _check_db_version() 内部直接 shutil.rmtree()

拆成:
  _need_rebuild(db_path) → bool
    → 只检查，不删文件

  _rebuild_db(db_path)
    → 删旧目录 + create_fn()

  _load_or_create_db 改为:
    if self._need_rebuild(db_path):
      return self._rebuild_db(db_path, create_fn)
    return self._load_existing_db(db_path)
```

### Task 3.4：import 归位

```
逐文件检查:
  grep -rn "^    from " backend/
  grep -rn "import" backend/ | grep "def \|class "

将非循环依赖、非重量级依赖的 import 移到文件顶部。
保留在函数内的: 只有解决循环依赖的 import。
```

### Task 3.5：清理 chain.py ask()

```
同 Task 3.2 思路:
  _prepare()   → Memory.start_session
  _execute()   → chain.invoke
  _verify()    → Citation
  _trace()     → trace_collector.finish
```

**验收标准**：
- [ ] `_init()` ≤ 6 行（4 个方法调用）
- [ ] `ask()` ≤ 15 行
- [ ] `_need_rebuild()` 无删除操作
- [ ] 80% import 在文件顶部
- [ ] Agent 对话 + Agent Trace 正常

---

## Phase 4：Capability / Skill / Tool

**目标**：新增抽象层，标准化能力注册和调用

### Task 4.1：定义 Capability 抽象

```
backend/capabilities/base.py:

class BaseCapability(ABC):
    """Agent 可调用的能力。每个 Capability 包含 1-N 个 Skill。"""
    name: str          # "rag" / "sql" / "report"
    description: str

    @abstractmethod
    def get_skills(self) -> list[BaseSkill]:
        ...

    def register(self, registry):
        """向 ToolRegistry 注册所有 Skill"""


# 实现:
backend/capabilities/rag.py:
class RAGCapability(BaseCapability):
    name = "rag"
    def get_skills(self):
        return [RAGQASkill(), DocumentSearchSkill()]

backend/capabilities/sql.py:
class SQLCapability(BaseCapability):
    name = "sql"
    ...

backend/capabilities/report.py:
class ReportCapability(BaseCapability):
    name = "report"
    ...
```

### Task 4.2：定义 Skill 抽象

```
backend/skills/base.py:

class BaseSkill(ABC):
    """一个具体的业务技能。被 Supervisor 调度。"""
    name: str
    capabilities: list[str]  # 对应 Capability.name
    tools: list[BaseTool]

    @abstractmethod
    def execute(self, state: dict) -> dict:
        ...


# 实现:
backend/skills/rag_qa.py:
class RAGQASkill(BaseSkill):
    name = "rag_qa"
    tools = [search_knowledge_tool]
    def execute(self, state): ...

backend/skills/sql_analysis.py:
class SQLAnalysisSkill(BaseSkill):
    name = "sql_analysis"
    ...
```

### Task 4.3：统一 Tool 接口

```
backend/tools/base.py:

class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema

    @abstractmethod
    def invoke(self, **kwargs) -> dict:
        ...


backend/tools/rag.py:
class SearchKnowledgeTool(BaseTool):
    name = "search_knowledge"
    def invoke(self, question: str, kb_id: str = "default"):
        return pipeline.ask(question, session_id="...", kb_id=kb_id)
```

### Task 4.4：接入 Agent 系统

```
更新 multi_agent/tool_registry.py:
  从硬编码 tool 列表 → 扫描 Capability → Skill → Tool

更新 Planner:
  接收能力清单时，从 Capability 层获取

更新 Supervisor:
  调度 Skill 时，通过 Capability.get_skills() 查找
```

**验收标准**：
- [ ] RAG/SQL/Report 各为一个 Capability
- [ ] 每个 Capability 注册到 ToolRegistry
- [ ] Agent 调用链路: Planner → Capability → Skill → Tool
- [ ] 现有功能（RAG问答/SQL查询/报告生成）全部正常

---

## Phase 5：MCP 集成

**目标**：通过 MCP 协议暴露工具，为 AI Agent 提供标准化接口

### Task 5.1：MCP Manager

```
backend/mcp/manager.py:

class MCPManager:
    """管理 MCP Server 生命周期"""
    def register(self, server: MCPServer): ...
    def discover(self) -> list[ToolSchema]: ...
    def route(self, tool_name: str, params: dict) -> dict: ...
```

### Task 5.2：MCP Server 实现

```
backend/mcp/servers/rag.py:

class RAGMCPServer(MCPServer):
    protocol = "mcp"
    tools = ["search_knowledge", "list_documents", "upload_document"]

    def handle(self, tool_name: str, params: dict):
        if tool_name == "search_knowledge":
            return pipeline.ask(params["question"], ...)
        ...

backend/mcp/servers/sql.py:
class SQLMCPServer(MCPServer):
    tools = ["sql_query", "list_tables", "describe_table"]
```

### Task 5.3：MCP 端点

```
backend/app/api/mcp.py:

@router.get("/mcp/tools")
async def list_mcp_tools():
    return manager.discover()

@router.post("/mcp/call")
async def call_mcp_tool(tool_name: str, params: dict):
    return manager.route(tool_name, params)
```

**验收标准**：
- [ ] `GET /mcp/tools` 返回所有工具清单
- [ ] `POST /mcp/call` 可调用任意工具
- [ ] MCP 端点可被外部 Agent 调用
- [ ] 现有 Agent 系统不受影响

---

## 各阶段影响范围总览

| Phase | 改代码行数（估） | 风险等级 | 回滚方式 |
|-------|:--:|:--:|------|
| 1 | ~500 行（import 路径替换） | 低 | git revert |
| 2 | ~200 行（新增 __init__.py） | 低 | git revert |
| 3 | ~300 行（拆函数） | 中 | 保留原函数注释 |
| 4 | ~600 行（新增抽象层） | 中 | 新旧两套并行 |
| 5 | ~400 行（新增 MCP） | 中 | 独立模块，不影响旧链路 |

**总工时：~27h（按单人估算）**

---

## 每个 Phase 完成后必须验证

```
□ backend/ 和 frontend/ 目录结构符合目标
□ start_all.bat 能一键启动前后端
□ pytest backend/tests/ 全部通过（如无测试则跳过）
□ 前端 Agent 对话页提问 → 正常回复
□ Agent Trace 页面 → 正常显示
□ LLM 切换 → 切换生效
□ 知识库文档页 → 上传/查看/删除正常
```
