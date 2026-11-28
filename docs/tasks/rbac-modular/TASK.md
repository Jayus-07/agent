# Task: Tier 12 RBAC 路由模块化解耦重构 (rbac.py)

## 目标
- 将 1995 行的巨型单体 `backend/app/api/routes/rbac.py` 解耦并模块化；
- 构建专职子包 `backend/app/api/routes/rbac_subroutes/`；
- 原始门面 `backend/app/api/routes/rbac.py` 瘦身至 200 行以内，削减 >90%；
- 100% 保持所有路由契约（路径、方法、参数、状态码）、Pydantic 模型与 monkeypatch 穿透兼容；
- 编写架构守卫测试，全量单测回归通过，PR 门禁检查通过后 Squash Merge 合入 main。
