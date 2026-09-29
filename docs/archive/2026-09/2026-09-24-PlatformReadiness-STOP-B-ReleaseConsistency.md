# Platform Readiness STOP B — Release Consistency & Migration Safety（发布一致性与迁移安全）

> 日期：2026-09-24 ｜ 前置：STOP A PASS（213976b，P0-2 迁移三层漂移活体）
> 本轮改动：6 文件（登记表/Build Identity/preflight/health 观测），零业务语义变更

---

## 1. Verdict

```text
STOP_B_PASS    = true
STOP_C_ALLOWED = true
```

---

## 2. P0-2 修复：迁移三层漂移（B3/B4）

**修复动作**：
1. `scripts/init_db.py` 登记 `048_memory_scope_and_versioning`（memory）与 `049_task_pending_recovery`（memory，runtime-managed）。
2. **发现并修复次生缺陷**：049 在 fresh 库上执行会 `UndefinedTable: tasks`（tasks 表由 `backend/tasks/schema.sql ensure_schema()` 运行时建立，非迁移链）——直接登记会让 fresh 链 rc=1。新增 `RUNTIME_MANAGED_MIGRATIONS` 语义：该类迁移统一 skip 并登记 `status='skipped'`，实库 schema 由 ensure_schema 幂等保证（不改 Phase3 会话的未提交文件）。
3. 真实库收敛：`init_db.py` 对权威库执行 → `048 applied（整文件成功）`、`049 skipped`，rc=0；schema_migrations 与登记表收敛。

**验证（真实命令与结果）**：
- fresh 链：scratch 库（`agent_memory_platformcheck/agent_business_platformcheck`）`--reset --yes` 全链重建 → **rc=0**，memory 42 行/41 applied+1 runtime-managed skip，business 10/10 applied，只读账号连通 ok。
- 幂等：连续 3 次执行 → 第 2/3 次全 skip，**rc=0**。
- preflight（`backend/scripts/verify_migration_state.py`，三层校验 repo×登记表×DB[+可选镜像]）：
  - 修复前：`missing_in_db: 048,049` + `missing_in_image: 048,049` → **FAIL**
  - 修复后：`[repo] 登记 52/52`、`[db][OK] 52 个全部已应用`、`MIGRATION_STATE_OK`；`--image agent-db-migrate` 仍报镜像缺 048/049（旧镜像待重建，归 STOP E 发布演练闭环）。
- 实测中纠正 preflight 自身两坑：①连接目标必须解析权威库（仓库 `.env` 的 localhost:5432 会命中宿主机原生 PG 同名旧库——双 PG 坑再现）；②两库 schema_migrations 有同名异值残留行，必须按 `(目标库, filename)` 定位，禁止跨库合并；③checksum 口径与 `checksum_of` 一致（sha256(utf-8)[:16]）。

## 3. Build Identity（B1/B2，A8/P1-17 闭环）

- Dockerfile：`ARG/ENV GIT_COMMIT/BUILD_TIME`（默认 unknown）。
- compose：新增 `x-build` 锚（context+args，从发布环境透传 `${GIT_COMMIT:-unknown}`），9 处 `build: .` 全部改为 `build: *build`（`docker compose config` 校验通过）。
- server.py：新增首个 startup handler `log_build_identity`——打印 `service/commit/build_time/environment`，无 secret。
- /health：新增 `build.{commit,build_time}` 与 `migrations.{status,packaged_max,applied_max,applied_count}`（软失败 unknown）。
- 接线验证：一次性构建注入 `GIT_COMMIT=abc1234` → 容器内 `commit= abc1234 build_time= 20260924` ✓。约定：**build.commit=unknown 即发布流程违规**（正式生效于 STOP G Release Gate Gate-0）。

## 4. Forward/Backward Compatibility（B5/B6/B10）

- 044-049 全量扫描：**无 DROP TABLE/COLUMN、TRUNCATE、ALTER TYPE、RENAME**——纯 expand（新增 nullable/default 列、新表、IF NOT EXISTS 索引）。Expand/Contract 原则已在遵循，记录并冻结。
- 结论：**新 schema 兼容旧 app**（旧代码不读新列）；旧 schema + 新 app 仅在「新代码硬依赖新列」时成立——048 的 memory 列已被应用、049 列由 ensure_schema 保证，当前窗口安全。
- 发布策略判定（B10）：**stop-the-world**。单机 compose 单副本无滚动能力，诚实声明：全量 `dev-rebuild.bat`（10 服务同批构建）为唯一发布形态；日常「只 rebuild app」路径是 mixed-version 入口（实测在运行的 4 个构建时刻并存即证据），Release Gate 以 build identity 一致性拦截：**app/worker/db-migrate 三者 GIT_COMMIT 必须同值**。当前在运栈 mixed（app 07:10 无身份 vs worker 02:19-05:40）——作为「已知旧态」记录，STOP E 发布演练统一重建后消除。

## 5. Fresh DB / Existing DB（B8/B9）

fresh：见 §2（chain 完整：建库→迁移→只读角色→连通性实测，seed 默认不灌、`--demo-sandbox` 可选）。existing：真实库收敛执行前后行数/数据无变化（init_db 输出 top 表 counts 一致），只新增 schema_migrations 两行。

## 6. 遗留

- 旧 db-migrate 镜像仍在运行（缺 048/049 文件）——需一次 `rebuild db-migrate`（与 app 同批），归 STOP E；重建前 preflight `--image` 层保持 FAIL 是正确信号。
- `agent_memory_platformcheck/agent_business_platformcheck` scratch 库保留作证据，STOP G 清理。
- preflight 未纳入 CI/定时——登记 P2-18（可挂 beat maintenance）。

---

```text
STOP_B_PASS    = true
STOP_C_ALLOWED = true
```
