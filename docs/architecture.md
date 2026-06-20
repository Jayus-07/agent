# Agent Platform — 架构图

> 所有图表使用 Mermaid 语法，GitHub / Gitee / VS Code 预览直接渲染。

## 1. 系统全景

```mermaid
graph TB
    subgraph 用户层
        UI[Next.js 前端<br/>端口 3000]
        API_直接[curl / HTTP Client]
    end

    subgraph 网关层
        FAST[FastAPI + Uvicorn<br/>端口 8000]
        CORS[CORS 中间件]
        HEALTH[/health]
        DOCS[/docs Swagger]
    end

    subgraph API路由
        CHAT[POST /chat<br/>SSE /chat/stream]
        SQL[POST /sql]
        RAG[POST /rag]
        REPORT[POST /report]
    end

    subgraph Agent层
        MAS[MultiAgentSystem<br/>multi_agent/graph.py]
        SQL_AG[SQLAgent<br/>sql_agent/]
        RAG_PL[RAGPipeline<br/>retrieval/pipeline.py]
        REP_GEN[ReportGenerator<br/>report_agent/]
    end

    subgraph 记忆系统
        MEM_SVC[MemoryService<br/>memory/service.py]
        L1[L1 ShortTermBuffer<br/>环形缓冲区]
        L2[L2 SessionMemory<br/>PostgreSQL async]
        L3[L3 LongTermMemory<br/>pgvector]
    end

    subgraph 数据层
        PG[(PostgreSQL 18<br/>:5432)]
        CHROMA[(ChromaDB<br/>向量检索)]
        OLLAMA[Ollama<br/>qwen2.5:4b :11434]
        DOCS_DISK[data/docs/<br/>文档文件]
    end

    UI -->|SSE Stream| CHAT
    API_直接 --> CHAT & SQL & RAG & REPORT
    CHAT --> MAS
    SQL --> SQL_AG
    RAG --> RAG_PL
    REPORT --> REP_GEN

    MAS --> MEM_SVC
    SQL_AG --> PG
    RAG_PL --> CHROMA & DOCS_DISK
    REP_GEN --> PG

    MEM_SVC --> L1 & L2 & L3
    L2 --> PG
    L3 --> PG
    MAS --> OLLAMA
    SQL_AG --> OLLAMA
    RAG_PL --> OLLAMA
```

## 2. 请求生命周期

```mermaid
sequenceDiagram
    actor U as 用户
    participant FE as Next.js :3000
    participant API as FastAPI :8000
    participant MAS as MultiAgentSystem
    participant MM as MemoryManager
    participant L2 as SessionMemory (PG)
    participant L3 as LongTermMemory (pgvector)
    participant LLM as Ollama :11434
    participant WK as Workers

    U->>FE: 输入问题
    FE->>API: POST /chat/stream (SSE)
    API->>MAS: ask(question, session_id)

    Note over MAS,MM: ── 记忆加载 ──
    MAS->>MM: start_session()
    MM->>L2: load_messages() → 历史对话
    MM->>L3: retrieve(question) → 长期记忆
    MM-->>MAS: L1 缓冲区 (含历史 + 背景)

    Note over MAS,LLM: ── Agent 执行 ──
    MAS->>LLM: Planner: 任务拆解
    MAS->>WK: Supervisor → Workers 并行
    WK->>LLM: SQL / RAG / Report Worker
    WK-->>MAS: 执行结果

    Note over MAS,LLM: ── 记忆持久化 ──
    MAS->>LLM: Reporter: 汇总答案
    MAS->>MM: end_turn(question, answer)
    MM->>L2: save_turn() → 持久化对话
    MM->>L3: store() → 后台管线写入

    MAS-->>API: final_answer
    API-->>FE: SSE done event
    FE-->>U: Markdown 渲染答案
```

## 3. 记忆系统架构

```mermaid
graph TB
    subgraph 入口
        MS[MemoryService<br/>统一入口]
        MM[MemoryManager<br/>同步兼容层]
    end

    subgraph L1_层[L1 短期记忆]
        STB[ShortTermBuffer<br/>环形缓冲区<br/>max 20 messages]
    end

    subgraph L2_层[L2 会话记忆]
        SM[SessionMemory<br/>async PG]
        SR[SessionRepository<br/>chat_sessions<br/>chat_messages]
    end

    subgraph L3_层[L3 长期记忆]
        LM[LongTermMemory<br/>pgvector]
        MR[MemoryRepository<br/>memory_records]
    end

    subgraph L3管线[L3 写入管线]
        EXTRACT[LLM Fact Extract]
        PII[PII Filter<br/>正则脱敏]
        TRIGGER[WorthinessClassifier<br/>STORE / IGNORE]
        SCORE[ImportanceScorer<br/>0.0 ~ 1.0]
        DEDUP[Vector Dedup<br/>cosine similarity]
        WRITE[pgvector INSERT]
    end

    subgraph L3检索[L3 检索]
        HYBRID[HybridRetriever]
        DECAY[MemoryDecayService<br/>定时衰减]
    end

    subgraph 存储
        PG[(PostgreSQL 18)]
    end

    MS --> MM
    MM --> STB & SM & LM

    SM --> SR --> PG
    LM --> MR --> PG
    LM --> EXTRACT

    EXTRACT --> PII --> TRIGGER --> SCORE --> DEDUP --> WRITE --> PG
    SCORE -->|importance < 0.6| DROP[丢弃]
    TRIGGER -->|IGNORE| DROP

    LM --> HYBRID
    HYBRID --> MR
    DECAY --> MR

    MS -.->|Agent 唯一入口<br/>禁止直接访问 repository| MR
```

## 4. Multi-Agent 工作流 (LangGraph)

```mermaid
stateDiagram-v2
    [*] --> Planner
    Planner --> Supervisor: DAG 任务规划

    Supervisor --> SQL_Worker: Send
    Supervisor --> RAG_Worker: Send
    Supervisor --> Report_Worker: Send

    SQL_Worker --> Supervisor: 结果
    RAG_Worker --> Supervisor: 结果
    Report_Worker --> Supervisor: 结果

    Supervisor --> Supervisor: 循环调度
    Supervisor --> Reporter: 全部完成

    Reporter --> [*]

    note right of Planner
        LLM 拆解复杂问题
        输出 DAG 子任务
    end note

    note right of Supervisor
        route_after_supervisor
        返回 list[Send]
        LangGraph 自动并行执行
    end note
```

## 5. 前端组件树

```mermaid
graph TB
    subgraph Next.js
        LAYOUT[layout.tsx<br/>RootLayout]
        PAGE[page.tsx<br/>HomePage]
        SIDEBAR[Sidebar<br/>会话列表]
        CHAT_VIEW[ChatView<br/>主容器]
    end

    subgraph 组件
        EMPTY[EmptyState<br/>统一示例]
        MSG_LIST[MessageList]
        MSG_BUBBLE[MessageBubble<br/>用户/AI 气泡]
        THINK[ThinkingPanel<br/>Worker 进度]
        MD[MarkdownContent<br/>react-markdown]
        INPUT[ChatInput<br/>通用输入框]
        STATUS[StatusBar<br/>实时进度条]
    end

    subgraph 数据层
        STORE[Zustand Store<br/>chat.ts]
        SSE_HOOK[useSSE<br/>SSE 流消费]
        CHAT_HOOK[useChat<br/>发送消息]
        API_CLIENT[api.ts<br/>HTTP + SSE]
        SSE_PARSER[sse-parser.ts<br/>进度解析]
        CONSTANTS[constants.ts<br/>Worker 图标]
    end

    LAYOUT --> SIDEBAR & PAGE
    PAGE --> CHAT_VIEW
    CHAT_VIEW --> EMPTY & MSG_LIST & INPUT & STATUS

    MSG_LIST --> MSG_BUBBLE
    MSG_BUBBLE --> THINK & MD

    INPUT --> CHAT_HOOK
    CHAT_HOOK --> SSE_HOOK
    SSE_HOOK --> API_CLIENT
    SSE_HOOK --> STORE

    MSG_BUBBLE --> STORE
    MSG_BUBBLE --> SSE_PARSER
    STATUS --> STORE
    STATUS --> SSE_PARSER

    THINK --> CONSTANTS
    MSG_BUBBLE --> CONSTANTS
    STATUS --> CONSTANTS
```

## 6. 数据库 Schema

```mermaid
erDiagram
    chat_sessions {
        serial id PK
        varchar session_id UK
        varchar user_id
        text summary
        timestamptz created_at
        timestamptz updated_at
    }

    chat_messages {
        serial id PK
        varchar session_id FK
        varchar role
        text content
        timestamptz created_at
    }

    memory_records {
        uuid id PK
        varchar user_id
        varchar session_id
        varchar memory_type
        text content
        vector embedding
        float importance_score
        float confidence_score
        int access_count
        timestamptz created_at
        timestamptz last_access_at
        timestamptz expire_at
        boolean is_active
        uuid superseded_by FK
    }

    chat_sessions ||--o{ chat_messages : "session_id"
    memory_records ||--o{ memory_records : "superseded_by"
```

## 7. 项目目录总览

```mermaid
graph LR
    subgraph 项目根
        CONFIG[config.py]
        ENV[.env]
        API_DIR[api/]
        LLM_DIR[llm/]
        RET_DIR[retrieval/]
        PRE_DIR[preprocessing/]
        MA_DIR[multi_agent/]
        SQL_DIR[sql_agent/]
        REP_DIR[report_agent/]
        MEM_DIR[memory/]
        UTIL_DIR[utils/]
        WEB_DIR[web/]
        DATA_DIR[data/]
    end

    API_DIR --> MA_DIR & SQL_DIR & RET_DIR & REP_DIR
    MA_DIR --> MEM_DIR & SQL_DIR & RET_DIR & REP_DIR
    MEM_DIR --> CONFIG
    RET_DIR --> CONFIG

    style MEM_DIR fill:#4a9,stroke:#333,color:#fff
    style MA_DIR fill:#49a,stroke:#333,color:#fff
    style API_DIR fill:#94a,stroke:#333,color:#fff
    style WEB_DIR fill:#a94,stroke:#333,color:#fff
```
