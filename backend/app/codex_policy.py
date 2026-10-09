"""Public safe status codes; model choice is never a browser input."""
import os
from urllib.parse import urlsplit

MODEL = "gpt-6.1-sol"
REASONS = {"not_configured", "not_enabled_for_account", "auth_expired", "auth_revoked",
           "model_unavailable", "policy_refused", "budget_exhausted", "unavailable"}
ERRORS = {"auth_expired": "codex_auth_expired", "auth_revoked": "codex_auth_revoked",
          "model_unavailable": "codex_model_unavailable", "policy_refused": "codex_policy_refused",
          "budget_exhausted": "codex_budget_exhausted", "not_enabled_for_account": "codex_not_enabled_for_account"}


def personal_origin_enabled(settings):
    """Deployment admission only; flags never prove grant/support/access ownership."""
    if os.getenv("CODEX_PERSONAL_ENABLE") != "true" or os.getenv("APP_ENV") != "personal-test":
        return False
    parsed = urlsplit(settings.origin)
    if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    return (parsed.scheme == "https" and settings.secure_cookie
            and settings.base_path == "/codex-trial" and bool(settings.trusted_proxy_cidrs)
            and os.getenv("CODEX_EXECUTION_SCOPE") == "personal-private"
            and os.getenv("CODEX_PERSONAL_REMOTE_ORIGIN") == settings.origin)


class CodexFailure(RuntimeError):
    def __init__(self, reason="unavailable"):
        self.reason = reason if reason in REASONS else "unavailable"
        super().__init__(ERRORS.get(self.reason, "codex_unavailable"))


def error_reason(error):
    # Provider free text/details are never persisted or returned as errors.
    if not isinstance(error, dict):
        return "unavailable"
    data = error.get("data") if isinstance(error.get("data"), dict) else error
    info = data.get("codexErrorInfo")
    code = data.get("code")
    message = error.get("message", "")
    message = message.lower() if isinstance(message, str) else ""
    if code in {"model_not_found", "model_unavailable", "unsupported_model"} or ("model" in message and any(term in message for term in ("not available", "not found", "does not exist", "do not have access", "not supported"))):
        return "model_unavailable"
    if info == "unauthorized":
        return "auth_expired" if "expired" in message else "auth_revoked"
    if info in ("cyberPolicy", "misalignmentPolicyViolation", "tooManyDenials"):
        return "policy_refused"
    return "unavailable"
