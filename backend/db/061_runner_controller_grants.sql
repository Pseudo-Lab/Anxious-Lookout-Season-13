-- #61 runner-controller, observer login: close the work of a dead runner (node reboot or a
-- runner container exit) before its stop is released. A running conversation becomes failed
-- with codex_runner_lost and open admissions complete. Install once with the other #16 grants
-- (016_runner_observer_grants.sql first). Never grant to anxious_api.
GRANT SELECT(version) ON research.conversations TO anxious_runner_observer;
GRANT UPDATE(state,error_code,version,updated_at,tool_token_hash) ON research.conversations TO anxious_runner_observer;
GRANT UPDATE(completed_at) ON runner_runtime.admissions TO anxious_runner_observer;
