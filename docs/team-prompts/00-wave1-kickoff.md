# 波次 1 · 团队启动指令（整块粘贴到支持 Team 的会话）

```
创建一个团队，4 个成员，按文件域严格分工，谁也不许越界。

开始前，四个人都先读：
  docs/2026-09-16-实施计划与Team提示词.md          （执行手册：命令、通过标准、熔断规则）
  docs/team-prompts/ 下自己那一份                  （你的任务卡）
另可参考 docs/2026-09-16-总交接与实施计划.md §5.1/§5.2（分工表与资源互斥矩阵）。

【成员 1 · 网关写手】只允许写 apisix/**
  任务：B5（S0-1）。详见 docs/team-prompts/01-member-gateway.md

【成员 2 · 文档收口写手】只允许写 docs/**，以及删除 Java 时代的 .bat
  任务：B9。详见 docs/team-prompts/02-member-docs.md

【成员 3 · 前端写手】只允许写 frontend/**
  任务：B11。详见 docs/team-prompts/03-member-frontend.md

【成员 4 · 审查员（只读）】不改任何文件
  等三位写手落地后逐条复核，按严重度排序输出问题清单。详见 docs/team-prompts/04-member-reviewer.md
  发现问题回报给对应写手去修，不要自己动手改。

【全员硬约束 · 违反即回滚】
1. 禁止改 backend/**（含 backend/travel/**）—— 上一轮就是因为在跑测试期间改后端，
   导致整轮约 20 分钟的结果作废。
2. 禁止 docker compose up/down/rebuild；禁止重启 app/postgres/redis/rag-service/mcp-service。
   只有网关写手可以 restart agent-apisix 这一个容器。
3. 提交一律路径限定：git commit -m "..." -- <paths>；新文件先 git add（否则报 pathspec did not match）；
   提交后必须 git log --oneline -1 与 git show --stat HEAD 复核。动手前先 git diff --cached --name-only
   看有没有别人 staged 的东西。
4. 同一文件不要并行编辑（并行 Edit 会互相覆盖，后写者赢，且两次都报成功）。
5. 不要跑后端全量测试（另有会话独占）。

最后汇总：每人的改动清单 + 实测验收数字 + 未决问题。
```

## 触发后的三个操作点

| 操作 | 作用 |
|---|---|
| `Shift+Tab` | 切**委派模式**，领导只能协调、不能自己动手改码（不切它常抢成员的活） |
| `Ctrl+T` | 切换共享任务列表（待处理/进行中/已完成，带依赖阻塞） |
| `@成员名` | 绕过领导直接找某成员；已完成成员发消息会自动唤醒 |

## 想加实施前 gate

在触发语末尾追加一句：

```
要求三位写手在改代码前先提交计划等我审批。
```

## 为什么只有 3 个写手

并行度上限是 **3 条线**（α 前端 / δ 网关 / ε 文档）。
**β 后端是独占资源**（跑测试期间谁都不能改 `backend/**`、不能动容器），
**γ 旅游线要等 `rebuild app` 窗口** —— 二者硬凑进本批只会重演纪律违反。
它们单独排在波次 2，见 `docs/team-prompts/05-wave2-beta-serial.md`。

> ⚠️ 触发前请先确认后端确实没人在跑（手册 §一 自检）。
> 截至 01:50，B1（定位卡死用例）**正由并发会话执行中**，本批务必别碰后端。
