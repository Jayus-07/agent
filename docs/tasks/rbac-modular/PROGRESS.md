# Progress: Tier 12 RBAC 路由模块化解耦重构 (rbac.py)

- [x] 独立 Git 工作树 `agent-rbac` 创建完成（分支 `refactor/rbac-modular`）
- [x] 配置环境与单测隔离准备
- [x] 存量测试基线确认（63 个用例全部绿灯）
- [x] 领域子包 `rbac_subroutes/` 构建与透明代理设计实现（10 个专职子模块）
- [x] 门面瘦身至 71 行（从 1995 行缩减 96.4%），完备重导出所有契约符号与猴子补丁穿透
- [x] 架构守卫测试 `test_rbac_modular_architecture.py` 编写并 100% 通过（5/5 passed）
- [x] CI 清单 `.github/workflows/rag_smoke.yml` 更新
- [x] 本地全量回归验证通过（63 + 5 = 68 项测试全部绿灯）
- [ ] PR 提交、CI 门禁与合并收尾
