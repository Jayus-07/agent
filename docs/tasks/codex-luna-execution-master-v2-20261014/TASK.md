# TASK — EXECUTION MASTER V2

## 目标
按 GitHub Issue #67 的 EXECUTION MASTER V2 顺序推进 A→B→C→D。当前继续阶段 A：Conversation-first 主助手的真实 APISIX/API/Next 闭环验收，并修复测试中发现的真实缺陷。

## 范围与约束
- 最新基线：main `d02c80c83a2249fa5ba7fd6899bda65a74fb31f0`；分支 `codex/main-assistant-conversation-first-20261014`；PR #69。
- 可改主助手、共享身份/编排和必要客服 API 路径适配；不改 `backend/travel_v2/`、`frontend-admin/`、退款服务/状态机。
- 使用独立 Compose project、network、volume、容器名、端口和 PostgreSQL；不读写共享/生产业务数据，不调用本地 Ollama。仅按用户授权使用管理端 Provider Probe 验证目标模型可达性；该入口会更新 `last_probe_*` 摘要，不能替代隔离生成验收。
- 用户已授权逐个合并达到 MERGE_READY 的 PR；不绕过测试/Review/CI 门禁。模型 API 真实生成仅经隔离 Provider Registry 做有预算上限的测试。

## 验收与停止条件
- A1–A6 的既有 T1/T2 证据保留在 `PROGRESS.md`。
- A 的 T3 要求实际 Next → APISIX → API 登录、可信产品身份、真实模型 `done.answer` 与 Trace/History/Memory、RAG 引用、只读 SQL 与写入拒绝、跨用户/租户 fail-closed、工具错误/取消/SSE 恢复及浏览器验证。
- Provider 凭据缺失时先完成所有零付费 T3；不得以 fixture、本地 Ollama、静态 fallback 或 HTTP 200 冒充模型成功。
- 只有适用 P0 T3、最新 main 回归、CI 与所需 Review 证据均通过，A 才能标记 MERGE_READY；未经用户授权不合并。
- A 达到 MERGE_READY 或唯一外部阻断后，按 EXECUTION MASTER V2 推进无依赖冲突的后续工作。
