# 任务契约：AI 对话图运行核心（runner.py / iter_events）事件分发解耦重构

## 背景与痛点
- 现状：`backend/orchestration/graph/runner.py` 单文件长达 1716 行，其中上帝函数 `iter_events()` 长达 **931 行**（L387-L1317）。
- 异味根因：
  1. 单个 `while` 循环硬编码匹配所有图事件类型（`on_chat_model_start`, `on_tool_start`, `on_tool_end`, `node_transition` 等）；
  2. 流式文本增量推送、SQL/RAG 结构化卡片装配、工具调用生命周期、调用链路度量与错误降级话术严重纠缠在分支内；
  3. 圈复杂度极高，局部修改极易破坏端到端 SSE 协议与前端卡片渲染。

## 目标与重构范式
- 遵循 **单一职责原则（SRP）** 与 **Event Dispatcher（事件分发器）模式**：
  1. 建立专职子包 `backend/orchestration/graph/handlers/`：
     - `base.py`: 定义 `GraphEventHandler` 抽象基类与上下文协议
     - `stream_handler.py`: 打字机流式推送、分片抽取与推理块处理
     - `tool_handler.py`: 工具调用参数解析、执行生命周期与事件通知
     - `card_handler.py`: SQL 表格卡片、RAG 检索卡片、调试卡片构建与装配
     - `error_handler.py`: 异常捕获、降级话术与错误卡片构建
     - `transition_handler.py`: 节点状态机迁移、进度更新与生命周期钩子
  2. 瘦身 `runner.py`：
     - `iter_events()` 从 931 行瘦身为 60~100 行的高可读事件流管道；
     - `runner.py` 整体行数控制在 500~700 行以内；
     - 100% 保持对外 API 导出与 SSE 协议无损。
  3. 建立架构守卫测试：`backend/tests/orchestration/test_runner_modular_architecture.py`。
  4. 通过全量图运行时、SSE 事件流与前端卡片协议回归测试。

## 约束
- 遵循 `AGENTS.md`：在独立工作树 `D:\Program Files\workplace\agent-graph-runner` 的分支 `refactor/graph-runner-dispatcher` 施工；
- 保持向后兼容：三端前端与 SSE 协议格式严格零漂移；
- 必须通过 GitHub Actions CI 门禁并经确认后方可合入。
