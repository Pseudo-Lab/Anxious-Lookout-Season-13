"""Development-only C1 factory. Normal runner entrypoints never import this module.

Use the two benchmark images built from the same runner base, with an immutable
root-owned mode marker (restart or retain). `retain` disables every MCP call; it
only compares model text latency with the same initialized MCP catalogue. It
cannot authorize an API operation with a reused process capability. C2 uses the
normal (restart) factory with actual calls. No shell/security policy is relaxed.
"""
import os
from pathlib import Path
import stat
import threading

from app.codex_policy import CodexFailure
from runner import app as runner_app

BaseNative = runner_app.Native


def mode():
    path = Path('/etc/codex/research-gate-mode')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o444:
        raise CodexFailure('policy_refused')
    value = path.read_text().strip()
    if value not in {'retain', 'restart'}:
        raise CodexFailure('policy_refused')
    return value


class GateNative(BaseNative):
    def prepare_research(self, session, body):
        selected = mode()
        if os.environ.get('APP_ENV') != 'operator-service' or (body.scope or {}).get('page', {}).get('kind') != 'general':
            raise CodexFailure('policy_refused')
        bridge = self.research
        if selected == 'restart':
            return super().prepare_research(session, body)
        bridge.calls_disabled = True
        if bridge.grant is None:
            return super().prepare_research(session, body)  # First/cold turn, excluded from warm comparison.
        with bridge.lock:
            bridge.session, bridge.request, bridge.scope = session, str(body.requestId), body.scope
            bridge.turn = None
            bridge.ready = threading.Event()

    def release_research(self):
        if mode() == 'retain' and self.research is not None:
            with self.research.lock:
                self.research.turn = None
                self.research.ready.set()
            return
        return super().release_research()


def install():
    mode()  # Only the separate benchmark Docker stage installs this import + marker.
    runner_app.Native = GateNative
