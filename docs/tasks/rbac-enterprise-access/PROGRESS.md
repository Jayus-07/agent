# 进度

## 当前状态

实现完成并已提交，等待用户验收。工作树为 `C:\Users\wh\.codex\worktrees\rbac-audit-usernames\agent`，分支 `codex/rbac-enterprise-access`，提交 `ab65a39`，起点 `14cbd1a36cdb35bfdfa1de1985dcd2eb36ccb290`。

## 已完成

- 用户页仅管理平台用户、平台角色、部门和账号状态；客服角色、容量、档案启用和接单设置移到独立客服团队页。
- 新增客服团队接口，只能管理本租户启用客服部门中的在职用户；用户离开客服部门或账号被停用时，在同一事务停用客服档案并关闭接单。
- 部门增加业务类型和主管关联；平台管理员可在部门页配置。部门主管只能读取和更新其负责部门的基础成员资料及账号状态，后端重新核对租户、部门和主管关联。
- 更新访问控制导航、接口类型和授权文档；迁移 `104_rbac_department_type_manager.sql` 已登记到迁移运行器。
- 通用用户更新逻辑拒绝客服档案字段，清除了原先不可达的客服档案增删改代码。

## 验证

- `python -m py_compile backend/app/api/routes/rbac.py scripts/init_db.py`：通过。
- `ruff check backend/app/api/routes/rbac.py`：通过。
- `frontend-admin` 的 `tsc --noEmit`：通过。
- `git diff --check`：通过。
- 数据库运行时验证未执行：独立 worktree 没有 `.env` / `PGPASSWORD`，且未对共享数据库执行迁移。
- 前端生产构建未通过：worktree 的 `node_modules` junction 将 Next.js 解析到主目录带空格的路径，webpack 报 `Can't resolve './D:/Program Files/...'`；重试得到相同环境错误。TypeScript 静态编译已通过。

## 下一步

复核最终差异并提交此功能分支，交用户验收。未经确认不合并 main、不推送 GitHub；合并获确认后再按仓库规则重建共享 Docker 栈。
