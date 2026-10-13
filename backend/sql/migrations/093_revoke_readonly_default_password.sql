-- This forward migration must be applied by scripts.init_db.apply_migration, which
-- commits NOLOGIN before terminating old sessions and then applies the configured
-- PG_READONLY_USER / PG_READONLY_PASSWORD. Standalone execution fails closed.
ALTER ROLE agent_readonly NOLOGIN;
