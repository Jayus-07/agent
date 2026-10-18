# PR #52 本机真实诊断与最小修复

## 范围与限制

基于 main `b4341381a022839108029f862805b02e1a980fb6`、PR #52 `440a08e3bf65433fd125f7f10c2fac8c33ecbf0d`，建立唯一隔离Compose、完成免费预检、执行一次新真实Provider流水线，再根据真实证据最小修复。不得重跑付费Smoke、合并PR、发布生产Prompt、改Golden/阈值/模型或修改其他PR及worktree。

## 工作区

- 本轮worktree：`C:/Users/wh/.codex/worktrees/pr52-local-diagnostic-20261018/agent`
- 本轮本地修复分支：`codex/pr52-local-diagnostic-fix-20261018`
- 目标PR原远程分支：`codex/rag-multi-query-p0-20261013`；原分支被另一个worktree占用，故本轮从其冻结HEAD另建专用分支，交付时仅快进推送原远程分支，不改动另一个worktree。
- main免费验证专用checkout：`D:/tmp/pr52-main-free-20261018`，detached于冻结main。
- 环境与Artifact目录：`D:/tmp/rag-eval-20261018-pr52/`。

## 停止条件

真实流水线已经执行一次，额度已使用，后续只做免费回归及交付。RAG质量/RC-024/031未完成真实诊断，不能宣布算法验收通过或建议合并。完整证据、费用与风险见PROGRESS.md。
