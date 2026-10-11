# 全平台开发规范与代码/目录统一任务

- 状态：**第一阶段规范已提交至 PR #21 分支；第二阶段静态盘点进行中；第三阶段代码与目录整改尚未施工。**
- 审计代码基线：GitHub `main@47731825326a70b5dd353d7d093e4a80349bfce6`（2026-10-11 远端）。
- 规范分支：`task/docs-architecture-failure-standard-20261011`。当前文档不改动业务代码。
- 目标：面向所有模块，按统一边界审查并分批消除真实差异；不依靠大搬家或删测试来“变干净”。

## 范围

API/网关、Router/LangGraph/Node/Workflow、Agent/Skill/Tool/MCP/Provider、旅游/客服/RAG/SQL/选品、身份与多租户、数据库/迁移、任务队列、记忆与模型成本、Web/Admin/CS 前端、可观测性、CI/测试、数据与杂项文件、运维文档。所有域与前端均在范围中，**但静态盘点不是全部代码逐行审计或真实运行验收**。

## 权威规范

[架构分层](../../architecture/module-boundaries.md) · [目录规范](../../development/repository-layout.md) · [横切契约](../../development/cross-cutting-contracts.md) · [Tool 失败](../../architecture/failure-degradation.md) · [重构手册](../../development/refactoring-playbook.md) · [测试](../../development/testing-guide.md) · [发布门禁](../../operations/release-quality.md)。

## 执行批次与停止条件

0. **Baseline（只读）**：建立 Git 跟踪资产、活跃代码入口、重复候选和在途任务清单；按 KEEP/FIX/MERGE/MOVE/DELETE/REVIEW 分类，保留证据。
1. **Safe Cleanup**：不动业务语义地整理重复导入、文档/脚本说明、明确生成物的可回收候选。先备份/引用核对，不能擅自清理现有 `tmp/`、`data/`、`artifacts/`。
2. **Shared Code**：针对经静态+行为分析证明相同的前端工具与公共 DTO/错误适配，先抽取可复用实现，再分客户端改引用；每步 T1/T2。
3. **Domain / Runtime**：仅对证明存在的反向依赖、重复业务入口与不一致契约实施小批迁移；保持审批/权限/幂等/会话/数据库行为不变，必要时 ADR/数据迁移与回滚。
4. **Closure**：逐域逐入口验证正常/异常/权限/并发/成本/Trace/前端终态；未跑真实链路均标 BLOCKED/NOT VERIFIED。

不可跨越的红线：未经引用和所有权审核不删测试、不动历史运行数据；不强推、不改其他工作树、不重建共享库、不把“推送分支”说成“已经主线采用”。

## 验收格式

每项 `P0/P1/P2 + KEEP/FIX/MERGE/MOVE/DELETE/REVIEW + 事实源 + 差异 + 改动影响 + 验证命令 + 结论 + 证据 + 回滚`。任何阶段均不以单一测试绿替代相关调用方的契约测试。