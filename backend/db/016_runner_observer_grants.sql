-- Separate root observer login receives this capability; never grant to API.
CREATE ROLE anxious_runner_observer NOLOGIN;
GRANT USAGE ON SCHEMA auth,research,runner_runtime,runner_bindings,runner_termination TO anxious_runner_observer;
GRANT SELECT ON auth.accounts TO anxious_runner_observer;
GRANT UPDATE(updated_at) ON auth.accounts TO anxious_runner_observer;
GRANT SELECT(token_hash,account_id,expires_at) ON auth.sessions TO anxious_runner_observer;
GRANT SELECT(owner_id,state) ON research.conversations TO anxious_runner_observer;
GRANT SELECT ON ALL TABLES IN SCHEMA runner_runtime,runner_bindings,runner_termination TO anxious_runner_observer;
GRANT INSERT ON runner_runtime.entry_receipts,runner_runtime.outbox,runner_termination.stops TO anxious_runner_observer;
GRANT UPDATE(phase,generation,revision,pod_uid,active_requests,ambiguous,last_activity)
  ON runner_runtime.registry TO anxious_runner_observer;
GRANT UPDATE(node_name,node_uid,boot_id,receipt,proof,completed_at) ON runner_termination.stops TO anxious_runner_observer;
GRANT UPDATE(consumed_at) ON runner_termination.entries TO anxious_runner_observer;
