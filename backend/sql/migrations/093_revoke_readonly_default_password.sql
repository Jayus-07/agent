-- 004 is an applied migration; this forward step safely updates the readonly credential.
-- The migration runner sets a configured password here, or revokes password auth if unset.
ALTER ROLE agent_readonly PASSWORD NULL;

-- Password changes do not invalidate already authenticated PostgreSQL sessions.
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE usename = 'agent_readonly'
  AND pid <> pg_backend_pid();
