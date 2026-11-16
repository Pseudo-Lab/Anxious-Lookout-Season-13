"""Pre-#97 data setup for legacy acceptance tests; never an HTTP tool substitute.

These helpers seed retained pending records through their original storage code.
HTTP get/accept/discard, ownership, CSRF, concurrency and replay assertions stay on
the real routes. The production tool API rejects the retired creation operation.
"""
import os
import uuid

import httpx
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.research import Store, ResearchError
from app.research_models import Conversation


def seed_proposal(client, session, runner, name, arguments):
    from app.research_tools import SaveDocument, EditDocument
    from app.post_proposals import Propose, create as post_proposal
    from app.document_proposals import create as new_document_proposal
    from app.writing import create_proposal as document_proposal
    choices = {'research_post_propose': (Propose, post_proposal),
               'research_document_save': (SaveDocument, new_document_proposal),
               'research_document_update': (EditDocument, document_proposal)}
    model, action = choices[name]
    engine = create_engine(os.environ['ADMIN_DATABASE_URL'], hide_parameters=True)
    try:
        with Session(engine) as db, db.begin():
            conversation = db.get(Conversation, uuid.UUID(session['id']))
            store = Store(db, conversation.owner_id)
            parsed = model.model_validate(arguments)
            result, _ = store.mutate(parsed.mutationId, 'tool:' + name, arguments,
                                     lambda: action(store, parsed))
        return httpx.Response(200, json=result)
    except ResearchError as error:
        return httpx.Response(error.status, json={'error': {'code': error.code, 'details': error.details}})
    except ValidationError:
        return httpx.Response(422, json={'error': {'code': 'validation_error'}})
    finally:
        engine.dispose()
