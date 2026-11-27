# 任务规划：Tier 6 - backend/app/api/routes/cs_admin.py 模块化解耦重构

## 1. 目标与背景
`backend/app/api/routes/cs_admin.py` 当前为 2990 行的超大文件，作为智能客服管理控制台的核心 API 路由，承担了高度密集的异构职责：
1. **技能组与坐席管理 (Skill Groups & Agents)**：技能组 CRUD、坐席技能绑定与在线状态维护；
2. **知识库与专家绑定 (Knowledge & Experts)**：知识库集合绑定、专家偏好配置、检索策略调整；
3. **会话转接与人工工单 (Handoff & Tickets)**：人工转接生命周期、会话认领/释放、工单状态流转、SLA 超时处置；
4. **质检与对话评测 (Quality & Audits)**：对话质检打分、敏感拦截历史回溯、质检规则配置；
5. **运营统计与监控大盘 (Analytics & Dashboard)**：接待量、SLA 达标率、满意度与转人工率统计；
6. **公共辅助与依赖注入 (Shared Dependencies & Models)**：Pydantic 请求/响应模型、鉴权依赖与通用序列化辅助。

本任务依据马斯克五步骤（质疑需求 -> 删除多余 -> 简化优化 -> 加快周期 -> 自动化）与单一职责原则（SRP），将超大路由解耦为专职子路由模块，门面 `cs_admin.py` 瘦身至纯 Router 聚合入口（控制在 250 行以内），同时 100% 保持对外 HTTP API 路由契约、参数结构及测试 Monkeypatch 的兼容性。

## 2. 改造范围
- 目标文件：`backend/app/api/routes/cs_admin.py`
- 重构后结构：
  - `backend/app/api/routes/cs_admin_subroutes/` (专职子领域路由包)
    - `models.py`: Pydantic 请求体/响应体 Schema 与公共常量定义
    - `deps.py`: 依赖注入（如权限校验 `require_cs_admin`、租户解析、DB Session 注入等）与通用辅助函数
    - `agents.py`: 坐席管理、技能组配置、坐席分配与在线状态端点
    - `knowledge.py`: 知识库绑定、专家配置与问答参数端点
    - `handoff.py`: 会话转接、工单认领/关闭、SLA 管理端点
    - `quality.py`: 质检评测、审查记录与敏感审计端点
    - `analytics.py`: 运营监控指标、大盘报表与趋势分析端点
    - `__init__.py`: 聚合子路由并构建统一主路由器
  - `backend/app/api/routes/cs_admin.py`: 门面 Router，继续导出 `router = APIRouter(...)`，并重新导出全部公开符号与函数
- 架构守卫测试：
  - `backend/tests/api/test_cs_admin_modular_architecture.py`

## 3. 验收标准
1. `backend/app/api/routes/cs_admin.py` 单文件行数由 2990 行瘦身至 250 行以内（>90% 降幅）；
2. FastAPI 路由表 (`/api/v1/cs/admin/...` 等) 路径、HTTP 方法、参数结构与状态码 100% 保持不变；
3. 全仓现有涉及 `cs_admin` 的测试全部通过（包含已有的 monkeypatch 场景）；
4. 架构守卫测试通过并防止职责重新混杂；
5. 新建 PR 并通过全套 CI 门禁合入 main。
