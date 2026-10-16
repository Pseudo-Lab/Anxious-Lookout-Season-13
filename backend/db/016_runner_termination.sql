-- Additive, explicit administrator migration. No startup DDL.
CREATE SCHEMA runner_termination;
REVOKE ALL ON SCHEMA runner_termination FROM PUBLIC;
CREATE TABLE runner_termination.stops (
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  state_id uuid NOT NULL REFERENCES runner_runtime.registry(state_id),
  generation bigint NOT NULL CHECK(generation>0),
  pod_uid uuid UNIQUE NOT NULL,
  stop_id uuid UNIQUE NOT NULL,
  requested_revision bigint NOT NULL CHECK(requested_revision>=0),
  requested_at timestamptz NOT NULL DEFAULT now(),
  node_name text,
  node_uid uuid,
  boot_id uuid,
  receipt jsonb,
  proof jsonb,
  completed_at timestamptz,
  PRIMARY KEY(owner_id,generation),
  CHECK((node_name IS NULL AND node_uid IS NULL AND boot_id IS NULL) OR
        (node_name IS NOT NULL AND length(node_name)>0 AND node_uid IS NOT NULL AND boot_id IS NOT NULL)),
  CHECK(receipt IS NULL OR jsonb_typeof(receipt)='object'),
  CHECK(proof IS NULL OR jsonb_typeof(proof)='object'),
  CHECK(completed_at IS NULL OR (proof IS NOT NULL AND node_uid IS NOT NULL))
);
CREATE TABLE runner_termination.entries (
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  key uuid NOT NULL,
  state_id uuid NOT NULL REFERENCES runner_runtime.registry(state_id),
  generation bigint NOT NULL CHECK(generation>0),
  session_hash varchar(64) NOT NULL REFERENCES auth.sessions(token_hash) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  consumed_at timestamptz,
  PRIMARY KEY(owner_id,key)
);
REVOKE ALL ON ALL TABLES IN SCHEMA runner_termination FROM PUBLIC;
GRANT USAGE ON SCHEMA runner_termination TO anxious_api;
GRANT SELECT ON ALL TABLES IN SCHEMA runner_termination TO anxious_api;
GRANT INSERT(owner_id,state_id,generation,pod_uid,stop_id,requested_revision)
  ON runner_termination.stops TO anxious_api;
GRANT INSERT(owner_id,key,state_id,generation,session_hash)
  ON runner_termination.entries TO anxious_api;
-- Observer role is provisioned separately. API cannot mark a stop completed,
-- attach evidence or consume an entry intent.
