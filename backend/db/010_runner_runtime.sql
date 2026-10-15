CREATE SCHEMA runner_runtime;
REVOKE ALL ON SCHEMA runner_runtime FROM PUBLIC;
GRANT USAGE ON SCHEMA runner_runtime TO anxious_api;

CREATE TABLE runner_runtime.schema_version (
  singleton boolean PRIMARY KEY CHECK(singleton),
  version integer NOT NULL CHECK(version=1)
);
INSERT INTO runner_runtime.schema_version VALUES(true,1);
CREATE TABLE runner_runtime.registry (
  owner_id uuid PRIMARY KEY REFERENCES auth.accounts(id),
  state_id uuid UNIQUE NOT NULL,
  phase text NOT NULL CHECK(phase IN ('not_prepared','provisioning','capacity_wait','ready','runner_unreachable','runner_failed','stopping','idle_stopped')),
  generation bigint NOT NULL CHECK(generation>=0),
  revision bigint NOT NULL CHECK(revision>=0),
  pod_uid uuid UNIQUE,
  active_requests bigint NOT NULL CHECK(active_requests>=0),
  ambiguous boolean NOT NULL,
  last_activity timestamptz NOT NULL,
  CHECK((phase='not_prepared' AND generation=0 AND pod_uid IS NULL) OR (phase<>'not_prepared' AND generation>0)),
  CHECK(phase NOT IN ('ready','runner_unreachable','stopping') OR pod_uid IS NOT NULL),
  CHECK((active_requests=0 AND NOT ambiguous) OR pod_uid IS NOT NULL),
  CHECK(phase NOT IN ('not_prepared','idle_stopped','stopping','capacity_wait') OR (active_requests=0 AND NOT ambiguous)),
  CHECK(phase NOT IN ('idle_stopped','capacity_wait') OR pod_uid IS NULL)
);
CREATE TABLE runner_runtime.entry_receipts (
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  key uuid NOT NULL,
  generation bigint NOT NULL CHECK(generation>0),
  created_at timestamptz NOT NULL,
  PRIMARY KEY(owner_id,key)
);
CREATE TABLE runner_runtime.admissions (
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  request_id uuid NOT NULL,
  state_id uuid NOT NULL,
  generation bigint NOT NULL CHECK(generation>0),
  pod_uid uuid NOT NULL,
  admitted_at timestamptz NOT NULL,
  completed_at timestamptz,
  PRIMARY KEY(owner_id,request_id),
  CHECK(completed_at IS NULL OR completed_at>=admitted_at)
);
CREATE INDEX runner_pending_admissions ON runner_runtime.admissions(owner_id) WHERE completed_at IS NULL;
CREATE TABLE runner_runtime.outbox (
  owner_id uuid NOT NULL REFERENCES runner_runtime.registry(owner_id),
  state_id uuid NOT NULL,
  generation bigint NOT NULL CHECK(generation>0),
  revision bigint NOT NULL CHECK(revision>0),
  pod_uid uuid,
  kind text NOT NULL CHECK(kind IN ('ensure','stop-pod-only')),
  settled boolean NOT NULL DEFAULT false,
  PRIMARY KEY(owner_id,generation,revision,kind),
  CHECK((kind='ensure' AND pod_uid IS NULL) OR (kind='stop-pod-only' AND pod_uid IS NOT NULL))
);
CREATE INDEX runner_pending_outbox ON runner_runtime.outbox(owner_id,generation,revision) WHERE NOT settled;
REVOKE ALL ON ALL TABLES IN SCHEMA runner_runtime FROM PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA runner_runtime TO anxious_api;
GRANT INSERT ON runner_runtime.registry,runner_runtime.entry_receipts,runner_runtime.admissions,runner_runtime.outbox TO anxious_api;
GRANT UPDATE(phase,generation,revision,pod_uid,active_requests,ambiguous,last_activity) ON runner_runtime.registry TO anxious_api;
GRANT UPDATE(completed_at) ON runner_runtime.admissions TO anxious_api;
GRANT UPDATE(settled) ON runner_runtime.outbox TO anxious_api;
