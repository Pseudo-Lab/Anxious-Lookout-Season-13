CREATE TABLE research.credentials (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 name varchar(80) NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
 scope varchar(8) NOT NULL CHECK (scope = 'read' OR scope = 'write'),
 purpose varchar(40) NOT NULL CHECK (purpose = 'research-pat-v1'),
 token_hash varchar(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
 issued_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 last_used_at timestamptz,
 rate_window timestamptz,
 rate_count integer NOT NULL DEFAULT 0 CHECK (rate_count >= 0),
 CONSTRAINT credentials_expiry_check CHECK (expires_at > issued_at)
);
CREATE INDEX credentials_owner_issued_idx ON research.credentials(owner_id, issued_at, id);
CREATE TABLE research.pat_issuances (
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 key uuid NOT NULL,
 fingerprint varchar(64) NOT NULL,
 credential_id uuid NOT NULL REFERENCES research.credentials(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (owner_id, key)
);
CREATE TABLE research.pat_audit (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES auth.accounts(id),
 credential_id uuid NOT NULL REFERENCES research.credentials(id),
 operation varchar(80) NOT NULL,
 status varchar(16) NOT NULL CHECK (status = 'succeeded' OR status = 'failed'),
 summary jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE research.versions ADD COLUMN author_name varchar(80), DROP CONSTRAINT versions_author_check;
ALTER TABLE research.versions ADD CONSTRAINT versions_author_check CHECK (
 (author_kind = 'human' OR author_kind = 'unknown' OR author_kind = 'codex' OR author_kind = 'pat')
 AND ((author_kind = 'codex') = (author_conversation_id IS NOT NULL))
 AND ((author_kind = 'pat') = (author_credential_id IS NOT NULL))
 AND ((author_kind = 'pat') = (author_name IS NOT NULL))
);
ALTER TABLE research.post_versions ADD COLUMN author_name varchar(80), DROP CONSTRAINT post_versions_author_check;
ALTER TABLE research.post_versions ADD CONSTRAINT post_versions_author_check CHECK (
 (author_kind = 'human' OR author_kind = 'unknown' OR author_kind = 'codex' OR author_kind = 'pat')
 AND ((author_kind = 'codex') = (author_conversation_id IS NOT NULL))
 AND ((author_kind = 'pat') = (author_credential_id IS NOT NULL))
 AND ((author_kind = 'pat') = (author_name IS NOT NULL))
);
REVOKE ALL ON research.credentials, research.pat_issuances, research.pat_audit FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON research.credentials TO anxious_api;
GRANT SELECT, INSERT ON research.pat_issuances, research.pat_audit TO anxious_api;
