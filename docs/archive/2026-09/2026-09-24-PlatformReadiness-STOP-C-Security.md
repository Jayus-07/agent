# Platform Readiness STOP C — Security & Tenant Isolation（安全与租户隔离）

> 日期：2026-09-24 ｜ 前置：STOP B PASS（12945c2）
> 方法：代码审计（STOP A 入口矩阵）+ 实机碰撞复现（e2e_tenant_collision.py 双租户同 session_id）+ 隔离实例修复验证

---

## 1. Verdict

```text
STOP_C_PASS    = true   （两个 P0 实锤并在本 STOP 内最小修复+回归+隔离实例复验）
STOP_D_ALLOWED = true
```

## 2. P0 修复（复现 → 定位 → 最小修复 → 回归 → 实机重验）

### P0-3 checkpoint 跨租户碰撞
- **复现**（现行栈，A=default/50 与 B=tenant-b/51 使用同一 session）：
  A 规划厦门→三天；B 插入规划杭州→两天；A 续问「改成五天」→ **返回「# 杭州 5 天行程」**（B 的目的地）。物证：`checkpoints` 表同一 thread `travel:stopC3-leak-demo` 三条记录。
- **定位**：travel 域图 thread=`travel:{conv}` 不含租户/用户；LangGraph checkpoint 按 thread 定位 → 同 conv 跨租户共享执行态。
- **修复**（a0e662c）：thread namespace 扩展为 `{domain}:{tenant}:{user}:{conv}`（travel 实装、cs 契约对齐——CS checkpointer 当前关闭）。旧 checkpoint 成孤儿，由 graceful reconstruction（ConversationContext 按 (tenant,user,conv) 租户隔离重建）+ 7d TTL 兜底，跨轮连续性无损。
- **重验**：隔离实例（修复代码 + 真实 PG checkpointer + Redis）同序列 A2 返回**厦门**行程（无污染）；契约测试 10 例锁定（跨租户/跨用户/跨域/与 task thread 互不碰撞）。
- **冻结例外声明**：Domain Runtime CORE_FROZEN 的 thread 契约按其冻结条款「直接阻断性 P0 例外」最小修订，仅为 namespace 追加，路由/图结构零改动。

### P0-4 session 跨用户收养
- **定位**：`chat_sessions.session_id` 全局唯一且 `get_or_create` 无属主校验；`chat_messages` 仅按 session_id 存取 → 任意用户可用他人 session 读取全部 L2 历史并写入同一历史。复现序列中两租户 14 条消息混流（B 本轮未复述地址属措辞运气，通道存在）。
- **修复**：`get_or_create` 属主不匹配抛 `SessionOwnerMismatch`（fail-closed）；MemoryService 三入口（start_session/end_turn/save_messages）捕获后派生隔离存储键 `{session}::u:{sha1(user)[:8]}` 重试——B 的读写落自己的键，A 零暴露，**无 schema 变更**。
- **附带修复 HEAD 断裂**：并行 memory 会话的 1d81153 收编了 service.py 守卫但漏提交 session_repo.py（异常类定义），主干 import 断裂——本提交补齐。

## 3. C1 身份入口

九伪造头网关无条件剥离（gateway-auth.lua:48-49）；header/strict 模式只认网关注入头；login/refresh/logout/register 缺可信租户头 401（72be04d）；enforce 模式生产默认。E15 密钥硬门通过。**无伪造通道**。

## 4. C2/C5/C6/C7/C8 数据面隔离矩阵（实测+设计边界如实）

| 数据面 | 隔离维度 | 实测/证据 | 判定 |
|---|---|---|---|
| ConversationContext | (tenant,user,conv) 键 | E3：同 session 两租户两把 Redis 键各自厦门/杭州 | ✓ |
| Checkpoint | thread namespace（本轮修复） | 碰撞复现→修复→隔离实例复验 | ✓（修复后） |
| chat_sessions/messages | session_id + **属主守卫**（本轮修复） | 收养复现→派生键隔离 | ✓（修复后） |
| Memory L3 | (tenant,user,memory_key) + 检索双维过滤 | 模型/代码；pgvector 隔离用例（tests/memory，PGPORT=5433 口径） | ✓ |
| RAG | **KB 矩阵（audience/department），无 tenant 维度** | 代码（authorized_kbs；retrievers kb 过滤） | 设计边界：单租户部署现实（网关注入 default），多租户化前必须补 tenant 元数据（延续 P1-4 家族） |
| SQL 数仓 | **无租户列（结构隔离决策 D1）+ 行级 scope/权限门** | R2 实证 sql step permission_denied；C 实证 promotions 数据范围拒绝 | 设计边界（同上） |
| Tasks | user_id 属主 + 404 防枚举 | E3 跨租户 404；D8 owner 404 | ✓ |
| CS 业务表 | tenant_id 列 + 归属校验 + 确认 CAS | 代码+上轮证据 | ✓ |

## 5. C9/C10/C11 管理面与选品/CS 权限

- Admin 端点全量守卫链核对（STOP A §2 表）：/sys 写=admin、/admin/* =admin(_operator)、/approvals=admin、/prompts 三级、/observability 三个敏感端点=admin（其余只读面登记 P2-19 建议补 operator 门）。
- 选品两路由 `resolve_operator_role` 门禁复验（上轮 5 例回归常绿）；**水平语义=角色内共享运营数据集**（有意识设计，P1-1 记录），未授权访问=401。
- CS：查询/动作=归属校验+确认 CAS；工单流转=supervisor 双档闸；坐席 offer=绑定反查；ws=ticket 一次性。

## 6. C12 Upload Security（代码核验）

/rag/upload：扩展名白名单+MIME 校验+大小门（413 中间件）+文件数/去重（file_hash）+路径规范化（export 路径穿越防护同族）；存储落本地卷（无对象存储）。无 P0/P1。

## 7. C13/C14 泄漏与日志

- 密钥硬门复验通过（API Key/Bearer 不进日志，上一轮 E15+本轮策略观察）。
- **C14 最小脱敏落地**：新增 `observability/log_privacy.py::query_preview`（≤16 字预览+原文长度），接入 4 个 query 原文日志点（travel/selection prefilter、router 缓存命中、answer_cache 命中行并降 DEBUG）。未删任何日志，排障归因能力保留（命中类型+长度）。

## 8. C15 Security E2E Matrix

| Case | Expected | Result |
|---|---|---|
| forged user header | deny | ✓ 网关剥离+strict 模式（历史 B2 矩阵在案） |
| tenant collision（session） | isolated | 修复前 LEAK → 修复后 isolated（隔离实例复验） |
| checkpoint collision | isolated | 修复前污染 → 修复后 isolated |
| cross tenant task | 404 | ✓ E3 |
| cross tenant RAG | 设计边界 | KB 矩阵隔离；tenant 维度多租户化前补（P1-4） |
| SQL scope violation | deny | ✓ R2 permission_denied |
| selection unauthorized | deny | ✓ 401（resolve_operator_role） |
| CS action unauthorized | deny | ✓ 归属校验+CAS |
| admin unauthorized | deny | ✓ require_admin 全链 |
| API key leak | none | ✓ E15 硬门 |

## 9. 遗留

- P1-19 /observability 只读端点无角色门（traces/tokens/metrics 等，API-Key 可达）——建议统一 operator 门。
- P1-4 家族：RAG/SQL 数仓无 tenant 维度（多租户化前置条件，集中登记）。
- P2-19 /chat/messages|abort 等无身份端点的属主校验补齐。

```text
STOP_C_PASS    = true
STOP_D_ALLOWED = true
```
