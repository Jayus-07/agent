# 常用命令

以下命令以当前仓库根目录执行；仅在本地或已获授权的环境运行会停止、重建服务或访问真实 Provider 的命令。服务定义以 `docker-compose.yml` 和脚本为准。

## 本地服务

`devctl.bat` 是本地开发服务入口：`backend` 对应 Compose 的 `app`，`admin`、`cs`、`web` 分别启动对应前端。

~~~powershell
.\devctl.bat status
.\devctl.bat start backend
.\devctl.bat start web
.\devctl.bat stop web /y
.\devctl.bat restart all /y
.\devctl.bat rebuild /y
~~~

`rebuild` 会重建后端服务；日常查看状态和日志优先使用下面的 Compose 命令。共享环境中不要擅自停止或重建服务。

## Docker Compose 与排障

~~~powershell
docker compose ps
docker compose up -d apisix app
docker compose stop app
docker compose logs --tail=100 app
docker compose logs -f app agent-worker cs-dispatcher
docker compose --profile observability up -d prometheus grafana
~~~

停止或重建前先确认服务是否被其他会话使用。不要使用 `down -v` 清理共享数据卷。观测栈的启停配置见根目录 Compose 文件中的 `observability` profile。

## 后端与前端检查

后端局部测试从仓库根目录执行，并指定受影响测试路径：

~~~powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/<受影响路径> -q --no-cov
~~~

本地单独启动后端时：

~~~powershell
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.server:app --reload --host 127.0.0.1 --port 8000
~~~

前端在受影响的 `frontend/`、`frontend-admin/` 或 `frontend-cs/` 目录执行：

~~~powershell
npm test -- <测试文件>
npx tsc --noEmit
~~~

前端测试和类型检查按改动目录选择；`frontend-mp/` 使用自己的 Taro 构建脚本，不套用上述 Vitest 命令。

## RAG 与 SQL 评测

先用离线冒烟检查验证数据和规则：

~~~powershell
.\.venv\Scripts\python.exe -m backend.evaluation rag --smoke --no-ragas
.\.venv\Scripts\python.exe -m backend.evaluation sql --smoke
~~~

真实 LLM、Provider 或 SQL 执行需显式使用 `--live`，会访问外部服务或测试数据；仅在已配置且隔离的环境运行。评测选项以 `backend/evaluation/cli.py` 为准。

## 日志、Trace 与运行契约

- Compose 服务状态与日志使用上面的 `docker compose ps` 和 `docker compose logs`。
- Trace / Span 字段见 [Trace 模型](../observability/trace-model.md)；主图、SSE 和 Checkpoint 入口见 [AI Runtime](../architecture/ai-runtime.md)。
- 测试范围遵循唯一的[测试策略](../development/testing-guide.md)，不默认运行全量回归。
