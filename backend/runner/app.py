"""Private per-account Codex app-server adapter. No DB access or Docker socket."""
import hmac
import hashlib
import fcntl
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from copy import deepcopy
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.codex_policy import MODEL, CodexFailure, ERRORS, error_reason
from .auth import PersonalAuth, read_private, write_private
from .projection import PROJECTION_VERSION
from .binding import canonical_uuid, require_binding
from .access import AccessAuth, SupplySocket
from .service_access import ServiceAccessAuth
from .broker_client import BrokerClient, BrokerPull
from . import dot_codex
from . import native_policy as policy
from . import stream as streams
from .native_policy import Decision
from app.page_scope import Scope, POLICIES, tool_allowed, turn_prompt, shell_allowed


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    text: str = Field(min_length=1, max_length=50000)
    tools: list[dict]
    toolToken: str = Field(min_length=32, max_length=100)
    context: dict | None = None
    scope: dict | None = None
    expectedThreadId: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value):
        return Scope.model_validate(value).model_dump(mode="json", exclude_none=True) if value is not None else None


class Interrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID


class IdleCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    idleSeconds: int = Field(strict=True, gt=0, le=999999999)


class Drain(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stopId: str
    ownerId: str
    stateId: str
    podUid: str
    generation: int = Field(gt=0)

    @field_validator('stopId', 'ownerId', 'stateId', 'podUid')
    @classmethod
    def identity(cls, value):
        canonical_uuid(value)
        return value


def bind_owner(root, owner):
    if root.is_symlink():
        raise RuntimeError("Runner state must be a private volume")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    marker = root / "account-id"
    if marker.exists():
        if marker.is_symlink() or marker.read_text().strip() != owner:
            raise RuntimeError("This state volume belongs to a different account")
        return
    if any(path.name != "lost+found" for path in root.iterdir()):
        raise RuntimeError("Unbound populated state must not be adopted")
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(owner)
        file.flush()
        os.fsync(file.fileno())


@contextmanager
def volume_lease(root):
    with (root / "runner.lock").open("a+") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("The account volume is already being served") from None
        try:
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)


def native_command(binary, home, codex_home, work, *, file_auth, external, fixture):
    """The app-server command line and environment the runner starts (also used by the #68 VM checks)."""
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": str(home), "CODEX_HOME": str(codex_home)}
    command = [binary, "app-server", "--listen", "stdio://",
                "-c", 'cli_auth_credentials_store="file"' if file_auth else 'cli_auth_credentials_store="ephemeral"',
                "-c", 'model="gpt-6.1-sol"', "-c", "features.shell_tool=true",
                "-c", 'model_provider="openai"',
                "-c", "features.multi_agent=false", "-c", "features.multi_agent_v2=false",
                "-c", "features.unbounded_connection_retries=false",
                "-c", "features.apps=false", "-c", "features.plugins=false", "-c", "features.remote_plugin=false",
                "-c", "features.hooks=false", "-c", "features.code_mode_host=true" if not fixture else "features.code_mode_host=false",
                "-c", "features.daemon_auto_start=false",
                "-c", "features.browser_use=false", "-c", "features.computer_use=false",
                "-c", "features.image_generation=false", "-c", 'web_search="disabled"',
                "-c", 'forced_login_method="chatgpt"']
    if external:
        # Same authenticated Codex endpoint, named provider solely to disable retries.
        # The pinned built-in provider cannot be overridden. No API key route.
        for setting in ('model_provider="access_only_openai"',
                        'model_providers.access_only_openai.name="OpenAI"',
                        'model_providers.access_only_openai.base_url="https://chatgpt.com/backend-api/codex"',
                        'model_providers.access_only_openai.wire_api="responses"',
                        'model_providers.access_only_openai.requires_openai_auth=true',
                        'model_providers.access_only_openai.supports_websockets=false',
                        'model_providers.access_only_openai.request_max_retries=0',
                        'model_providers.access_only_openai.stream_max_retries=0',
                        'analytics.enabled=false', 'otel.exporter="none"',
                        'otel.trace_exporter="none"', 'otel.metrics_exporter="none"'):
            command += ['-c', setting]
    # Unified exec (and with it write_stdin) is pinned off by the managed requirements only:
    # 0.160.1 ignores a user `features.unified_exec=false` (#68).
    # The workspace's project trust, as one inline table (#68 G5 (a′)): Codex splits a `-c` key on every `.` and
    # does not read quotes, so the earlier `projects."<work>".trust_level=...` set a key with literal quotes and
    # never applied. With the trust unset, 0.160.1's thread/start persisted `trusted` into CODEX_HOME/config.toml.
    command += ['-c', 'projects={%s={trust_level="untrusted"}}' % toml_basic_string(str(work))]
    return command, environment


def toml_basic_string(text):
    """`text` as a TOML 1.0 basic string: `"` and `\\` escaped, control characters as \\uXXXX. A lone surrogate
    (a path that is not valid UTF-8) cannot be encoded and is refused rather than passed on: Codex would read an
    unparsable `-c` value as a plain string and the trust pin would silently not apply."""
    out = []
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:
            raise CodexFailure("policy_refused")
        if char in '"\\':
            out.append('\\' + char)
        elif code < 0x20 or code == 0x7F:
            out.append('\\u%04X' % code)
        else:
            out.append(char)
    return '"' + ''.join(out) + '"'


# #68 P-A: approval records. They describe what the runner's handler did with each approval request; they never
# decide anything, and they hold no command, cwd, path, reason text, argument or token (design P-A r3).
APPROVAL_KINDS = {"item/commandExecution/requestApproval": "command", "item/fileChange/requestApproval": "fileChange"}
APPROVAL_STATE_LOCK = threading.Lock()  # guards the lazy creation: the reader thread and the turn thread both record
# #90: the native notifications the stream buffer reads (backend/runner/stream.py).
STREAM_METHODS = {"item/started", "item/completed", "item/agentMessage/delta", "item/commandExecution/outputDelta"}


def approval_hash(value):
    """The first 12 hex of sha256 over the UTF-8 text of an id, or None when the id is not a string. The same function
    hashes rollout call_ids for the VM judge."""
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:12] if isinstance(value, str) else None


def approval_rpc_hash(identity):
    """A JSON-RPC id may be an integer or a string: its canonical JSON text is hashed (null only for a missing id)."""
    if identity is None or isinstance(identity, bool) or not isinstance(identity, (int, str)):
        return None
    return approval_hash(json.dumps(identity))


def approval_utc(stamp):
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(stamp)) + '.%03dZ' % int(stamp % 1 * 1000)


def approval_refused(message):
    """The sorted names of policy.COMMAND_REFUSED present and non-null in a command request; [] otherwise. Names only."""
    params = message.get('params') if isinstance(message, dict) else None
    if APPROVAL_KINDS.get(message.get('method')) != 'command' or not isinstance(params, dict):
        return []
    return sorted(name for name in policy.COMMAND_REFUSED if params.get(name) is not None)


class Native:
    PROJECTION_VERSION = PROJECTION_VERSION
    external = False
    def __init__(self, root, model, binary, callback_url, api_key_file=None, auth=None, binding_check=None):
        if model != MODEL or api_key_file:
            raise CodexFailure("policy_refused")
        self.auth, self.blocked_reason, self.real_verified = auth, None, False
        self.external = bool(getattr(auth, 'external', False))
        self.binding_check = binding_check
        self.binding_failed = False
        self.check_binding()
        self.fixture = binary == "/usr/local/bin/codex-fixture"
        if self.fixture and os.getenv("APP_ENV") != "test":
            raise CodexFailure("policy_refused")
        if auth and not self.fixture:
            expected = ("operator-service", "operator-access-service") if getattr(auth, "service", False) else ("personal-test", "personal-private")
            if (os.getenv("APP_ENV"), os.getenv("CODEX_EXECUTION_SCOPE")) != expected:
                raise CodexFailure("policy_refused")
        if not self.fixture:
            from .install_codex import pinned
            if binary != "/usr/local/bin/codex":
                raise CodexFailure("policy_refused")
            try:
                pin = pinned()
            except RuntimeError:
                policy.refuse("binary.platform")
            for path, digest, check in ((binary, pin["codex_sha256"], "binary.codex"),
                                        ("/usr/local/bin/codex-code-mode-host", pin["host_sha256"], "binary.code_mode_host")):
                try:
                    with open(path, "rb") as executable:
                        matches = hashlib.file_digest(executable, "sha256").hexdigest() == digest and os.access(path, os.X_OK)
                except OSError:
                    matches = False
                policy.require(matches, check)
            # The image's Codex policy files, pinned in code next to the binary digests (#68).
            policy.check_files(str(root))
        self.root, self.model, self.callback_url = root, model, callback_url
        self.lock, self.messages, self.next_id = threading.Lock(), queue.Queue(), 1
        self.state_lock = threading.RLock()
        self.completed = {}
        self.active_session = None
        self.idle_since = time.monotonic()
        self.mapping_file = root / "broker.json"
        self.mapping = json.loads(self.mapping_file.read_text()) if self.mapping_file.exists() else {}
        if auth and any(entry.get("projectionVersion") != self.PROJECTION_VERSION for entry in self.mapping.values()):
            raise CodexFailure("policy_refused")  # Legacy unsafe projections need private reconciliation, never automatic trust.
        self.closing, self.closed = False, False
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # #68: the sandboxed native shell writes the workspace, its cwd. Codex must never read
        # model-written policy: a project `.codex` stays an untrusted (disabled) layer, checked
        # through config/read, and CODEX_HOME config/rules or any `.codex` other than the runner's own recorded
        # empty placeholder refuse (check_local, dot_codex).
        home, codex_home, work = root / "home", root / "codex", root / "workspace"
        for path in (home, codex_home, work):
            path.mkdir(exist_ok=True, mode=0o700)
        self.codex_home, self.work = codex_home, work
        # #68 G2 (design g2 r2): the runner-owned empty `.codex`, before any Codex process or credential.
        dot_codex.ensure_placeholder(root, work)
        self.check_local()
        if not auth and (codex_home / "auth.json").exists():
            raise CodexFailure("policy_refused")
        if auth:
            auth.attach(codex_home)
            try:
                for session, entry in self.mapping.items():
                    self.verify_projection(session, entry)
            except Exception:
                auth.close(clean=False)
                raise CodexFailure("policy_refused") from None
        if (root / "model-block.json").exists() or (auth and auth.state().get("modelBlocked")):
            self.blocked_reason = "model_unavailable"
        command, environment = native_command(binary, home, codex_home, work, file_auth=bool(auth) and not self.external,
                                              external=self.external, fixture=self.fixture)
        self.command, self.environment = command, environment
        self.thread_policy = {"approvalPolicy": "untrusted", "approvalsReviewer": "user", "permissions": policy.PROFILE}
        self.thread_web_modes = {}
        try:
            self.launch_process()
        except Exception:
            if hasattr(self, "process"):
                self.close()
            raise
        # Interrupted dispatches are reconciled from originals, never replayed.
        for entry in self.mapping.values():
            if entry.get("state") == "running":
                entry["state"] = "failed"
        self.save()
        # #90: the in-memory stream buffer. Nothing in it is durable or decides a turn.
        self.stream = streams.Hub(auth, queue_size=lambda: self.messages.qsize(), work=self.work)
        self.interrupt_request = None

    def launch_process(self):
        self.process = subprocess.Popen(self.command,
                    env=self.environment, cwd=self.work, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.reader_thread = threading.Thread(target=self.reader, daemon=True)
        self.reader_thread.start()
        try:
            self.rpc("initialize", {"clientInfo": {"name": "anxious_research", "version": "1"}, "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized"})
            self.check_policy()  # Before any credential reaches this process.
            if self.external:
                self.login_access()
            if self.auth:
                account = self.rpc("account/read", {"refreshToken": False}).get("account")
                if not account or account.get("type") != "chatgpt":
                    raise CodexFailure("auth_revoked")
                if account.get("email"):
                    self.auth.secrets.add(account["email"])
        except CodexFailure:
            raise
        except Exception:
            raise RuntimeError("Native initialization or authentication is unavailable") from None

    def check_local(self, phase="start"):
        # Model-writable places Codex could read as policy are never present (#68). The workspace `.codex` is
        # absent or exactly the runner's recorded empty placeholder (G2, design g2 r2); any other refuses.
        policy.require(not os.path.lexists(self.codex_home / "config.toml"), "local.codex_home_config")
        policy.require(not os.path.lexists(self.codex_home / "rules"), "local.codex_home_rules")
        dot_codex.check_placeholder(self.root, self.work, phase)
        policy.require(not any(os.path.lexists(path / ".codex") for path in self.work.parents), "local.parent_dot_codex")

    def check_policy(self, phase="launch"):
        """What this app-server process reports applied (#68). Every launch and every turn."""
        self.check_local(phase)
        for method, params, check in (("configRequirements/read", {}, policy.check_requirements),
                                      ("config/read", {"includeLayers": True, "cwd": str(self.work)},
                                       lambda result: policy.check_config(result, str(self.root)))):
            try:
                result = self.rpc(method, params)
            except CodexFailure:
                policy.refuse("rpc." + method)
            check(result)

    def reply_server(self, message, turn):
        if message.get("method") == "item/tool/call":
            self.reply_tool(message, turn)
        else:
            self.reply_approval(message, turn)

    def reply_approval(self, message, turn):
        """Approve one pending command or file change after the shell permission check, or decline.

        The answer goes to this request's id only, and an item is decided once: a duplicate,
        a late request for a finished turn or anything not in the allow lists is declined.
        """
        seq = self.approval_dispatched(message, turn)  # #68 P-A record; never raises, never decides
        if turn is not None:
            self.stream_note(message, "requested")  # #90 item.status approval; never raises, never decides
        decision, reason = Decision.DECLINE, 'error'
        try:
            params = message.get("params")
            if message.get("method") == "item/commandExecution/requestApproval":
                eligible = policy.command_eligible(params, turn, str(self.work))
                arguments = {"command": params["command"], "cwd": params["cwd"]} if eligible else None
            else:
                eligible = policy.file_change_eligible(params, turn, str(self.work))
                arguments = {}
            if isinstance(params, dict) and turn is not None and isinstance(params.get("itemId"), str):
                turn.setdefault("approvalItems", set()).add(params["itemId"])
            # The same evaluation order and short-circuit as before; the reason only names the branch taken.
            reason = 'no_turn' if turn is None else 'not_eligible'
            if eligible:
                reason = 'completed_turn'
                if (turn["thread"], turn["turnId"]) not in self.completed:
                    reason = 'authorize_refused'
                    if self.authorize_shell(turn, arguments):
                        decision, reason = Decision.ACCEPT, 'accepted'
        except Exception:
            decision, reason = Decision.DECLINE, 'error'
        self.approval_decided(seq, message, decision, reason)
        answer, sent, before = policy.answer(message["id"], decision), False, time.monotonic_ns()
        try:
            self.send(answer)
            sent = True
        finally:
            self.approval_sent(seq, sent, before, time.monotonic_ns())
            if turn is not None:
                self.stream_note(message, "decided")

    # --- #68 P-A approval records (logging only; every failure here is swallowed and shows as an incomplete record) --

    def approval_state(self):
        state = getattr(self, '_approval_records', None)
        if state is None:
            with APPROVAL_STATE_LOCK:
                state = getattr(self, '_approval_records', None)
                if state is None:
                    state = self._approval_records = {'lock': threading.Lock(), 'seq': 0, 'arrived': {},
                                                      'pending': set(), 'window': []}
        return state

    @staticmethod
    def approval_log(prefix, record):
        try:
            logging.getLogger('uvicorn.error').info('%s %s', prefix, json.dumps(record, sort_keys=True))
        except Exception:
            pass

    def approval_arrived(self, message, pending=False):
        """Called by the stdout reader thread when an approval request line is read from Codex, before it enters the
        queue (the design's arrival time; review round 1); with pending=True (the receive loop, when the request is
        queued in pendingTools before the turn id is known) it only marks the request, never a new stamp."""
        try:
            state, key = self.approval_state(), json.dumps(message.get('id'), sort_keys=True)
            with state['lock']:
                if pending:
                    state['pending'].add(key)
                else:
                    state['arrived'][key] = (approval_utc(time.time()), time.monotonic_ns())
        except Exception:
            pass

    def approval_dispatched(self, message, turn):
        try:
            state, monotonic, stamp = self.approval_state(), time.monotonic_ns(), time.time()
            key = json.dumps(message.get('id'), sort_keys=True)
            with state['lock']:
                state['seq'] += 1
                seq = state['seq']
                arrived = state['arrived'].pop(key, (None, None))
                pending = key in state['pending']
                state['pending'].discard(key)
                state['window'].append(('dispatched', seq, turn is not None))
            params = message.get('params') if isinstance(message.get('params'), dict) else {}
            self.approval_log('runner_approval', {
                'v': 1, 'seq': seq, 'stage': 'dispatched', 'kind': APPROVAL_KINDS.get(message.get('method'), 'other'),
                'rpc': approval_rpc_hash(message.get('id')), 'item': approval_hash(params.get('itemId')),
                'thread': approval_hash(params.get('threadId')), 'turn': approval_hash(params.get('turnId')),
                'inTurn': turn is not None, 'pending': pending, 'arrived': arrived[0], 'arrivedM': arrived[1],
                'm': monotonic, 't': approval_utc(stamp)})
            return seq
        except Exception:
            return None

    def approval_decided(self, seq, message, decision, reason):
        try:
            monotonic, stamp = time.monotonic_ns(), time.time()
            with self.approval_state()['lock']:
                self.approval_state()['window'].append(('decided', seq, None))
            self.approval_log('runner_approval', {
                'v': 1, 'seq': seq, 'stage': 'decided', 'decision': decision.value, 'reason': reason,
                'refused': approval_refused(message), 'm': monotonic, 't': approval_utc(stamp)})
        except Exception:
            pass

    def approval_sent(self, seq, ok, before, after):
        try:
            with self.approval_state()['lock']:
                self.approval_state()['window'].append(('sent', seq, None))
            self.approval_log('runner_approval', {'v': 1, 'seq': seq, 'stage': 'sent', 'ok': bool(ok), 'm0': before,
                                                  'm1': after, 't': approval_utc(time.time())})
        except Exception:
            pass

    def turn_released(self, entry):
        """#68 P-A (review round 2): the turn's approval summary (snapshot, reset, line) is taken while the protocol
        mutex is still held, so the next turn's records never fall into this turn's summary; then the mutex is
        released, always (approval_summary never raises)."""
        try:
            self.approval_summary(entry.get("thread"), entry.get("turn_id"))
        finally:
            self.lock.release()

    def approval_summary(self, thread, turn_id):
        """One line per turn, in the turn's final block: the counts since the previous summary, then reset."""
        try:
            state = self.approval_state()
            with state['lock']:
                window, arrived_only = state['window'], len(state['arrived'])
                state['window'], state['arrived'], state['pending'] = [], {}, set()
            seqs = [seq for stage, seq, _ in window if stage == 'dispatched' and seq is not None]
            self.approval_log('runner_approval_summary', {
                'v': 1, 'thread': approval_hash(thread), 'turn': approval_hash(turn_id),
                'dispatched': sum(stage == 'dispatched' for stage, _, _ in window),
                'decided': sum(stage == 'decided' for stage, _, _ in window),
                'sent': sum(stage == 'sent' for stage, _, _ in window), 'arrivedOnly': arrived_only,
                'seqFirst': min(seqs) if seqs else None, 'seqLast': max(seqs) if seqs else None,
                'outside': sum(stage == 'dispatched' and not in_turn for stage, _, in_turn in window),
                't': approval_utc(time.time())})
        except Exception:
            pass

    def authorize_shell(self, turn, arguments):
        try:
            if (self.closing or self.blocked_reason or not turn.get('turnId')
                    or self.active_session != turn['session'] or not shell_allowed(turn.get('scope'))
                    or self.mapping[turn['session']].get('state') != 'running'):
                return False
            self.check_binding()
            if self.auth:
                self.auth.permitted_tool(arguments)
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                reply = client.post(self.callback_url, headers={'Authorization': 'Bearer ' + turn['token']},
                    json={'sessionId': turn['session'], 'requestId': turn['request'],
                          'name': 'runner_shell_authorize', 'arguments': {}})
            return reply.is_success
        except Exception:
            return False

    def reset_thread_tools(self):
        # Pinned 0.160.1 retains loaded tool registries on thread/resume, even
        # after unsubscribe. Recreate only the native process under the RPC
        # mutex; original thread files and signed broker projections survive.
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.reader_thread.join(timeout=10)
        if self.reader_thread.is_alive():
            raise CodexFailure("policy_refused")
        self.process.stdin.close()
        self.process.stdout.close()
        self.messages = queue.Queue()
        self.completed.clear()
        self.thread_web_modes.clear()
        self.launch_process()

    def login_access(self):
        self.auth.check(budget=False)
        result = self.rpc('account/login/start', {'type': 'chatgptAuthTokens', **self.auth.snapshot})
        if result.get('type') != 'chatgptAuthTokens':
            raise CodexFailure('auth_revoked')

    def resupply(self, envelope):
        # Operator-only. Never resume/replay a failed request while replacing auth.
        with self.state_lock:
            self.check_binding()
            if self.active_session is not None or not self.lock.acquire(blocking=False):
                raise CodexFailure('unavailable')
            try:
                if self.process.poll() is not None or self.closing:
                    raise CodexFailure('unavailable')
                self.auth.install(envelope)
                self.blocked_reason = None
                self.login_access()
                self.real_verified = False
            except CodexFailure as failure:
                self.blocked_reason = failure.reason
                self.auth.block(failure.reason)
                raise
            finally:
                self.lock.release()

    def availability(self):
        try:
            self.check_binding()
        except CodexFailure as failure:
            return failure.reason
        if self.blocked_reason:
            return self.blocked_reason
        if self.process.poll() is not None:
            return "unavailable"
        if not self.auth and not self.fixture:
            return "not_configured"
        if self.auth:
            try:
                self.auth.check()
            except CodexFailure as failure:
                return failure.reason
        return None

    def check_binding(self):
        if getattr(self, "binding_failed", False):
            raise CodexFailure("policy_refused")
        check = getattr(self, "binding_check", None)
        if check:
            try:
                check()
            except CodexFailure:
                self.blocked_reason = "policy_refused"
                self.binding_failed = True
                raise

    def verify_projection(self, session, entry):
        if self.auth and (entry.get("record", {}).get("turns") or "modelMismatchBaseline" in entry.get("record", {})):
            expected = self.auth.projection_mac(session, entry.get("thread"), entry["record"])
            actual = entry.get("projectionMac")
            if entry.get("projectionVersion") != self.PROJECTION_VERSION or not isinstance(actual, str) or not re.fullmatch(r"[0-9a-f]{64}", actual) or not hmac.compare_digest(expected, actual):
                raise CodexFailure("policy_refused")

    def store_record(self, session, entry, response, verified_turn_id=None, display_only=None):
        # Polling verifies under this same lock. Publish record and MAC as one
        # state change, including when a worker projects its completed turn.
        with self.state_lock:
            self._store_record(session, entry, response, verified_turn_id, display_only)

    DISPLAY_ONLY_TURN_LIMIT = 512 * 1024
    DISPLAY_ONLY_LIMIT = 2 * 1024 * 1024

    def merge_display_only(self, record, previous_record, display_only, mismatches):
        """#90 (design r1 §4, r2 Q3, r3): the partial answer of a user-stopped turn that Codex did not record. Signed
        with the record (projectionMac), carried over every later projection (thread/read, next turn, restart), and
        dropped when Codex records the same item id (native wins, D4) or the turn is model-quarantined. Display only:
        it is not in the Codex thread, so it is never part of a resumed model context."""
        kept = {turn_id: list(items) for turn_id, items in (previous_record.get("displayOnly") or {}).items()}
        dropped = list(previous_record.get("displayOnlyDropped") or [])
        if display_only and display_only[1]:
            turn_id, items = display_only
            accepted, size = [], 0
            for item in items:
                text = item["text"]
                while len(text.encode("utf-8", "surrogatepass")) + size > self.DISPLAY_ONLY_TURN_LIMIT and text:
                    text = text[: len(text) // 2]
                size += len(text.encode("utf-8", "surrogatepass"))
                accepted.append({"id": item["id"], "type": "agentMessage", "text": text, "partial": True, "displayOnly": True})
            kept[turn_id] = accepted
        turns = {turn.get("id"): turn for turn in record["turns"]}
        result = {}
        for turn_id, items in kept.items():
            if turn_id in mismatches:
                continue
            native = {item.get("id") for item in (turns[turn_id].get("items", []) if turn_id in turns else [])}
            remaining = [item for item in items if item.get("id") not in native]
            if remaining:
                result[turn_id] = remaining
        order = {turn_id: index for index, turn_id in enumerate(turns)}

        def size_of(items):
            return sum(len(item.get("text", "").encode("utf-8", "surrogatepass")) for item in items)
        while result and sum(size_of(items) for items in result.values()) > self.DISPLAY_ONLY_LIMIT:
            oldest = min(result, key=lambda turn_id: order.get(turn_id, -1))
            del result[oldest]
            dropped.append(oldest)
        if self.auth:
            result = self.auth.sanitize(result)
        record.pop("displayOnly", None)
        record.pop("displayOnlyDropped", None)
        if result:
            record["displayOnly"] = result
        if dropped:
            record["displayOnlyDropped"] = dropped[-1000:]

    def _store_record(self, session, entry, response, verified_turn_id, display_only=None):
        record = self.record(response)
        mismatches = entry.setdefault("modelMismatchTurns", [])
        for previous in entry.get("record", {}).get("turns", []):
            if previous.get("modelMismatch") is True and previous.get("id") not in mismatches:
                mismatches.append(previous["id"])
        previous_record = entry.get("record", {})
        baseline = previous_record.get("modelMismatchBaseline")
        if baseline is None and entry.get("error") == "codex_model_unavailable":
            # A model event can abort turn/start before its reply supplies an
            # identity. Freeze the previously projected turns once; quarantine
            # every new original turn, including ones appearing on later reads
            # or after restart. An early/stale event never authorizes a tool or
            # selects an old good turn for exclusion.
            baseline = [previous.get("id") for previous in previous_record.get("turns", [])]
        if baseline is not None:
            # Quarantine outlives the request's error and operator release.
            # Only a completed, correlated new turn can join the signed allow
            # list; a history read must never bless an unidentified old result.
            verified = list(previous_record.get("modelVerifiedTurns", []))
            if verified_turn_id and verified_turn_id not in mismatches and verified_turn_id not in verified:
                verified.append(verified_turn_id)
            record["modelMismatchBaseline"] = baseline  # Private metadata is covered by the projection MAC.
            record["modelVerifiedTurns"] = verified
            for current in record["turns"]:
                if current.get("id") not in baseline and current.get("id") not in verified and current.get("id") not in mismatches:
                    mismatches.append(current.get("id"))
        for turn in record["turns"]:
            if turn.get("id") in mismatches:
                turn["modelMismatch"] = True
                turn["items"] = [{**item, "modelMismatch": True} for item in turn.get("items", []) if item.get("type") != "agentMessage"]
        self.merge_display_only(record, previous_record, display_only, mismatches)
        self.publish_record(session, entry, record)

    def publish_record(self, session, entry, record):
        entry["record"] = record
        entry["projectionVersion"] = self.PROJECTION_VERSION
        if self.auth:
            entry["projectionMac"] = self.auth.projection_mac(session, entry.get("thread"), record)

    def quarantine_history(self, session, entry):
        # Capture the already safe boundary before any fallible history RPC.
        # A later operator release cannot erase it if that RPC never succeeds.
        with self.state_lock:
            record = deepcopy(entry.get("record", {"turns": []}))
            if self.auth:
                record = self.auth.sanitize(record)
            record.setdefault("modelMismatchBaseline", [turn.get("id") for turn in record.get("turns", [])])
            record.setdefault("modelVerifiedTurns", [])
            self.publish_record(session, entry, record)

    def record(self, response):
        if self.auth:
            self.auth.refresh_projection_secrets()
        # Retired literal fingerprints persist outside the home and remain
        # available when partial or edited original turns are reprojected.
        turns = deepcopy(response.get("turns", []))
        record = {"turns": turns}
        # Turn errors retain safe codes, never provider message/additionalDetails.
        for turn in record["turns"]:
            if turn.get("error"):
                turn["error"] = {"code": ERRORS.get(error_reason(turn["error"]), "codex_failed")}
        return self.auth.sanitize(record) if self.auth else record

    def block_model(self):
        with self.state_lock:
            self.blocked_reason = "model_unavailable"
            self.real_verified = False
            write_private(self.root / "model-block.json", {"reason": "model_unavailable"})
            if self.auth:
                self.auth.block_model()

    def ensure_model(self, response, required=False):
        if (required and response.get("model") != MODEL) or ("model" in response and response["model"] != MODEL):
            self.block_model()
            raise CodexFailure("model_unavailable")

    def reader(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                if isinstance(message, dict) and message.get("method") in APPROVAL_KINDS and "id" in message:
                    self.approval_arrived(message)  # #68 P-A: stamped as read from Codex's stdout, before the queue
                self.messages.put(message)
        except Exception:
            pass
        self.messages.put({"fatal": True})

    def write_lock(self):
        """#90: an interrupt is sent from a request thread while the turn thread writes; one line at a time."""
        lock = getattr(self, '_write_lock', None)
        if lock is None:
            with APPROVAL_STATE_LOCK:
                lock = getattr(self, '_write_lock', None)
                if lock is None:
                    lock = self._write_lock = threading.RLock()
        return lock

    def send(self, body):
        with self.write_lock():
            self.process.stdin.write(json.dumps(body, ensure_ascii=True) + "\n")
            self.process.stdin.flush()

    def stream_hub(self):
        """#90: the turn's stream buffer, or None (tests that build Native without __init__)."""
        return getattr(self, 'stream', None)

    def stream_note(self, message, approval=None):
        hub = self.stream_hub()
        if hub is not None:
            hub.note(message, approval)  # never raises; a failure stops the stream, never the turn

    def receive(self, turn=None):
        timeout = 120
        if self.external:
            self.auth.check(budget=False)
            import time
            timeout = max(.01, min(timeout, self.auth.state()['deadline']-time.time()))
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.external:
                self.auth.check(budget=False)
            raise CodexFailure('unavailable') from None
        if self.external:
            self.auth.check(budget=False)
        if message.get("fatal"):
            raise RuntimeError("Codex process stopped")
        if message.get("method") == "model/rerouted":
            self.block_model()
            hub = self.stream_hub()
            if hub is not None:
                hub.discard("model_unavailable")  # the record drops this turn's agentMessage; the stream does too
            params = message.get("params", {})
            entry = self.mapping.get(self.active_session)
            if turn and entry and params.get("threadId") == turn["thread"] and not turn.get("turnId"):
                entry["turn_id"] = params.get("turnId")
            with self.write_lock():
                self.next_id += 1
                self.send({"id": self.next_id - 1, "method": "turn/interrupt", "params": {"threadId": params.get("threadId"), "turnId": params.get("turnId")}})
            raise CodexFailure("model_unavailable")
        if message.get("method") in STREAM_METHODS and turn:
            self.stream_note(message)
        if message.get("method") == "account/updated" and self.auth:
            if message.get("params", {}).get("authMode") not in ({'chatgptAuthTokens'} if self.external else {'chatgpt'}):
                self.blocked_reason = "auth_revoked"
                raise CodexFailure("auth_revoked")
        if message.get("method") in {"turn/started", "turn/updated"}:
            self.ensure_model(message.get("params", {}).get("turn", {}))
        if message.get("method") == "item/started" and turn:
            self.file_change_started(message.get("params"), turn)
        if message.get("method") == "turn/completed":
            params = message.get("params", {})
            self.ensure_model(params.get("turn", {}))
            self.completed[(params.get("threadId"), params.get("turn", {}).get("id"))] = params.get("turn", {})
        if "method" in message and "id" in message:
            method, params = message["method"], message.get("params", {})
            if method == 'account/chatgptAuthTokens/refresh' and self.external:
                refresher = getattr(self.auth, 'refresher', None)
                if refresher is not None:
                    # #60: renew from codex-broker. install() keeps the ledger's owner/account/expiry
                    # and retired-token checks; the app-server receives only the access token.
                    try:
                        rejected = (self.auth.snapshot or {}).get('accessToken')
                        envelope = refresher()
                        if envelope['accessToken'] == rejected:
                            raise CodexFailure('auth_revoked')  # The 401 token again: never loop on it.
                        self.auth.install(envelope)
                        snapshot = self.auth.snapshot
                        self.send({'id': message['id'], 'result': {'accessToken': snapshot['accessToken'],
                                   'chatgptAccountId': snapshot['chatgptAccountId'],
                                   'chatgptPlanType': snapshot.get('chatgptPlanType')}})
                        return message
                    except Exception:
                        pass  # Fall through: the same fixed refusal as without a broker.
                self.send({'id': message['id'], 'error': {'code': -32001, 'message': 'Operator access resupply required'}})
                self.auth.block('auth_revoked')
                self.blocked_reason = 'auth_revoked'
                raise CodexFailure('auth_revoked')
            if method in {"item/tool/call", "item/commandExecution/requestApproval", "item/fileChange/requestApproval"} and turn:
                if not turn.get("turnId") and isinstance(params, dict) and params.get("threadId") == turn["thread"]:
                    pending = turn.setdefault("pendingTools", [])
                    if len(pending) < 32:
                        pending.append(message)  # Wait for correlated turn/start response; never borrow a token early.
                        if method in APPROVAL_KINDS:
                            self.approval_arrived(message, pending=True)
                    else:
                        self.reply_server(message, None)
                else:
                    self.reply_server(message, turn)
            elif method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                self.reply_approval(message, None)  # Outside a turn: declined.
            else:
                # No hidden shell/file permission grants or host credential refresh.
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Interactive capability is unavailable"}})
        return message

    @staticmethod
    def file_change_started(params, turn):
        """#68 X2: keep a fileChange item's changes for its approval (native_policy.file_change_eligible). In 0.160.1
        the item/started notification of a patch is sent before its approval request, with the same itemId and the
        same verified action. Kept only for this turn's thread. A second item/started for the same id marks the item
        as a conflict, so its approval is declined. Beyond policy.FILE_CHANGE_ITEMS items nothing more is kept (declined).
        Never raises."""
        try:
            item = params.get("item") if isinstance(params, dict) else None
            if not isinstance(item, dict) or item.get("type") != "fileChange" or params.get("threadId") != turn["thread"]:
                return
            identity = item.get("id")
            if not isinstance(identity, str) or not identity:
                return
            kept = turn.setdefault("fileChanges", {})
            if identity in kept:
                kept[identity]["conflict"] = True
                return
            if len(kept) >= policy.FILE_CHANGE_ITEMS:
                return
            kept[identity] = {"threadId": params.get("threadId"), "turnId": params.get("turnId"),
                              "changes": json.loads(json.dumps(item.get("changes")))}
        except Exception:
            pass

    def reply_tool(self, message, turn):
        started = time.monotonic()
        try:
            return self._reply_tool(message, turn)
        finally:
            if turn and "timing" in turn:
                turn["timing"]["toolCalls"] += 1
                turn["timing"]["toolMs"] += (time.monotonic() - started) * 1000

    def _reply_tool(self, message, turn):
        params = message.get("params", {})
        failure = None
        valid = (not self.blocked_reason and turn and turn.get("turnId") and params.get("threadId") == turn["thread"]
                 and params.get("turnId") == turn["turnId"] and isinstance(params.get("callId"), str)
                 and params["callId"] and (turn["thread"], turn["turnId"]) not in self.completed)
        if not valid:
            success, result = False, {"error": {"code": "native_turn_mismatch", "message": "Storage tool is outside the active native turn"}}
        else:
            try:
                self.check_binding()
                if self.auth:
                    self.auth.permitted_tool(params["arguments"])
                if not tool_allowed(turn.get("scope"), params.get("tool"), params.get("arguments")):
                    success, result = False, {"error": {"code": "tool_not_allowed", "message": "Tool is unavailable in this request's research mode"}}
                else:
                    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                        reply = client.post(self.callback_url, headers={"Authorization": "Bearer " + turn["token"]},
                                            json={"sessionId": turn["session"], "requestId": turn["request"],
                                                  "name": params["tool"], "arguments": params["arguments"]})
                    success, result = reply.is_success, reply.json()
            except CodexFailure as rejected:
                failure = rejected
                self.blocked_reason = rejected.reason
                success, result = False, {"error": {"code": str(rejected), "message": "Storage tool permission is unavailable"}}
            except Exception:
                success, result = False, {"error": {"code": "not_ready", "message": "Storage tool did not confirm success"}}
        if self.auth:
            result = self.auth.sanitize(result)
        self.send({"id": message["id"], "result": {"success": success, "contentItems": [{"type": "inputText", "text": json.dumps(result, ensure_ascii=True)}]}})
        if failure:
            raise failure

    def rpc(self, method, params, turn=None):
        if self.external:
            self.auth.check(budget=False)
        with self.write_lock():  # #90: id allocation and the line, atomic against an interrupt
            identity, self.next_id = self.next_id, self.next_id + 1
            self.send({"id": identity, "method": method, "params": params})
        while True:
            result = self.receive(turn)
            if result.get("id") == identity and "method" not in result:
                if "error" in result:
                    raise CodexFailure(error_reason(result["error"]))
                return result["result"]

    def save(self):
        self.check_binding()
        write_private(self.mapping_file, self.mapping)

    def accept(self, session, body):
        request = str(body.requestId)
        fingerprint_body = {"text": body.text, "tools": body.tools, "context": body.context}
        if body.scope is not None:
            fingerprint_body["scope"] = body.scope
        if body.expectedThreadId is not None:
            fingerprint_body["expectedThreadId"] = body.expectedThreadId
        fingerprint = hashlib.sha256(json.dumps(fingerprint_body, sort_keys=True).encode()).hexdigest()
        # Admission depends on a reserved turn, not a history RPC holding the
        # protocol mutex. The worker waits for that RPC without resubmission.
        with self.state_lock:
            entry = self.mapping.get(session)
            if body.expectedThreadId is not None and (entry is None or entry.get("thread") != body.expectedThreadId):
                raise CodexFailure("policy_refused")  # Never replace an established conversation with thread/start.
            recorded = entry.get("requests", {}).get(request) if entry else None
            if recorded:
                if not hmac.compare_digest(recorded, fingerprint):
                    raise RuntimeError("Request ID was used for different input")
                return
            if self.closing:
                raise CodexFailure("unavailable")
            reason = self.availability()
            if reason:
                raise CodexFailure(reason)
            if self.active_session is not None:
                raise RuntimeError("Runner is busy")
            self.active_session = session
            self.idle_since = time.monotonic()
            if self.auth:
                try:
                    self.auth.consume(request)
                except Exception:
                    self.active_session = None
                    raise
            requests = dict(entry.get("requests", {})) if entry else {}
            requests[request] = fingerprint
            self.mapping[session] = {"thread": entry.get("thread") if entry else None, "request": request,
                                     "requests": requests, "turn_id": None, "state": "running", "record": entry.get("record", {"turns": []}) if entry else {"turns": []}, "projectionVersion": self.PROJECTION_VERSION,
                                     "modelMismatchTurns": entry.get("modelMismatchTurns", []) if entry else [], "projectionMac": entry.get("projectionMac") if entry else None}
            try:
                self.save()  # Durable reservation before any potentially paid turn.
            except Exception:
                if entry:
                    self.mapping[session] = entry
                else:
                    del self.mapping[session]
                self.active_session = None
                raise RuntimeError("Could not reserve the turn") from None
            try:
                self.worker = threading.Thread(target=self.run_turn, args=(session, body), daemon=True)
                self.worker.start()
            except Exception:
                self.mapping[session]["state"] = "failed"
                try:
                    self.save()
                finally:
                    self.active_session = None
                raise RuntimeError("Could not start the reserved turn") from None

    def interrupt(self, session, request):
        """#90 Stop: only this session's running request. Idempotent; the turn ends through turn/completed."""
        with self.state_lock:
            entry = self.mapping.get(session)
            if (self.closing or self.active_session != session or entry is None or entry.get("request") != request
                    or entry.get("state") != "running"):
                return "not_running"
            self.interrupt_request = (session, request)
        self.send_interrupt(session, entry)
        return "interrupting"

    def send_interrupt(self, session, entry):
        """turn/interrupt once the turn id is known (a stop before turn/start answered waits for it)."""
        if getattr(self, "interrupt_request", None) != (session, entry.get("request")):
            return
        with self.write_lock():
            if not entry.get("turn_id") or not entry.get("thread") or entry.get("interruptSent"):
                return
            entry["interruptSent"] = True
            self.next_id += 1
            self.send({"id": self.next_id - 1, "method": "turn/interrupt",
                       "params": {"threadId": entry["thread"], "turnId": entry["turn_id"]}})

    def run_turn(self, session, body):
        started_at = time.monotonic()
        timing = {"toolCalls": 0, "toolMs": 0.0}
        self.lock.acquire()  # Serialize native RPCs after any in-flight history read.
        acquired_at = time.monotonic()
        timing["queueMs"] = (acquired_at - started_at) * 1000
        entry = self.mapping[session]
        hub, stream_turn = self.stream_hub(), None
        if hub is not None:
            stream_turn = hub.begin(session, entry["request"])
        try:
            self.check_binding()
            kind = (body.scope or {}).get("page", {}).get("kind")
            web_mode = "live" if kind in POLICIES and kind not in {"public_document", "public_post"} else "disabled"
            if entry["thread"] in self.thread_web_modes and self.thread_web_modes[entry["thread"]] != web_mode:
                self.reset_thread_tools()
            config = {"web_search": web_mode}
            self.check_policy("turn_pre")  # Again per turn: nothing the last turn wrote may change the policy.
            turn = {"thread": entry["thread"], "turnId": None, "pendingTools": [], "approvalItems": set(),
                    "session": session, "request": entry["request"], "token": body.toolToken, "scope": body.scope, "timing": timing}
            if entry["thread"]:
                result = self.rpc("thread/resume", {"threadId": entry["thread"], "model": self.model,
                                  "cwd": str(self.work), **self.thread_policy, "config": config})
                if result.get("thread", {}).get("id") != entry["thread"]:
                    raise CodexFailure("policy_refused")
            else:
                result = self.rpc("thread/start", {"model": self.model, "cwd": str(self.work),
                                  **self.thread_policy, "config": config, "dynamicTools": body.tools,
                                  "historyMode": "legacy", "ephemeral": False})
            self.ensure_model(result, required=True)
            policy.check_thread(result, self.work)  # Applied to this thread, start and resume alike.
            timing["threadMs"] = (time.monotonic() - acquired_at) * 1000
            entry["thread"] = result["thread"]["id"]
            self.thread_web_modes[entry["thread"]] = web_mode
            self.save()
            turn['thread'] = entry['thread']
            if self.auth:
                self.auth.secrets.add(body.toolToken)
            if hub is not None:
                hub.bind(thread=entry["thread"], turn=stream_turn)
            # Catalog stays thread-persistent; request mode is enforced by both
            # callback boundaries, while page data is only a compact hint.
            content = turn_prompt(body.text, body.scope)
            timing["promptChars"] = len(content)
            model_started_at = time.monotonic()
            started = self.rpc("turn/start", {"threadId": entry["thread"], "model": MODEL, "input": [{"type": "text", "text": content}]}, turn)
            timing["dispatchMs"] = (time.monotonic() - model_started_at) * 1000
            entry["turn_id"] = started["turn"]["id"]
            self.ensure_model(started.get("turn", {}))
            turn_id = started["turn"]["id"]
            if self.auth and any(old.get("id") == turn_id for old in entry["record"].get("turns", [])):
                self.blocked_reason = "policy_refused"
                raise CodexFailure("policy_refused")
            turn["turnId"] = turn_id
            if hub is not None:
                hub.bind(turn_id=turn_id, turn=stream_turn)
                hub.verified(stream_turn)  # the pinned model answered turn/start: structural events may leave
            self.send_interrupt(session, entry)  # #90: a stop that arrived before the turn id
            for message in turn.pop("pendingTools"):
                self.reply_server(message, turn)
            entry["turn_id"] = turn_id
            self.save()
            key = (entry["thread"], turn_id)
            while key not in self.completed:
                self.receive(turn)
            completed = self.completed.pop(key)
            timing["nativeMs"] = (time.monotonic() - model_started_at) * 1000
            status = completed.get("status")
            # #90: a user stop (this request's interrupt) ends the turn normally; any other interruption is a failure.
            interrupted = status == "interrupted" and getattr(self, "interrupt_request", None) == (session, entry["request"])
            entry["state"] = "idle" if (status == "completed" or interrupted) and not self.blocked_reason else "failed"
            entry["turnStatus"] = "interrupted" if interrupted and entry["state"] == "idle" else (
                "completed" if entry["state"] == "idle" else "failed")
            if entry["state"] == "failed":
                entry["error"] = ERRORS.get(error_reason(completed.get("error")), "codex_failed")
                if self.external and error_reason(completed.get('error')) in {'auth_revoked', 'auth_expired', 'provider_rate_limited'}:
                    self.blocked_reason = error_reason(completed.get('error'))
                    self.auth.block(self.blocked_reason)
            elif not self.fixture and status == "completed":
                self.real_verified = True
            display_only = None
            if entry["turnStatus"] == "interrupted" and hub is not None:
                display_only = (turn_id, hub.partial_items(stream_turn))  # sanitized; kept only if Codex did not record it
            history_started_at = time.monotonic()
            self.store_record(session, entry, self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"],
                              verified_turn_id=turn_id if entry["state"] == "idle" and status == "completed" else None,
                              display_only=display_only)
            timing["historyMs"] = (time.monotonic() - history_started_at) * 1000
        except Exception as failure:
            entry["state"] = "failed"
            entry["error"] = str(failure) if isinstance(failure, CodexFailure) else "codex_failed"
            if self.external and isinstance(failure, CodexFailure) and failure.reason in {'auth_revoked', 'auth_expired'}:
                self.auth.block(failure.reason)
                # Stop only this owned turn; never dispatch a retry after auth refusal.
                if entry.get('thread') and entry.get('turn_id'):
                    try:
                        with self.write_lock():
                            self.next_id += 1
                            self.send({'id': self.next_id-1, 'method': 'turn/interrupt',
                                       'params': {'threadId': entry['thread'], 'turnId': entry['turn_id']}})
                    except Exception:
                        pass
            if isinstance(failure, CodexFailure) and failure.reason in {"auth_revoked", "auth_expired", "model_unavailable", "policy_refused"}:
                self.blocked_reason = failure.reason
                if failure.reason == "model_unavailable":
                    self.quarantine_history(session, entry)
            if entry["thread"] and not (self.external and self.blocked_reason in {'auth_revoked', 'auth_expired'}):
                try:
                    self.store_record(session, entry, self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"])
                except Exception:
                    pass
        finally:
            try:
                with self.state_lock:
                    try:
                        self.save()
                    finally:
                        self.active_session = None
                        self.idle_since = time.monotonic()
            finally:
                self.turn_released(entry)  # #68 P-A: the summary under the mutex, then the release
                if hub is not None:
                    # #90: after the record is stored and saved, this turn's last event. It names its own Turn: a turn
                    # admitted after the release is never completed or cleared by it (review stage-1).
                    try:
                        hub.complete(entry["state"],
                                     entry.get("turnStatus", "failed" if entry["state"] == "failed" else "completed"),
                                     entry.get("error") if entry["state"] == "failed" else None, stream_turn)
                    except Exception:
                        pass
                timing["totalMs"] = (time.monotonic() - started_at) * 1000
                logging.getLogger("uvicorn.error").info("runner_turn_timing %s", json.dumps({
                    "requestId": str(body.requestId), "state": entry["state"],
                    **{key: round(value, 3) for key, value in timing.items()},
                }, sort_keys=True))

    def read(self, session):
        self.check_binding()
        with self.state_lock:
            entry = self.mapping.get(session)
            if entry is None:
                return None
            self.verify_projection(session, entry)
            refresh = (self.active_session is None and entry.get("state") != "running" and entry.get("thread")
                       and not (self.external and self.availability() is not None))
        if refresh and self.lock.acquire(blocking=False):
            try:
                raw = self.rpc("thread/read", {"threadId": entry["thread"], "includeTurns": True})["thread"]
                with self.state_lock:
                    # A turn can be admitted while this RPC is in flight. Its
                    # new reservation/cache must not be overwritten by the read.
                    if self.mapping.get(session) is entry and self.active_session is None:
                        self.store_record(session, entry, raw)
                        self.save()
            finally:
                self.lock.release()
        # Return the private conversation cache, not paths/config/auth metadata.
        with self.state_lock:
            entry = self.mapping[session]
            state = "running" if self.active_session == session else entry["state"]
            record = {"turns": entry["record"].get("turns", [])}
            for key in ("displayOnly", "displayOnlyDropped"):  # #90: signed with the record
                if key in entry["record"]:
                    record[key] = entry["record"][key]
            extra = {"turnStatus": entry["turnStatus"]} if state != "running" and entry.get("turnStatus") else {}
            return {"requestId": entry["request"], "threadId": entry.get("thread"), "turnId": entry.get("turn_id"), "state": state,
                    "record": record, "errorCode": entry.get("error"), "model": MODEL, **extra}

    def close(self):
        if self.closed:
            return getattr(self, 'clean_shutdown', False)
        self.closing = True
        hub = self.stream_hub()
        if hub is not None:
            hub.close()  # #90: open streams end now, not at their next heartbeat
        active_at_start = self.active_session is not None
        alive_at_start = self.process.poll() is None
        forced = False
        worker = getattr(self, "worker", None)
        binding_ok = not getattr(self, "binding_failed", False)
        try:
            self.check_binding()
        except CodexFailure:
            binding_ok = False
        try:
            if alive_at_start:
                try:
                    self.process.stdin.close()  # Quiescent EOF is expected graceful shutdown.
                except (BrokenPipeError, OSError):
                    forced = True
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forced = True
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            worker = getattr(self, "worker", None)
            if worker:
                worker.join(timeout=20)
        finally:
            clean = (binding_ok and alive_at_start and not forced and not active_at_start
                     and self.active_session is None and self.process.poll() == 0
                     and not (worker and worker.is_alive()))
            self.clean_shutdown = clean
            if self.auth:
                self.auth.close(clean=clean)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream:
                    try:
                        stream.close()
                    except (BrokenPipeError, OSError):
                        pass
            self.closed = True
        return self.clean_shutdown


def end_streams_on_signal(app):
    """#90: uvicorn waits for every open response before the lifespan shutdown closes native, and the Pod command
    (policy-pinned, runner_kube.py) sets no graceful timeout. An open event stream would hold a SIGTERM until SIGKILL,
    skipping the clean native close. Chain SIGTERM/SIGINT: end the streams, then uvicorn's own handler. Returns the
    restore function. Outside the main thread (TestClient) nothing is installed."""
    import signal
    installed = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(sig)
        if not callable(previous):
            continue

        def handler(signum, frame, previous=previous):
            try:
                native = app.state.native
                hub = native.stream_hub() if native is not None else None
                if hub is not None:
                    hub.close()
            finally:
                previous(signum, frame)
        try:
            signal.signal(sig, handler)
        except ValueError:
            return lambda: None
        installed.append((sig, previous))

    def restore():
        for sig, previous in installed:
            try:
                signal.signal(sig, previous)
            except ValueError:
                pass
    return restore


def create_app():
    owner = str(uuid.UUID(os.environ["RUNNER_ACCOUNT_ID"]))
    state_id = os.getenv("RUNNER_STATE_ID")
    if state_id is not None:
        canonical_uuid(state_id)
    elif os.getenv("APP_ENV") != "test":
        raise ValueError("Explicit durable runner state identity is required")
    pod_uid = os.getenv("RUNNER_POD_UID")
    if pod_uid is not None:
        canonical_uuid(pod_uid)  # Auxiliary instance metadata, never an auth identity.
    # Kubernetes passes the transport token as an env reference (#59); drop it from the
    # environment so no later child process can inherit it. Local runs use a file.
    token = os.environ.pop("RUNNER_TOKEN", None)
    token = token.strip() if token is not None else Path(os.environ["RUNNER_TOKEN_FILE"]).read_text().strip()
    model = os.getenv("CODEX_MODEL", MODEL)
    if len(token) < 32 or model != MODEL or os.getenv("CODEX_API_KEY_FILE"):
        raise ValueError("Explicit private runner configuration is required")
    root = Path(os.environ.get("RUNNER_STATE_DIR", "/state"))
    lifecycle_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        if state_id:
            require_binding(root, owner, state_id)  # Refuse wrong/fresh volume before auth/native startup.
        bind_owner(root, owner)
        with volume_lease(root):
            auth = None
            supply = None
            app.state.native, app.state.auth_failure = None, None
            app.state.stopping = False
            app.state.idle_since = time.monotonic()
            app.state.drain = None
            try:
                mode = os.getenv("CODEX_AUTH_MODE", "none")
                if mode == "personal-cache":
                    if os.getenv("CODEX_EXECUTION_SCOPE") != "personal-private" or os.getenv("APP_ENV") != "personal-test":
                        raise CodexFailure("policy_refused")
                    auth = PersonalAuth(os.environ["CODEX_PERSONAL_CONTROL_DIR"], owner)
                elif mode == 'access-only':
                    if os.getenv('CODEX_EXECUTION_SCOPE') != 'personal-private' or os.getenv('APP_ENV') != 'personal-test' or not state_id:
                        raise CodexFailure('policy_refused')
                    auth = AccessAuth(os.environ['CODEX_ACCESS_CONTROL_DIR'], owner, state_id)
                elif mode == 'access-service':
                    if os.getenv('CODEX_EXECUTION_SCOPE') != 'operator-access-service' or os.getenv('APP_ENV') != 'operator-service' or not state_id:
                        raise CodexFailure('policy_refused')
                    account = read_private(Path(os.environ['CODEX_OPERATOR_ACCOUNT_FILE']), json_format=False).strip()
                    auth = ServiceAccessAuth(os.environ['CODEX_ACCESS_CONTROL_DIR'], owner, state_id, account)
                elif mode != "none":
                    raise CodexFailure("policy_refused")
                def start_native():
                    return Native(root, model, os.environ.get('CODEX_BIN', '/usr/local/bin/codex'),
                                  os.environ['RESEARCH_TOOL_CALLBACK_URL'], auth=auth,
                                  binding_check=(lambda: require_binding(root, owner, state_id)) if state_id else None)
                if mode in {'access-only', 'access-service'}:
                    app.state.auth_failure = 'not_configured'
                    def apply_locked(envelope):
                        if app.state.stopping:
                            raise CodexFailure('unavailable')
                        require_binding(root, owner, state_id)
                        try:
                            if app.state.native:
                                app.state.native.resupply(envelope)
                            else:
                                auth.install(envelope)
                                candidate = start_native()
                                if app.state.stopping:
                                    candidate.close()
                                    raise CodexFailure('unavailable')
                                app.state.native = candidate
                            app.state.auth_failure = None
                        except Exception as failure:
                            app.state.auth_failure = failure.reason if isinstance(failure, CodexFailure) else 'unavailable'
                            raise
                    def apply(envelope):
                        with lifecycle_lock:
                            return apply_locked(envelope)
                    if mode == 'access-service':
                        # #60: pull from codex-broker with this Pod's projected token; no host exec supply.
                        broker = BrokerClient(os.environ['BROKER_URL'], os.environ['BROKER_TOKEN_FILE'], owner, state_id)
                        auth.refresher = lambda: broker.fetch(refresh=True)
                        supply = BrokerPull(broker.fetch, apply)
                    else:
                        supply = SupplySocket(auth.control, apply)
                else:
                    app.state.native = start_native()
            except Exception as failure:
                app.state.auth_failure = failure.reason if isinstance(failure, CodexFailure) else "unavailable"
                if auth:
                    auth.close()
            restore = end_streams_on_signal(app)
            try:
                yield
            finally:
                restore()
                with lifecycle_lock:
                    app.state.stopping = True
                if supply:
                    supply.close()
                with lifecycle_lock:
                    if app.state.native:
                        app.state.native.close()
                    elif auth:
                        auth.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    class Authenticate:
        """Plain ASGI (#90): a BaseHTTPMiddleware would sit between the event stream and the API. The checks and
        responses are the former `authenticate` unchanged (config_tests/test_transport_middleware.py)."""
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            request = Request(scope)
            refused = None
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
                refused = JSONResponse({"error": "unauthenticated"}, status_code=401)
            elif state_id and not hmac.compare_digest(request.headers.get("x-runner-state-id", ""), state_id):
                refused = JSONResponse({"ownerId": owner, "error": "codex_policy_refused"}, status_code=503)
            elif state_id:
                try:
                    require_binding(root, owner, state_id)
                except CodexFailure:
                    if app.state.native:
                        app.state.native.blocked_reason = "policy_refused"
                        app.state.native.binding_failed = True
                    refused = JSONResponse({"ownerId": owner, "error": "codex_policy_refused"}, status_code=503)
            if refused is not None:
                return await refused(scope, receive, send)

            async def send_no_store(message):
                if message["type"] == "http.response.start":
                    MutableHeaders(scope=message)["Cache-Control"] = "no-store"
                await send(message)

            await self.app(scope, receive, send_no_store)

    app.add_middleware(Authenticate)

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        return JSONResponse({"ownerId": owner, "error": "codex_unavailable"}, status_code=503)

    @app.post('/lifecycle/idle')
    def idle_check(body: IdleCheck):
        with lifecycle_lock:
            native = app.state.native
            idle = False
            if not app.state.stopping and native is not None:
                with native.state_lock:
                    idle = (native.active_session is None and not native.lock.locked()
                            and all(entry.get('state') == 'idle' for entry in native.mapping.values())
                            and time.monotonic()-native.idle_since >= body.idleSeconds)
            elif not app.state.stopping and app.state.auth_failure == 'not_configured':
                idle = (not (root/'broker.json').exists() and not (root/'codex').exists()
                        and time.monotonic()-app.state.idle_since >= body.idleSeconds)
            elif not app.state.stopping and unavailable_reason() is not None:
                # #61 R1: native never started (supply or startup failure). Every turn and read is
                # refused without native, so no work can be in flight in this process.
                idle = time.monotonic()-app.state.idle_since >= body.idleSeconds
            return {'ownerId':owner,'stateId':state_id,'podUid':pod_uid,'idle':idle}

    def unavailable_reason():
        """The startup/supply failure of a runner whose native never started, else None."""
        reason = app.state.auth_failure
        if app.state.native is None and isinstance(reason, str) and reason and reason != 'not_configured':
            return reason
        return None

    @app.get("/health")
    def health():
        native = app.state.native
        # #61 R1: `failure` names why native never started, also while stopping, so the controller
        # can match it against an unavailable drain receipt.
        failure = {"failure": unavailable_reason()} if unavailable_reason() is not None else {}
        if app.state.stopping:
            return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid,
                    "verification": "unverified", "available": False, "reason": "unavailable", "model": MODEL, **failure}
        if not native:
            return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, "verification": "unverified", "available": False, "reason": app.state.auth_failure, "model": MODEL, **failure}
        reason = native.availability()
        return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, "verification": "fixture" if native.fixture else "real" if native.real_verified else "unverified", "available": reason is None, "reason": reason, "model": MODEL,
                **(native.auth.status() if getattr(native, 'external', False) else {})}

    def turn_locked(identity, body):
        if app.state.stopping:
            return JSONResponse({"ownerId": owner, "error": "codex_runner_stopped"}, status_code=503)
        if not app.state.native:
            return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure))}, status_code=503)
        try:
            app.state.native.accept(str(identity), body)
        except CodexFailure as failure:
            return JSONResponse({"ownerId": owner, "error": str(failure)}, status_code=503)
        except RuntimeError:
            return JSONResponse({"ownerId": owner, "error": "conflict"}, status_code=409)
        return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, "state": "running", "requestId": str(body.requestId)}

    @app.post("/sessions/{identity}/turn", status_code=202)
    def turn(identity: uuid.UUID, body: Turn):
        with lifecycle_lock:
            return turn_locked(identity, body)

    @app.post('/lifecycle/drain')
    def drain(body: Drain):
        # The authenticated controller binds generation to the current registry
        # and this Pod UID. A receipt proves only native drain, never Pod death.
        with lifecycle_lock:
            if (body.ownerId, body.stateId, body.podUid) != (owner, state_id, pod_uid):
                return JSONResponse({'error': 'codex_policy_refused'}, status_code=409)
            value = body.model_dump()
            if app.state.drain is not None:
                if app.state.drain != value:
                    return JSONResponse({'error': 'conflict'}, status_code=409)
                receipt = read_private(root / ('termination-'+pod_uid+'.json'))
                return receipt
            native = app.state.native
            if app.state.stopping:
                return JSONResponse({'error': 'codex_runner_stopped'}, status_code=503)
            if (root / ('termination-'+pod_uid+'.json')).exists():
                return JSONResponse({'error': 'codex_policy_refused'}, status_code=409)
            if native is not None:
                with native.state_lock:
                    # Failed/unknown projections require reconciliation too;
                    # process death alone cannot resolve an uncertain request.
                    if (native.active_session is not None or native.lock.locked()
                            or any(entry.get('state') != 'idle' for entry in native.mapping.values())):
                        return JSONResponse({'error': 'conflict'}, status_code=409)
                    app.state.stopping = True
                    native.closing = True
                receipt = {**value, 'version': 1, 'clean': native.close() is True}
            elif app.state.auth_failure == 'not_configured':
                # Never supplied: nothing was installed or started.
                app.state.stopping = True
                clean = not (root / 'broker.json').exists() and not (root / 'codex').exists()
                receipt = {**value, 'version': 1, 'clean': clean}
            elif unavailable_reason() is not None:
                # #61 R1: native never started, so no turn was ever accepted. A child spawned by the
                # failed start dies with the Pod, which the controller deletes next; the receipt says
                # so (version 3) instead of claiming a clean native drain. `clean` is evidence only.
                app.state.stopping = True
                clean = not (root / 'broker.json').exists() and not (root / 'codex').exists()
                receipt = {**value, 'version': 3, 'unavailable': {'reason': unavailable_reason(), 'clean': clean}}
            else:
                return JSONResponse({'error': 'codex_unavailable'}, status_code=503)
            require_binding(root, owner, state_id)
            write_private(root / ('termination-'+pod_uid+'.json'), receipt)
            app.state.drain = value
            return receipt

    def read_locked(identity):
        if app.state.stopping:
            return JSONResponse({"ownerId": owner, "error": "codex_runner_stopped"}, status_code=503)
        if not app.state.native:
            return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure))}, status_code=503)
        result = app.state.native.read(str(identity))
        if result is None:
            return JSONResponse({"ownerId": owner, "error": "not_found"}, status_code=404)
        return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, **result}

    @app.get("/sessions/{identity}")
    def read(identity: uuid.UUID):
        with lifecycle_lock:
            return read_locked(identity)

    @app.post("/sessions/{identity}/interrupt")
    def interrupt(identity: uuid.UUID, body: Interrupt):
        # #90 Stop. Not under lifecycle_lock's turn admission: it only flags this request and writes one line.
        with lifecycle_lock:
            if app.state.stopping:
                return JSONResponse({"ownerId": owner, "error": "codex_runner_stopped"}, status_code=503)
            native = app.state.native
            if not native:
                return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure))}, status_code=503)
        result = native.interrupt(str(identity), str(body.requestId))
        return {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, "sessionId": str(identity),
                "requestId": str(body.requestId), "state": result}

    @app.get("/sessions/{identity}/events")
    async def events(identity: uuid.UUID, request: Request, after: str | None = None):
        """#90 SSE: hello, then the session's turn events (design r2 M2 / r3). Never holds lifecycle_lock."""
        native = app.state.native
        if app.state.stopping:
            return JSONResponse({"ownerId": owner, "error": "codex_runner_stopped"}, status_code=503)
        if not native or native.stream_hub() is None:
            return JSONResponse({"ownerId": owner, "error": str(CodexFailure(app.state.auth_failure or "unavailable"))}, status_code=503)
        try:
            cursor = streams.parse_cursor(after if after is not None else request.headers.get("last-event-id"))
        except ValueError:
            return JSONResponse({"ownerId": owner, "error": "invalid_cursor"}, status_code=400)
        hub, session = native.stream_hub(), str(identity)

        def open_subscription():  # takes threading locks: off the event loop
            with native.state_lock:
                entry = dict(native.mapping.get(session) or {})
            subscription, initial = hub.subscribe(session, cursor)
            return subscription, initial, hub.hello(session, entry)
        subscription, initial, hello = await run_in_threadpool(open_subscription)
        if subscription is None:
            return JSONResponse({"ownerId": owner, "error": "stream_limit"}, status_code=429)
        hello = {"ownerId": owner, "stateId": state_id, "podUid": pod_uid, **hello}
        # The response itself ends on drain/SIGTERM (Hub.close -> Subscription.stop), even mid-send to a slow reader.
        import asyncio
        subscription.loop, subscription.stop_event = asyncio.get_running_loop(), asyncio.Event()
        if subscription.closed or app.state.stopping:
            subscription.stop_event.set()
        return streams.StoppableStreamingResponse(
            streams.stream(hub, subscription, initial, hello, lambda: app.state.stopping), subscription.stop_event,
            on_close=lambda: hub.unsubscribe(subscription),
            media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    return app
