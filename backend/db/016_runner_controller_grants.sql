-- Explicit administrator action, separate from the read-only binding migration.
-- Capability role has no login. Grant it only to the deployment's controller login;
-- never grant it to anxious_api. Password/connection material stays in secret files.
CREATE ROLE anxious_runner_controller NOLOGIN;
GRANT USAGE ON SCHEMA auth,runner_runtime,runner_bindings TO anxious_runner_controller;
GRANT SELECT ON auth.accounts TO anxious_runner_controller;
-- PostgreSQL row SHARE locks require an UPDATE privilege; do not grant role or
-- approval mutation. This timestamp-only grant permits the membership lock.
GRANT UPDATE(updated_at) ON auth.accounts TO anxious_runner_controller;
GRANT SELECT ON ALL TABLES IN SCHEMA runner_runtime,runner_bindings TO anxious_runner_controller;
GRANT INSERT ON runner_bindings.bindings,runner_bindings.resources,runner_runtime.outbox TO anxious_runner_controller;
GRANT UPDATE(phase,generation,revision,pod_uid,active_requests,ambiguous,last_activity)
  ON runner_runtime.registry TO anxious_runner_controller;
GRANT UPDATE(settled) ON runner_runtime.outbox TO anxious_runner_controller;
