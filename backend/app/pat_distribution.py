"""Fixed public distribution paths; no arbitrary filesystem download."""
from pathlib import Path
from fastapi.responses import FileResponse
from .research_tools import TOOLS

ROOT = Path(__file__).resolve().parent.parent / 'client' / 'anxious-research-pat'


def register(app, settings, response):
    prefix = settings.base_path + '/api/research/client'

    @app.get(prefix + '/pat-client.py')
    def client():
        return FileResponse(ROOT / 'scripts/pat-client.py', media_type='text/x-python',
            filename='pat-client.py', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/SKILL.md')
    def skill():
        return FileResponse(ROOT / 'SKILL.md', media_type='text/markdown',
            filename='SKILL.md', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get(prefix + '/operations.json')
    def schema():
        return response({name: {'description': description, 'argumentsSchema': model.model_json_schema()}
                         for name, (model, description) in TOOLS.items() if name != 'research_page_context'})
