"""Development-only C1 factory. Normal runner entrypoints never import this module.

Use the two benchmark images built from the same runner base, with an immutable
root-owned mode marker (restart or retain). `retain` disables every MCP call; it
only compares model text latency with the persistent bootstrap/catalogue. It
cannot authorize an API operation. The restart variant retains the previous
per-turn-reset adapter as the comparison. C2 uses the normal persistent factory
with actual calls. No shell/security policy is relaxed.
"""
import os
from pathlib import Path
import stat

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
    def create_research(self):
        if mode() == 'restart':
            from runner.research_bridge import ResearchBridge
            return ResearchBridge(self)
        return super().create_research()

    def prepare_research(self, session, body):
        selected = mode()
        if os.environ.get('APP_ENV') != 'operator-service' or (body.scope or {}).get('page', {}).get('kind') != 'general':
            raise CodexFailure('policy_refused')
        bridge = self.research
        if selected == 'restart':
            bridge.prepare(session, str(body.requestId), body.scope)
            self.configure_research()
            self.reset_thread_tools()
            return
        bridge.calls_disabled = True
        return super().prepare_research(session, body)


def install():
    mode()  # Only the separate benchmark Docker stage installs this import + marker.
    runner_app.Native = GateNative
