-- #101 runner-controller, observer login: rollout progress, the binding-image move and the running turn
-- to interrupt at the turn deadline (decision D1). Install after 016_runner_observer_grants.sql and
-- 101_runner_rollout.sql. Never grant to anxious_api. No UPDATE on runner_bindings: the image moves
-- only through runner_rollout.move_image (decision D3).
GRANT USAGE ON SCHEMA runner_rollout TO anxious_runner_observer;
GRANT SELECT, INSERT, UPDATE ON runner_rollout.targets, runner_rollout.owners TO anxious_runner_observer;
GRANT EXECUTE ON FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) TO anxious_runner_observer;
GRANT SELECT(id,request_id) ON research.conversations TO anxious_runner_observer;
