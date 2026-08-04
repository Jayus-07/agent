"""
P0-2 决策记录：user_id 默认 'anonymous'，auth 接入后升级

日期：2026-07-16
关联文档：docs/observability/frontend-data-requirements.md §11.1.1 / §12.2.3

═══════════════════════════════════════════════════════════════
决策
═══════════════════════════════════════════════════════════════
sessions 表的 user_id 字段：
  - NOT NULL → NULL（可空）
  - DEFAULT 'anonymous'（写库时自动填充）

理由：
  - auth 是独立产品线（用户系统/SSO/权限），不应阻塞可观测性建设
  - 当前所有 trace 都没有真实 user_id（session_id 是 'prod-query-1' 等业务标识）
  - 设可空 → 老数据兼容，无需 backfill
  - auth 接入后只改默认值，schema 不变

═══════════════════════════════════════════════════════════════
当前实现（无 sessions 表，仅预留）
═══════════════════════════════════════════════════════════════
无（sessions 表是 §12 设计文档，未实现）

P0-2 落地代码：未来实现 SqliteStorage / PostgresStorage 时，
在 upsert_session() 方法中：

    def upsert_session(self, session: dict) -> None:
        user_id = session.get("user_id") or "anonymous"  # P0-2 默认值
        ...

═══════════════════════════════════════════════════════════════
Auth 接入路径（未来）
═══════════════════════════════════════════════════════════════
1. 引入 FastAPI Depends + JWT 中间件
2. 注入 current_user = Depends(get_current_user) 到所有路由
3. 在 trace.start() / session.upsert() 时：

    user_id = current_user.id  # JWT.sub
    # 不再需要 'anonymous' 默认值

4. 数据迁移（可选）：
   - 把已有 'anonymous' 的 session 按需 backfill
   - 或保持 'anonymous' 表示"未登录访问"（合规上有意义）

5. Schema 变化：**零**
   - user_id 仍可空（兼容 service-to-service 调用）
   - DEFAULT 'anonymous' → 移除（应用层强制必填）

═══════════════════════════════════════════════════════════════
验证清单
═══════════════════════════════════════════════════════════════
- [ ] sessions 表 user_id NOT NULL → NULL（迁移 SQL）
- [ ] ALTER TABLE sessions ALTER COLUMN user_id SET DEFAULT 'anonymous'
- [ ] Storage.upsert_session() 接受 user_id=None 并默认填充
- [ ] API 响应中 user_id='anonymous' 时前端可识别
- [ ] auth 接入后无需数据迁移（user_id 列不变）
"""
