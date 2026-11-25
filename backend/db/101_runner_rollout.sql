-- #101: rolling runner-image replacement state, and the only write path to a binding's image.
-- Explicit administrator migration (ops.runner_rollout_migrate), after 010/016. No startup DDL.
--
-- Binding plans stay immutable except `image` (PM decision D3, enforced here, not only in Python):
-- no role but anxious_runner_binding_writer may UPDATE runner_bindings.bindings, and that NOLOGIN role
-- only owns move_image(), which rewrites `image` alone, by compare-and-swap on the whole old plan, and
-- only while the owner has no Pod and no create permission in its current generation; a stopped owner
-- also needs the completed stop proof of that generation (a legacy stop without one stays blocked).
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anxious_runner_binding_writer') THEN
    CREATE ROLE anxious_runner_binding_writer NOLOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA runner_runtime, runner_bindings, runner_termination TO anxious_runner_binding_writer;
GRANT SELECT(owner_id,state_id,phase,generation,pod_uid) ON runner_runtime.registry TO anxious_runner_binding_writer;
GRANT SELECT(owner_id,state_id,generation,kind,settled) ON runner_runtime.outbox TO anxious_runner_binding_writer;
GRANT SELECT(owner_id,state_id,generation,completed_at,proof) ON runner_termination.stops TO anxious_runner_binding_writer;
GRANT SELECT(owner_id,state_id,plan), UPDATE(plan) ON runner_bindings.bindings TO anxious_runner_binding_writer;

CREATE SCHEMA runner_rollout;
REVOKE ALL ON SCHEMA runner_rollout FROM PUBLIC;
CREATE TABLE runner_rollout.targets (
  target_id uuid PRIMARY KEY,
  fingerprint jsonb NOT NULL CHECK(jsonb_typeof(fingerprint)='object'),
  fingerprint_sha text NOT NULL CHECK(fingerprint_sha ~ '^[0-9a-f]{64}$'),
  state text NOT NULL CHECK(state IN ('canary','rolling','complete','halted','superseded')),
  reason text CHECK(reason IS NULL OR reason ~ '^[a-z0-9_:]{1,63}$'),
  reason_owner uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  CHECK((state='halted') = (reason IS NOT NULL)),
  CHECK(reason_owner IS NULL OR reason IS NOT NULL),
  CHECK(state<>'complete' OR completed_at IS NOT NULL)
);
-- At most one current target; every earlier one is superseded.
CREATE UNIQUE INDEX targets_one_current ON runner_rollout.targets ((true)) WHERE state<>'superseded';
CREATE TABLE runner_rollout.owners (
  target_id uuid NOT NULL REFERENCES runner_rollout.targets(target_id),
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  phase text NOT NULL CHECK(phase IN ('waiting_turn','cutting','draining','moved','restarting','verified','blocked','failed')),
  canary boolean NOT NULL DEFAULT false,
  stop_id uuid,
  since timestamptz NOT NULL DEFAULT now(),
  reason text CHECK(reason IS NULL OR reason ~ '^[a-z0-9_:]{1,63}$'),
  PRIMARY KEY(target_id,owner_id),
  CHECK(phase NOT IN ('blocked','failed') OR reason IS NOT NULL)
);
REVOKE ALL ON ALL TABLES IN SCHEMA runner_rollout FROM PUBLIC;

CREATE FUNCTION runner_rollout.move_image(p_owner uuid, p_state uuid, p_old jsonb, p_image text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  moved jsonb;
BEGIN
  -- Each statement below must read rows committed before it ran, after the owner lock: under
  -- REPEATABLE READ/SERIALIZABLE the snapshot predates the lock and could miss a committed reserve.
  IF pg_catalog.current_setting('transaction_isolation') <> 'read committed' THEN
    RAISE EXCEPTION 'binding image move requires READ COMMITTED' USING ERRCODE = 'check_violation';
  END IF;
  IF p_owner IS NULL OR p_state IS NULL OR p_old IS NULL OR jsonb_typeof(p_old) IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_old->'image') IS DISTINCT FROM 'string'
     OR p_image IS NULL OR p_image !~ '^[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}$' OR p_old->>'image' = p_image THEN
    RAISE EXCEPTION 'binding image move refused' USING ERRCODE = 'check_violation';
  END IF;
  -- The registry owner lock (app.runner_registry.Registry._lock: the first 8 bytes of
  -- sha256('runner-registry-v1:<owner>') as a signed bigint). Every registry transition takes it, so
  -- the Pod/create-permission check below and the UPDATE see no concurrent entry, reserve or bind.
  PERFORM pg_catalog.pg_advisory_xact_lock(('x' || substr(encode(sha256(convert_to(
    'runner-registry-v1:' || p_owner::text, 'UTF8')), 'hex'), 1, 16))::bit(64)::bigint);
  UPDATE runner_bindings.bindings b SET plan = jsonb_set(b.plan, '{image}', to_jsonb(p_image), false)
   WHERE b.owner_id = p_owner AND b.state_id = p_state AND b.plan = p_old
     AND EXISTS (SELECT 1 FROM runner_runtime.registry r
                  WHERE r.owner_id = p_owner AND r.state_id = p_state AND r.pod_uid IS NULL
                    AND ((r.phase = 'idle_stopped'
                          -- Stopped by a proved stop of this generation; a legacy stop has no proof.
                          AND EXISTS (SELECT 1 FROM runner_termination.stops s
                                       WHERE s.owner_id = r.owner_id AND s.state_id = r.state_id
                                         AND s.generation = r.generation AND s.completed_at IS NOT NULL
                                         AND jsonb_typeof(s.proof) = 'object' AND s.proof <> '{}'::jsonb))
                         -- Waiting for a Pod: only before the create permission of this generation.
                         OR (r.phase IN ('provisioning','capacity_wait')
                             AND NOT EXISTS (SELECT 1 FROM runner_runtime.outbox o
                                              WHERE o.owner_id = r.owner_id AND o.state_id = r.state_id
                                                AND o.generation = r.generation AND o.kind = 'ensure'
                                                AND o.settled))))
  RETURNING b.plan INTO moved;
  IF moved IS NULL THEN
    RAISE EXCEPTION 'binding image move refused' USING ERRCODE = 'check_violation';
  END IF;
  RETURN moved;
END $$;
ALTER FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) OWNER TO anxious_runner_binding_writer;
REVOKE ALL ON FUNCTION runner_rollout.move_image(uuid,uuid,jsonb,text) FROM PUBLIC;
-- The controller's observer login receives its runner_rollout rights from 101_runner_rollout_grants.sql.
