-- 升级既有数据库：在 init_db.py 配置新口令前撤销旧只读角色密码。
-- PG_READONLY_PASSWORD 缺失时，agent_readonly 保持不可用密码认证状态。
ALTER ROLE agent_readonly PASSWORD NULL;
