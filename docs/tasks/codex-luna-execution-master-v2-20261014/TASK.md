# TASK — EXECUTION MASTER V2

## 目标
按 GitHub Issue #67 的 EXECUTION MASTER V2 顺序推进 A→B→C→D。当前包 A：在现有主图中实现 Conversation-first 主助手，固定可信产品入口、保留授权与运行时契约并提供可复现验证。

## 范围与约束
- 最新基线：`5df8b0603355566e7fa1eb87906129c11a8211ab`（原起始基线 `bf3cddf7ac7329f576f036cb1a9717a8f96dfd27`）；独立分支 `codex/main-assistant-conversation-first-20261014`。
- 可改主助手、共享身份/Orchestration 与必要客服 API 路径适配；不改 `backend/travel_v2/`、`frontend-admin/`、退款服务/状态机。
- 不合并 main、不写生产/共享数据库、不调用未获额度授权的付费 Provider。

## 验收与停止条件
A1–A6 实现及 T1/T2 真实可运行验证完成，提交并创建 PR；收集 CI/Review 并修复。T3 无隔离凭据时标记 BLOCKED，不冒报通过。A 达到 MERGE_READY 或唯一外部阻断后，按协议进入 B。
