# Task 1：P1 数据模型与迁移实现报告

## 结果

已完成客服自动派单、租户隔离、assignment offer 与事件 outbox 的 ORM 契约，以及 Alembic 0023 / 原生 028 幂等迁移。未修改用户已有的 Alembic 0014–0022、原生 027 overlay，也未触碰原工作树或依赖目录。

## 修改文件

- `backend/customer_service/models/conversation.py`
- `backend/customer_service/models/handoff.py`
- `backend/customer_service/models/agent.py`
- `backend/customer_service/models/assignment.py`
- `backend/customer_service/models/event.py`
- `backend/sql/alembic/memory/versions/0023_cs_dispatch.py`
- `backend/sql/migrations/028_cs_dispatch.sql`
- `backend/tests/customer_service/test_cs_dispatch_schema.py`
- 本报告文件

ORM 保留了现有兼容字段和旧状态；五个客服模型均增加 `tenant_id VARCHAR(64) NOT NULL` 与 ORM 默认值 `default`。新增了派单优先级/版本/offer 时间、坐席身份与接单状态、assignment 状态与版本、事件序号/outbox 字段及租户语义索引。`event_seq` 没有硬编码 ORM 默认值。

## TDD 红灯

先新增契约测试并运行：

```text
D:/Python/python.exe -m pytest tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
```

修正一次测试自身的顶层导入错误后，得到预期红灯：

```text
FFFFFFFF                                                                 [100%]
8 failed in 6.48s
```

失败原因是生产代码尚未提供 `CSHandoff.tenant_id`、派单/offer/outbox 字段、显式状态契约，且 `0023_cs_dispatch.py` 与 `028_cs_dispatch.sql` 尚不存在；不是断言语法或测试收集错误。

## GREEN 与验证

新增契约测试：

```text
D:/Python/python.exe -m pytest tests/customer_service/test_cs_dispatch_schema.py -q --no-cov
........                                                                 [100%]
8 passed in 5.06s
```

brief 指定的完整回归集：

```text
D:/Python/python.exe -m pytest tests/customer_service/test_cs_dispatch_schema.py tests/customer_service/test_models.py tests/customer_service/test_handoff_repo.py tests/customer_service/test_event_outbox.py -q --no-cov
.................................................                        [100%]
49 passed in 5.37s
```

额外验证：

- 5 个 ORM、0023 迁移和契约测试通过 `py_compile`，退出码 0。
- SQLAlchemy PostgreSQL DDL 编译通过，新增索引可生成合法 DDL。
- 在临时 PostgreSQL 数据库中执行 `028_cs_dispatch.sql` 两次均退出码 0；最终查询 `tenant_columns=5`、关键派单/outbox 索引数为 `4`。临时数据库和容器内 SQL 临时文件均已清理。

## 迁移假设

1. Alembic 0023 的 `down_revision` 固定为 `0022`；0014–0022 和原生 027 是工作树提供的基线 overlay，不属于本任务提交。
2. 028 可在五张表已由既有基线创建的存量库上执行，也可在空库执行；空库路径会建立本任务所需的最小完整客服表结构。
3. 存量行的租户字段统一回填为 `default`；派单/assignment 新的非空数值字段使用数据库默认值回填。旧 handoff 状态、`cs_agents.available`、assignment 的 `assigned_at/unassigned_at`、事件 `event_id` 均保留。
4. 为了兼容 006 以前的客服会话命名，原生迁移只对 `assigned_agent` 做非破坏性重命名，并按 `conversation_status` 或旧 `status` 创建会话租户/状态索引。
5. `event_seq` 对旧消息事件保持可空；普通消息事件可使用 `handoff_id IS NULL`，新的派单事件由数据库或后续服务负责提供单调序号。
6. 活动 handoff、活动 assignment、非空坐席身份、非空事件序号的重复数据，以及新增外键的存量孤儿引用，迁移会显式报错而不删除或静默改写，修复后可重复执行。

## 未解决风险

- 本任务未把真实生产存量库作为迁移目标；重复活动记录或孤儿外键会按上述设计阻断升级，需要运维先清理并重新运行迁移。
- `event_seq` 的单调性仍由数据库生成机制或后续服务保证；本任务刻意不在 ORM 中硬编码序号。
- 存量 assignment 若没有可推导的 `handoff_id` 会保持 NULL，这是保留历史行的兼容选择；自动派单新写入必须填充该字段，才能参与活动 assignment 唯一性约束。
- 0023 依赖工作树已有的 0022 基线；若集成目标不包含该 overlay，集成前必须重排 Alembic 基线。
