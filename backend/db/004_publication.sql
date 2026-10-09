-- Additive capability history. auth columns/marker are deliberately unchanged.
CREATE TABLE research.publications (
  id uuid PRIMARY KEY,
  document_id uuid NOT NULL,
  owner_id uuid NOT NULL,
  version_id uuid NOT NULL,
  version_kind varchar(16) NOT NULL DEFAULT 'document' CHECK(version_kind='document'),
  snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(version_id,document_id,owner_id,version_kind) REFERENCES research.versions(id,item_id,owner_id,kind),
  UNIQUE(id,document_id,owner_id)
);
CREATE TABLE research.publication_heads (
  document_id uuid PRIMARY KEY,
  owner_id uuid NOT NULL,
  kind varchar(16) NOT NULL DEFAULT 'document' CHECK(kind='document'),
  publication_id uuid,
  FOREIGN KEY(document_id,owner_id,kind) REFERENCES research.items(id,owner_id,kind),
  FOREIGN KEY(publication_id,document_id,owner_id) REFERENCES research.publications(id,document_id,owner_id)
);
REVOKE ALL ON research.publications,research.publication_heads FROM PUBLIC;
GRANT SELECT,INSERT ON research.publications TO anxious_api;
GRANT SELECT,INSERT,UPDATE ON research.publication_heads TO anxious_api;

CREATE FUNCTION research.set_membership(actor_cookie text, actor_origin text, target_id uuid,
  approved boolean, member_role text, expected_time timestamptz, change_reason text)
RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $membership$
DECLARE
  actor_id uuid;
  proof text;
  target_record auth.accounts%ROWTYPE;
BEGIN
  IF actor_cookie IS NULL OR length(actor_cookie) NOT BETWEEN 1 AND 200
     OR actor_origin IS NULL OR approved IS NULL OR member_role IS NULL
     OR member_role NOT IN ('editor','commenter') OR expected_time IS NULL
     OR change_reason IS NULL OR length(btrim(change_reason)) NOT BETWEEN 1 AND 4000 THEN
    RETURN 'forbidden';
  END IF;
  proof := encode(sha256(convert_to('m2-origin-session-v1','UTF8') || decode('00','hex')
           || convert_to(actor_origin,'UTF8') || decode('00','hex') || convert_to(actor_cookie,'UTF8')),'hex');
  SELECT s.account_id INTO actor_id FROM auth.sessions s
    WHERE s.token_hash=proof AND s.expires_at>clock_timestamp();
  IF actor_id IS NULL OR actor_id=target_id THEN RETURN 'forbidden'; END IF;
  -- Same account-lock order as operator operations; recheck proof after locking.
  PERFORM a.id FROM auth.accounts a WHERE a.id IN(actor_id,target_id) ORDER BY a.id FOR UPDATE;
  IF NOT EXISTS(SELECT 1 FROM auth.accounts a WHERE a.id=actor_id AND a.role='admin' AND a.is_approved)
     OR NOT EXISTS(SELECT 1 FROM auth.sessions s WHERE s.account_id=actor_id AND s.token_hash=proof
                   AND s.expires_at>clock_timestamp()) THEN RETURN 'forbidden'; END IF;
  SELECT * INTO target_record FROM auth.accounts a WHERE a.id=target_id;
  IF NOT FOUND THEN RETURN 'not_found'; END IF;
  IF target_record.role='admin' THEN RETURN 'forbidden'; END IF;
  IF target_record.updated_at<>expected_time THEN RETURN 'conflict'; END IF;
  IF target_record.role=member_role AND target_record.is_approved=approved THEN RETURN 'ok'; END IF;
  UPDATE auth.accounts SET role=member_role,is_approved=approved,updated_at=clock_timestamp() WHERE id=target_id;
  INSERT INTO auth.permission_audit(actor,account_id,reason,before,after)
    VALUES('account:'||actor_id::text,target_id,change_reason,
      jsonb_build_object('role',target_record.role,'isApproved',target_record.is_approved),
      jsonb_build_object('role',member_role,'isApproved',approved));
  DELETE FROM auth.sessions WHERE account_id=target_id;
  RETURN 'ok';
END;
$membership$;
REVOKE ALL ON FUNCTION research.set_membership(text,text,uuid,boolean,text,timestamptz,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION research.set_membership(text,text,uuid,boolean,text,timestamptz,text) TO anxious_api;
