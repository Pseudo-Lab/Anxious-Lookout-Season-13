-- #119 additive: PAT access scope (M2) and the administrator membership audit read (L3).
-- Existing credentials keep access NULL, which means their current authority (D7). No backfill.
ALTER TABLE research.credentials ADD COLUMN access varchar(8);
ALTER TABLE research.credentials ADD CONSTRAINT credentials_access_check CHECK (access = 'read' OR access = 'write');

-- anxious_api has no grant on auth.permission_audit. This definer function re-checks the caller's own
-- session (same proof as research.set_membership) and approved-admin role, then returns audit rows only.
CREATE FUNCTION research.membership_audit(actor_cookie text, actor_origin text, target_id uuid,
  after_time timestamptz, after_id uuid, row_limit integer)
RETURNS TABLE(audit_id uuid, audit_time timestamptz, actor_label varchar, actor_login varchar,
  target_account_id uuid, target_login varchar, target_github_id varchar,
  change_reason text, before_state jsonb, after_state jsonb)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $audit$
DECLARE
  actor_id uuid;
  proof text;
BEGIN
  IF actor_cookie IS NULL OR length(actor_cookie) NOT BETWEEN 1 AND 200 OR actor_origin IS NULL
     OR row_limit IS NULL OR row_limit NOT BETWEEN 1 AND 101
     OR (after_time IS NULL) <> (after_id IS NULL) THEN
    RAISE EXCEPTION 'membership audit refused' USING ERRCODE = 'insufficient_privilege';
  END IF;
  proof := encode(sha256(convert_to('m2-origin-session-v1','UTF8') || decode('00','hex')
           || convert_to(actor_origin,'UTF8') || decode('00','hex') || convert_to(actor_cookie,'UTF8')),'hex');
  SELECT s.account_id INTO actor_id FROM auth.sessions s
    WHERE s.token_hash=proof AND s.expires_at>clock_timestamp();
  IF actor_id IS NULL OR NOT EXISTS(SELECT 1 FROM auth.accounts a WHERE a.id=actor_id AND a.role='admin' AND a.is_approved) THEN
    RAISE EXCEPTION 'membership audit refused' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN QUERY
    SELECT p.id, p.created_at, p.actor, x.login, p.account_id, t.login, t.github_id, p.reason, p.before, p.after
    FROM auth.permission_audit p
    JOIN auth.accounts t ON t.id=p.account_id
    LEFT JOIN auth.accounts x ON p.actor='account:'||x.id::text
    WHERE (target_id IS NULL OR p.account_id=target_id)
      AND (after_time IS NULL OR p.created_at<after_time OR (p.created_at=after_time AND p.id<after_id))
    ORDER BY p.created_at DESC, p.id DESC
    LIMIT row_limit;
END;
$audit$;
REVOKE ALL ON FUNCTION research.membership_audit(text,text,uuid,timestamptz,uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.membership_audit(text,text,uuid,timestamptz,uuid,integer) TO anxious_api;
