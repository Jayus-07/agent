-- 004 is an applied migration; this forward step safely updates the readonly credential.
-- The migration runner sets a configured password here, or revokes password auth if unset.
ALTER ROLE agent_readonly PASSWORD NULL;
