# Agent 项目

基于 LangChain + LangGraph 的 RAG + Multi-Agent 智能问答与报告系统。

## 项目架构

```
agent/
├── config.py              # 统一配置（环境变量 + 默认值）
├── llm/                   # LLM 工厂（Ollama 适配）
├── retrieval/             # RAG 检索管线
│   ├── pipeline.py        #   主入口 RAGPipeline
│   ├── bm25.py            #   BM25 关键词检索
│   ├── hybrid.py          #   混合检索（向量 + BM25）
│   ├── reranker.py        #   BGE-Reranker 重排序
│   ├── retrievers.py      #   LangChain Retriever 封装
│   ├── chain.py           #   LangChain QA Chain
│   └── base.py            #   基类与协议
├── preprocessing/         # 文档预处理
│   ├── loader.py          #   多格式文档加载（PDF/MD/DOCX）
│   ├── entity.py          #   实体识别
│   ├── keyword.py         #   关键词提取
│   └── metadata.py        #   元数据管理
├── multi_agent/           # LangGraph Multi-Agent 工作流
│   ├── graph.py           #   MultiAgentSystem 主入口
│   ├── planner.py         #   DAG 任务规划器
│   ├── supervisor.py      #   监督者（任务调度）
│   ├── tools.py           #   工具执行器
│   ├── reporter.py        #   结果聚合器
│   ├── state.py           #   AgentState 状态定义
│   ├── tool_registry.py   #   工具注册表
│   └── demo.py            #   演示入口
├── sql_agent/             # SQL 安全查询 Agent
│   ├── sql_agent.py       #   主入口
│   ├── router.py          #   问题路由（需SQL / 不需要）
│   ├── schema_loader.py   #   数据库 schema 加载
│   ├── sql_generator.py   #   LLM SQL 生成
│   ├── sql_validator.py   #   sqlglot 语法校验
│   ├── row_security.py    #   行级安全控制
│   └── executor.py        #   只读执行器
├── report_agent/          # 报告生成模块
│   ├── report_generator.py #   主入口
│   ├── data_fetcher.py    #   SQL/API 数据取数
│   ├── template_engine.py #   Jinja2 模板渲染
│   ├── llm_polisher.py    #   LLM 语言润色
│   ├── chart_generator.py #   matplotlib 图表
│   ├── preference.py      #   用户偏好存储
│   └── snapshot.py        #   报告快照
├── utils/                 # 工具函数
│   ├── async_utils.py     #   异步并发控制
│   ├── logger.py          #   日志
│   ├── resource_monitor.py #  资源监控
│   └── timeout.py         #   超时控制
└── data/                  # 运行时数据（不提交）
    ├── chroma/            #   向量数据库
    ├── doc_db/            #   文档索引
    ├── docs/              #   原始文档
    └── reports/           #   生成的报告
```

## 技术栈

- **LLM**: Ollama (qwen2.5:3b) + LangChain
- **Embedding**: BAAI/bge-small-zh-v1.5 (ModelScope)
- **Reranker**: BAAI/bge-reranker-base
- **向量库**: ChromaDB
- **关键词检索**: rank-bm25
- **Multi-Agent**: LangGraph (Planner → Supervisor → Workers → Reporter)
- **SQL 安全**: sqlglot 语法校验 + 只读执行器
- **报告**: Jinja2 + matplotlib + LLM 润色
- **数据库**: PostgreSQL (demo)

## 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 运行 Multi-Agent 演示
python multi_agent/demo.py

# 运行 SQL Agent 演示
python sql_agent/demo_sql_agent.py

# 运行 Report Agent 演示
python report_agent/demo_report_agent.py

# 运行 RAG Pipeline
python retrieval/RAGPipeline.py
```

## 关键约定

- 配置通过 `config.py` 统一管理，敏感信息放 `.env`
- SQL Agent 只做只读查询，`row_security.py` 强制行级过滤
- Report Agent 中 LLM 仅做语言润色，数字/事实通过硬校验锁定
- Multi-Agent 通过 ToolRegistry 零侵入接入已有子系统
- 路径使用正斜杠 `/`（Windows + bash 兼容）
