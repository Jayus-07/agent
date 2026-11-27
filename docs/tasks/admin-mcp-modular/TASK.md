# Tier 11: MCP 管理控制台单体路由重构与模块化解耦 (admin_mcp.py)

## 1. 目标与背景
- **目标文件**: `backend/app/api/routes/admin_mcp.py` (2080 行)
- **目标**: 将 2080 行的庞大控制器彻底解耦重构至 `backend/app/api/routes/admin_mcp_subroutes/` 领域子包中，将入口 `admin_mcp.py` 压缩至 200 行以内的瘦门面（目标削减 > 90%）。
- **非功能约束**:
  - 100% 保持既有路由契约（路径、HTTP Method、状态码、响应体结构）；
  - 100% 保持所有导出符号的公开/内部契约（包括数据模型、常量、辅助函数、路由实例）；
  - 100% 保持外部测试对 `backend.app.api.routes.admin_mcp` 模块属性的 monkeypatch 穿透能力（通过 `FacadeProxy` 实现）；
  - 编写架构守卫测试 `test_admin_mcp_modular_architecture.py`，加入 CI 守卫。

## 2. 架构设计规划
- **拆分目标子包**: `backend/app/api/routes/admin_mcp_subroutes/`
  - `models.py`: Pydantic 请求/响应模型、数据传输对象及常量声明；
  - `dynamic.py`: `FacadeProxy` 门面动态穿透代理，实现测试 patch 穿透；
  - `servers.py`: MCP Server 管理路由（注册、修改、删除、启动、停止、列表、详情、健康探测）；
  - `tools.py`: MCP 工具管理路由（发现、同步、启停、授权、Schema 反射）；
  - `audit.py`: MCP 调用审计日志、统计、指标报表与排错追溯路由；
  - `routes.py`: 统一 APIRouter 挂载与聚合；
  - `__init__.py`: 统一显式导出。
- **门面瘦身**:
  - `backend/app/api/routes/admin_mcp.py`: 仅保留纯导出声明与统一路由挂载，行数 < 200 行。

## 3. 验收标准
1. 存量 MCP 关联单元测试 100% 通过（零破坏、零退化）；
2. 架构守卫测试 100% 绿灯（验证行数限制、模块边界、符号导出与 monkeypatch 动态穿透）；
3. CI Checks 全部通过，PR 经 Squash Merge 合入 main。
