-- Additive, explicit controller-owned binding publication. No credentials stored.
CREATE SCHEMA runner_bindings;
REVOKE ALL ON SCHEMA runner_bindings FROM PUBLIC;
GRANT USAGE ON SCHEMA runner_bindings TO anxious_api;
CREATE TABLE runner_bindings.bindings (
  owner_id uuid PRIMARY KEY REFERENCES runner_runtime.registry(owner_id),
  state_id uuid NOT NULL UNIQUE REFERENCES runner_runtime.registry(state_id),
  namespace text NOT NULL,
  namespace_uid uuid NOT NULL,
  plan jsonb NOT NULL CHECK(jsonb_typeof(plan)='object'),
  published_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runner_bindings.resources (
  owner_id uuid NOT NULL REFERENCES runner_bindings.bindings(owner_id),
  kind text NOT NULL CHECK(kind IN ('pvc','pv','secret')),
  name text NOT NULL,
  uid uuid NOT NULL,
  PRIMARY KEY(kind,name),
  UNIQUE(kind,uid)
);
REVOKE ALL ON ALL TABLES IN SCHEMA runner_bindings FROM PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA runner_bindings TO anxious_api;
-- Provisioning runs with a separately supplied controller DB identity. No writes
-- are granted to the API. Explicit deployment creates/grants that role.
