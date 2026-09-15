# 成员 3 · 前端写手（α 线）

**可写路径**：`frontend/**`
**禁止**：`backend/**`、`apisix/**`、任何容器操作

---

## 任务 B11：前端 P0-2~P0-6 地基收敛

> **P0-2 已完成**（`c8161d5` 错误码机制 `src/api/errors.ts` + `ApiError.code`），
> 本任务实际范围是 **P0-3 / P0-4 / P0-5 / P0-6**。

| 子项 | 内容 | 通过标准 | 备注 |
|---|---|---|---|
| **P0-3** | `lib/api/*`(5 个) + `services/*`(11 个) → `src/api/<domain>.ts` | `tsc` rc=0 + `vitest` 通过；`grep -rn "@/services/" src \| grep -v index` 为空 | **建议单域一笔 commit**，逐域可 revert |
| **P0-4** | 按 v3 附录 C 迁 11 个根级组件；**合并 `EmptyState` 双份**；**本轮不引入 `features/`** | `tsc` + `vitest` + `NEXT_DIST_DIR=.next-p04 next build` | 逐文件可 revert |
| **P0-5** | `src/lib/api.ts`、`src/services/index.ts` 改纯 re-export | `tsc` rc=0 | 可 revert |
| **P0-6** | surface 契约测试（未迁移路径的兼容断言） | `vitest` 通过 | 纯新增 |

**权威源**：`docs/superpowers/plans/2026-09-15-frontend-split-plan-v3.md`（ADR-001~010、P0/P1/P2 提交级清单）。
**附录 C 组件归位表**在它的前身 `2026-09-15-frontend-split-implementation-plan.md`（v3 未复制，仍需回查）。

> ⚠️ **测试放哪要先定口径**：v3 写的是 `src/api/__tests__/xxx.test.ts`，
> 而**仓库现状是共置**（`src/api/client.test.ts`、`src/api/errors.test.ts`）。
> **默认按现状共置**，并在回报里说明你的选择（口径漂移是这类重构最容易积累的债）。

### 通过标准（两条门禁，数字必须实测）

```bash
export PATH="/usr/bin:/bin:/usr/local/bin:$PATH"
NODE="C:/Users/wh/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
cd "D:/Program Files/workplace/agent/frontend"

"$NODE" ./node_modules/typescript/bin/tsc --noEmit ; echo "tsc exit=$?"     # 期望 rc=0
"$NODE" ./node_modules/vitest/vitest.mjs run                                # 期望 ≥195 passed / 12 files
```

> - **不要用 `npx`**：走 WSL 通道会报 `execvpe(/bin/bash) failed`，必须用受管 Node 直调 `node_modules/vitest/vitest.mjs`。
> - **基线 195 passed / 12 files**（截至 `bb047ea`）。低于基线 = 回归，**不得「先提交再修」**。
> - 构建沙箱：`NEXT_DIST_DIR=.next-<每次新空目录>`。Next 会清 distDir，复用旧目录会被 safe-delete 拦。
> - **数路由只认 `page.tsx`**（当前 **31** 条），不要数目录。

### 明确的边界

- **不要碰 P1 路由分区（B12）/ P2 管理端（B13）** —— 二者都会改 URL 结构，**需用户授权**，不在本批。
- 不要动 `docker-compose*`、不要起容器、不要跑后端测试。
- **同一文件不要并行 Edit**（会互相覆盖，后写者赢且两次都报成功）。

### 提交

```bash
cd "D:/Program Files/workplace/agent"
git diff --cached --name-only           # 先看有没有别人 staged 的东西
git commit -m "refactor(api): <单域> 迁入 src/api（前端 P0-3）

- 验收：tsc --noEmit rc=0；vitest <实测数字> passed / <文件数> files
- 未改任何调用语义，仅 import 路径搬迁" -- frontend/src/api/<domain>.ts frontend/src/services/<domain>.ts ...
git log --oneline -1 && git show --stat HEAD
```

**回报格式**：每个子项的 commit hash / **实测**门禁数字（贴命令行）/ 测试目录选择及理由 / 未决问题。
