CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.schema_version (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  version integer NOT NULL
);
CREATE TABLE IF NOT EXISTS auth.accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  github_id varchar(32) UNIQUE NOT NULL CHECK(github_id ~ '^[0-9]+$'),
  login varchar(100) NOT NULL,
  role varchar(16) NOT NULL DEFAULT 'commenter' CHECK(role IN ('commenter','editor','admin')),
  is_approved boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS auth.sessions (
  token_hash varchar(64) PRIMARY KEY,
  account_id uuid NOT NULL REFERENCES auth.accounts(id),
  csrf_token varchar(100) NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_account ON auth.sessions(account_id);
CREATE INDEX IF NOT EXISTS sessions_expiry ON auth.sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth.oauth_transactions (
  state_hash varchar(64) PRIMARY KEY,
  binding_hash varchar(64) NOT NULL,
  verifier text NOT NULL,
  expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS oauth_expiry ON auth.oauth_transactions(expires_at);
CREATE TABLE IF NOT EXISTS auth.permission_audit (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor varchar(200) NOT NULL,
  account_id uuid NOT NULL REFERENCES auth.accounts(id),
  reason text NOT NULL,
  before jsonb NOT NULL,
  after jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO auth.schema_version(singleton, version) VALUES(true, 1)
ON CONFLICT(singleton) DO NOTHING;
REVOKE ALL ON SCHEMA auth FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA auth FROM PUBLIC;
GRANT USAGE ON SCHEMA auth TO anxious_api;
GRANT SELECT ON auth.schema_version, auth.accounts TO anxious_api;
GRANT INSERT(github_id, login) ON auth.accounts TO anxious_api;
GRANT UPDATE(login, updated_at) ON auth.accounts TO anxious_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON auth.sessions, auth.oauth_transactions TO anxious_api;
