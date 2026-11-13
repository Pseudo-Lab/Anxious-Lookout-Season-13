"""#90: the API's stream evidence lines (api_stream_summary, conversation_refresh_write) go to a logger that uvicorn's
default configuration emits at INFO. The un-configured "hosting" logger drops INFO (root WARNING), so P4's slot
evidence would never appear."""
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

SOURCE = (Path(__file__).resolve().parents[1] / "app" / "conversations.py").read_text()


def test_evidence_lines_use_uvicorn_error():
    for name in ("api_stream_summary", "conversation_refresh_write"):
        line = next(line for line in SOURCE.splitlines() if '"' + name + " %s" in line)
        assert 'logging.getLogger("uvicorn.error").info(' in line


def test_uvicorn_default_config_emits_info_there_and_not_on_hosting():
    # Read the dict, do not apply it: dictConfig would change logging for every later test in this process.
    loggers = LOGGING_CONFIG["loggers"]
    assert loggers["uvicorn.error"]["level"] == "INFO" and loggers["uvicorn"]["handlers"]
    assert "hosting" not in loggers and "root" not in LOGGING_CONFIG  # root stays at WARNING: INFO on "hosting" is dropped
