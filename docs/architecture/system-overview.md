# 系统概览

本文说明部署边界和服务关系。端口、镜像参数、环境变量和运行开关由 Docker Compose、APISIX 与配置文件维护；本文不复制动态配置清单。

## 服务关系

- Web、管理端和客服工作台通过 APISIX 进入 FastAPI。
- FastAPI 承载 REST、Chat Runtime 与主 LangGraph Runtime。
- RAG 可以由应用内 Pipeline 执行，也可以经客户端接入独立 RAG 服务；远程模式以当前配置和服务实现为准。
- MCP 服务暴露现有 Tool 的协议入口，不替代应用内 Tool Runtime。
- PostgreSQL 保存业务、会话、运行状态和检索数据；Redis 提供缓存及任务消息基础设施。
- Celery 承载明确声明为异步的任务；/chat/stream 的请求执行与 SSE 生命周期遵循 Runtime 契约。
- Java 业务系统是独立部署边界；本仓库只维护已有的 Python 接入适配器。

## 网关与身份

APISIX 是外部请求入口。身份头由网关验证并注入，后端只接受已验证的服务端身份上下文；不能信任客户端自带的用户或租户标识。网关身份协议见 [identity-header-protocol.md](../contracts/identity-header-protocol.md)。

## 数据与运行边界

- 数据库结构及升级以 backend/sql/migrations/ 和实际数据库代码为准。
- Provider 凭据由受控配置/凭据来源提供，不能写入文档、日志或客户端构建产物。
- 外部服务、任务队列与模型不可用时按具体业务契约返回失败或降级；不得静默丢任务。
- 部署与运维操作参考仓库 Compose 文件及 [commands.md](../operations/commands.md)。