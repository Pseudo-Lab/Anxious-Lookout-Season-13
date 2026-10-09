CREATE TABLE research.conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL REFERENCES auth.accounts(id),
  title varchar(300) NOT NULL CHECK(length(btrim(title)) > 0),
  state varchar(16) NOT NULL DEFAULT 'idle' CHECK(state IN ('idle','running','failed')),
  version integer NOT NULL DEFAULT 1 CHECK(version > 0),
  archived boolean NOT NULL DEFAULT false,
  native_record jsonb NOT NULL DEFAULT '{}',
  context jsonb,
  pending_text text,
  request_id uuid,
  tool_token_hash varchar(64),
  tool_expires_at timestamptz,
  login_hash varchar(64),
  error_code varchar(40),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX research_conversations_owner ON research.conversations(owner_id,archived,updated_at DESC,id DESC);
CREATE UNIQUE INDEX research_conversations_tool_token ON research.conversations(tool_token_hash) WHERE tool_token_hash IS NOT NULL;
REVOKE ALL ON research.conversations FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON research.conversations TO anxious_api;
