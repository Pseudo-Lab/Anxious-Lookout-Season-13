"""PAT branch of the existing research router, with independent principal/audit."""
import time
from uuid import uuid4
from pydantic import ValidationError
from sqlalchemy import select, text

from . import research_api_budget as budget
from . import research_pats as pats
from . import research_receipts as receipts
from .pat_models import Credential, PatAudit
from .models import Account
from .research import Store, ResearchError, fail, mutation_lock, parse_id
from .research_credentials import reject_token_input
from .research_tools import dispatch
from .version_authors import VersionAuthor


def call(app, sessions, operation, body, token, response):
    from .research_api import require_schema, operation_failure
    with budget.authorization() as deadline:
        principal = pats.observe(sessions, token)
        reject_token_input(body)
        budget.checkout()
        with sessions.begin() as db:
            # Account first, mutation advisory second, credential last. New
            # Session prevents stale ORM state from candidate/admission reads.
            account = db.scalar(select(Account).where(Account.id == principal.owner).with_for_update(read=True))
            if operation in receipts.MUTATIONS:
                key = parse_id(body.get('mutationId'))
                db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
                           {'key': mutation_lock(principal.owner, key)})
            row = db.scalar(select(Credential).where(Credential.id == principal.credential,
                Credential.owner_id == principal.owner).with_for_update())
            checked = pats.resolve(row, token, account, pats.now())
            pats.allowed(checked, operation, receipts.MUTATIONS)
            if app.state.research_policy == 'pending':
                fail('not_ready', 'Research API is unavailable', 503)
            require_schema(db.connection())
            # Schema/lock work may consume time. Recheck the expiry at the actual
            # authorization boundary, before action and any idempotent replay.
            checked = pats.resolve(row, token, account, pats.now())
            deadline.remaining()
            deadline.end = time.monotonic() + 30
            store = Store(db, checked.owner, author=VersionAuthor(credential_id=checked.credential, name=checked.name))
            code = 200
            identity = uuid4()
            try:
                with db.begin_nested():
                    result = dispatch(store, operation, body)
                    safe = receipts.receipt(operation, result, body, identity)
            except (ResearchError, ValidationError) as error:
                result, code = operation_failure(error)
                safe = receipts.receipt(operation, None, {}, identity, code=result['error']['code'])
            # Explicit fields only, no raw input, body, headers, token/hash or
            # command. No conversation row/F2 receipt is fabricated.
            summary = {key: value for key, value in safe.items() if key in {'code', 'saved', 'target', 'count'}}
            db.add(PatAudit(id=identity, owner_id=checked.owner, credential_id=checked.credential,
                operation=operation, status='failed' if code != 200 else 'succeeded', summary=summary))
            db.flush()
        return response(result, code)
