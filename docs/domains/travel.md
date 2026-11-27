# 旅游规划 V2 业务域架构与并发控制指南

本文系统阐述旅游规划域（Travel Domain）从 V1 到 V2 的架构演进、核心并发控制模型（Trip + Revision 乐观锁 CAS）、自研自托管外部能力增强（高德与小红书 MCP），以及基于 Checkpointer 的人工中断与状态机恢复机制。

---

## 一、 V1 架构退役与 V2 正式落地

平台已彻底退役旧版 V1 行程版本服务的 Active/Draft 生命周期架构：
- **V1 彻底下线**：`backend/app/api/routes/travel.py` 中原本的 V1 端点已全部挂在 `_retired_v1_router` 且不再向 FastAPI 主应用注册；
- **V2 成为唯一生产主流**：
  - 对话式流式行程规划：`POST /api/travel/v2/plan/stream`（由 `backend/travel_v2/` 承接）；
  - 正式行程 CRUD 与结构化操作全部收敛于 `backend/travel_v2/api/`；
  - 前端 `/travel/itineraries/new` 完成了针对移动端弱网与长文本生成态体验的全面重构（PR #560）。

---

## 二、 Trip + Revision 乐观锁并发控制模型（CAS）

在真实生产中，用户可能在多端（如手机端、桌面端、以及后台 AI 自动优化）同时编辑同一份行程。若采用简单的覆盖写入，必将导致惨重的脏写与数据丢失。平台采用经典的 **Trip + Revision 链式版本模型结合 CAS（Compare-And-Swap）乐观锁**：

```mermaid
sequenceDiagram
    autonumber
    actor User as 客户端 (手机/网页)
    participant API as Travel V2 API (trips.py)
    participant Lock as PG 行锁 (FOR UPDATE)
    participant DB as PostgreSQL (trips & revisions)

    User->>API: POST /trips/{id}/edits<br>Headers: Idempotency-Key<br>Body: {expected_revision: 3, action: "add_place"}
    API->>Lock: BEGIN 事务; SELECT revision FROM trips WHERE id = :id FOR UPDATE
    Lock-->>API: 返回当前数据库最新版本 (current_revision = 3)
    
    alt 版本匹配 (current_revision == expected_revision)
        API->>DB: INSERT INTO trip_revisions (trip_id, rev_num=4, delta=...)
        API->>DB: UPDATE trips SET revision = 4, updated_at = NOW() WHERE id = :id
        API->>User: COMMIT; 200 OK (返回最新 Revision 4)
    else 版本冲突 (current_revision != expected_revision)
        API->>User: ROLLBACK; HTTP 409 Conflict<br>{code: "REVISION_CONFLICT", current: 4, expected: 3}
        Note over User: 客户端提示"行程已被其他端修改",<br>自动拉取最新版本 4 并展示差异合入
    end
```

### 1. 结构化编辑接口契约（`POST /api/travel/v2/trips/{trip_id}/edits`）
- **操作类型化**：每次提交必须为强类型的业务原子操作（如 `add_place`、`remove_place`、`reorder_places`、`adjust_timeslot`），严禁客户端直接传入一整段未经校验的纯文本覆盖数据库；
- **双重前置门禁**：
  - 强制要求 Header 提供 `Idempotency-Key`，防止网络重放；
  - 强制 Body 携带 `expected_revision`（客户端当前所持有的版本号）；
- **CAS 冲突处理**：
  - 在数据库行级锁（`SELECT ... FOR UPDATE`）内比对版本号；
  - 若 `current_revision != expected_revision`，立即抛出 **HTTP 409 Conflict**，绝不进行静默覆盖；
  - 操作成功后以追加写方式生成一条不可变的 `trip_revisions` 记录，并原子递增 `trips.revision`，保障行程变更历史具备完整的可审计回溯能力。

---

## 三、 外部动态增强与自研 MCP 集成体系

针对大模型容易虚构餐厅、编造景点开放时间的顽疾，平台通过 ADR-0011 规范将外部真实数据源封装为**自托管动态 MCP 服务**（PR #546, #550, #553, #554, #557）：

1. **高德地图自研 MCP（Amap MCP）**：
   - **周边美食推荐**：输入景点坐标，实时检索高德真实商户评分、人均消费与营业状态；
   - **POI 体验小贴士徽章**：实时提取景区的官方建议游玩时长、最佳游览季节；
   - **交通换乘卡片与距离测算**：基于高德驾车/公交路线规划 API 精准输出通勤耗时与换乘路线；
   - **静态地图打点缩略图**：自动根据行程各天 POI 坐标渲染静态轨迹地图，提升移动端直观感知。
2. **小红书真实攻略指引 MCP**：
   - 提取目的地高赞实操避坑攻略，作为辅助上下文送入 Reporter 节点，丰富行程小贴士；
3. **管理端探针与连通性治理**：
   - 管理端提供实时 Ping/Probe 探针面板；
   - 当外部地图或攻略 API 出现网络波动或配额耗尽时，运行时自动走优雅降级分支，并在前端标记“该商户信息待二次核实”，**严禁模型伪造虚假外部链接或未核验坐标**。

---

## 四、 人工决策中断（Interrupt）与事务级安全恢复

在规划长途旅行时，经常出现不可调和的硬性物理冲突（如：景点闭馆日恰好在行程当天、或两天行程地理跨度超出合理交通上限）。平台在 LangGraph 中设计了**人工决策挂起与状态机恢复机制**：

```
[执行计划校验] ──► 发现物理冲突 (如故宫周一闭馆)
                         │
                         ▼
           Checkpointer 健康检查 (健康方可挂起)
                         │
                         ▼
        生成带身份绑定的决策令牌 (decision_token)
                         │
                         ▼
        返回前端: needs_clarification (挂起状态)
                         │
                         ▼
             [用户在前端做出决策]
         ("移除冲突景点" 或 "调换游玩顺序")
                         │
                         ▼
   POST /travel/v2/plan/stream (带 decision_token)
                         │
                         ▼
         获取 PostgreSQL Advisory Lock 串行锁
                         │
                         ▼
         锁内重新加载 Checkpoint 并校验 Token 匹配性
                         │
                         ▼
       通过 V2 Trip 写入 & Revision CAS 原子更新行程
```

### 1. 决策令牌（`decision_token`）的严格绑定
- 令牌由服务端加密生成，强绑定：`checkpoint_id` + `tenant_id` + `user_id` + `conflict_hash`；
- 若用户在等待期间发起了其他修改导致行程状态变化，旧冲突被冲刷，持有旧令牌的请求将被拒绝，防止“拿前朝的决定解决今朝的冲突”。

### 2. PostgreSQL Advisory Lock 串行化防重入
- 恢复执行时，系统依据 `hash(tenant_id, user_id, trip_id)` 获取 PostgreSQL 会话级咨询锁（Advisory Lock）；
- 在咨询锁内部重读持久化 Checkpoint，确保并发请求不会重复应用同一次人工决策，随后继续走 V2 Trip Revision 的 CAS 校验流程。

---

## 五、 精选模板库与一键复制（迁移 155）

依据迁移 `155_travel_v2_featured_templates.sql`，系统构建了精选模板生态：
- **管理员发布模板**：运营人员可将经过人工实地验证的经典路线打标为 `featured=true` 并发布至全局模板市场；
- **一键深拷贝为正式行程**：普通用户浏览精选模板后，调用 `POST /api/travel/v2/templates/{template_id}/clone`：
  - 系统在事务内深度拷贝模板的日程（Arrangements）、地点坐标及小贴士；
  - 自动将所有者映射为当前登录用户的 `owner_id` 与 `tenant_id`，初始化 `revision = 1`；
  - 拷贝后的行程完全独立演进，用户可基于该蓝图继续触发 AI 结构化编辑。
