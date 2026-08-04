# CLAUDE.md

> 项目级约束，详细设计见 docs/

## Project

电商 RAG + Multi-Agent 平台

Stack:
- Backend: FastAPI + LangGraph
- Frontend: Next.js 14 + React
- AI: DeepSeek / Qwen / Ollama
- Trace: backend/rag/tracer.py

## Architecture

调用链：

简单请求:
API → Skill → Tool / RAG / SQL / Memory

复杂任务:
API → Planner → Supervisor → Skill → Tool → Subsystem → Reporter

核心约束：

- Planner:
  - 只负责任务拆解
  - 输出 Capability DAG
  - 禁止调用 Tool / Skill / DB

- Supervisor:
  - Capability 调度
  - 状态管理
  - 并发控制

- Skill:
  - 业务能力封装
  - 不直接访问外部系统

- Tool:
  - 无状态
  - 可测试
  - 必须 Trace


## Design Principle

- 优先生产级方案
- 设计需考虑：
  - 可维护性
  - 可观测性
  - 故障恢复
  - 扩展能力

允许：
- 重构架构
- 删除低价值代码
- 优先简单有效实现


## Code Rules

### Backend

- Python:
  - snake_case
  - 类型注解
  - logger 替代 print
  - 使用具体异常
  - SQL 参数化

禁止：
- 业务代码直接 os.getenv
- except Exception: pass


### Frontend

- Next.js 14 + React
- 保持现有目录结构
- API 按 domain 分层
- 禁止 alert / confirm
- Mock 必须匹配 DTO


## File Rules

禁止：

文件:
- misc.py
- helper.py
- common.py
- utils2.py

禁止 thin wrapper

例外:
- API compatibility layer
- DTO converter


## Priority

修改优先级：

P0:
- 数据错误
- 安全问题
- 崩溃
- Trace 丢失
- 内存泄漏


P1:
- 架构问题
- 强耦合
- 重复代码
- 长函数


P2:
- 命名
- 注释
- 小重构


## Change Flow

修改前：

1. 明确目标
2. 阅读代码
3. 分析影响范围
4. 修改
5. 测试


规则：

Bug:
- 先复现

Refactor:
- 修改前后测试通过

Feature:
- 优先补测试


## Validation

Backend:
- py_compile
- 相关测试


Frontend:
- npx tsc --noEmit
- npm test


## Docs

架构:
docs/architecture/

开发:
docs/development/

运维:
docs/operations/

可观测:
docs/observability/

经验:
memory/