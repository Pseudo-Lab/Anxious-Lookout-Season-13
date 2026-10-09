"""Public safe status codes; model choice is never a browser input."""
MODEL = "gpt-6.1-sol"
REASONS = {"not_configured", "not_enabled_for_account", "auth_expired", "auth_revoked",
           "model_unavailable", "policy_refused", "budget_exhausted", "unavailable"}
ERRORS = {"auth_expired": "codex_auth_expired", "auth_revoked": "codex_auth_revoked",
          "model_unavailable": "codex_model_unavailable", "policy_refused": "codex_policy_refused",
          "budget_exhausted": "codex_budget_exhausted", "not_enabled_for_account": "codex_not_enabled_for_account"}


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
