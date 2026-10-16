"""Minimal OpenAI Chat Completions client for Ark Agent Plan."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ModelRequestError(RuntimeError):
    """A safe-to-display model request error; credentials are never included."""


def _safe_provider_id(value: object) -> str:
    """Never show arbitrary provider messages, credentials, or paper excerpts."""
    import re
    if not isinstance(value, (str, int)):
        return ""
    token = str(value)
    return token if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", token) else ""


def _http_error_message(exc: HTTPError, *, model: str = "") -> str:
    """Distinguish Agent Plan quota, model TPM/RPM and transient 429 failures.

    The provider-controlled error.message may quote private payloads; it is
    NEVER displayed. Only a restricted error.code/type, safe request ID and
    bounded Retry-After value are included.
    """
    status = exc.code
    headers = getattr(exc, "headers", None)
    try:
        raw = exc.read(8192)
        decoded = json.loads(raw.decode("utf-8", errors="replace"))
        error = decoded.get("error", {}) if isinstance(decoded, dict) else {}
    except (ValueError, OSError, TypeError):
        error = {}
    if not isinstance(error, dict):
        error = {}

    code = _safe_provider_id(error.get("code"))
    code_type = _safe_provider_id(error.get("type"))
    error_name = (code or code_type).lower()
    compact_error_name = error_name.replace("_", "").replace("-", "")
    code_label = f" [error.code={code or code_type}]" if (code or code_type) else ""
    model_id = _safe_provider_id(model)
    model_label = f" [model={model_id}]" if model_id else ""
    request_id = ""
    retry_seconds = ""
    if headers is not None:
        try:
            for name in ("x-tt-logid", "x-request-id", "x-requestid"):
                request_id = _safe_provider_id(headers.get(name))
                if request_id:
                    break
            retry_value = str(headers.get("Retry-After", "")).strip()
            if retry_value.isascii() and retry_value.isdecimal() and 0 < int(retry_value) <= 86400:
                retry_seconds = f" Retry-After={int(retry_value)}s ({int(retry_value)} seconds)."
        except (AttributeError, TypeError, ValueError):
            pass
    id_label = f" [request_id={request_id}]" if request_id else ""

    if status == 429:
        if "agentplanquotaexceeded" in error_name or (
            "quota" in error_name and "exceed" in error_name
        ) or "insufficient_quota" in error_name:
            problem = (
                 "Agent Plan/model quota/balance exhausted or provider quota refusal. Check AFP "
                "period limits, model entitlements and provider detail; "
                "do not buy a higher plan merely from this error."
            )
        elif any(term in error_name for term in ("tpm", "tokenratelimit")):
            problem = (
                "Model-specific TPM/token reservation limit. The Pro input "
                "plus requested maximum output may exceed rate limits. "
                "Check model TPM settings and reduce output reservation "
                "before choosing to retry."
            )
        elif any(term in compact_error_name for term in ("rpm", "ratelimit", "ratelimitexceeded")):
            problem = "Provider request rate limit. Wait for the retry window or reduce concurrency."
        elif "overload" in error_name or "capacity" in error_name:
            problem = "Provider/model capacity is temporarily exhausted."
        else:
            problem = (
                "Provider refused this request; may be model-specific TPM/RPM, "
                "Agent Plan quota, entitlement or temporary capacity. "
                "Check the provider's safe error code and request ID."
            )
        return (f"HTTP 429: {problem}{retry_seconds}{code_label}"
                f"{model_label}{id_label} No automatic paid retry was made; "
                "Lite cache remains reusable.")

    if status in (401, 403):
        problem = "API authorization/entitlement failed; check key and model access."
    elif status in (400, 404, 422):
        problem = "Request parameters, model ID or API path were rejected."
    elif status in (500, 502, 503, 504):
        problem = "Provider server temporarily failed; request might still be billed."
    else:
        problem = "Provider declined the request; check model configuration."
    return f"HTTP {status}: {problem}{code_label}{model_label}{id_label}"


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: int = 420,
        progress: Callable[[str], None] | None = None,
    ):
        if not api_key.strip():
            raise ValueError("API key cannot be blank")
        if not base_url.strip().startswith(("https://", "http://")):
            raise ValueError("API base URL must use HTTP or HTTPS")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._endpoint = self._base_url + "/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._progress = progress
        # A per-client opener applies the requested proxy without changing the
        # download worker or another running AI request.
        from .network_settings import ai_request_opener
        self._opener = ai_request_opener()

    def list_model_ids(self) -> list[str]:
        """Best-effort provider /models listing; not every compatible API offers it."""
        request = Request(
            self._base_url + "/models",
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            method="GET",
        )
        sender = self._opener.open if self._opener is not None else urlopen
        try:
            with sender(request, timeout=min(30, self._timeout_seconds)) as response:
                data = json.loads(response.read(1_000_001).decode("utf-8"))
        except HTTPError as exc:
            raise ModelRequestError(_http_error_message(exc)) from None
        except (URLError, OSError, TimeoutError):
            raise ModelRequestError("Could not retrieve model list from this provider.") from None
        except (ValueError, UnicodeError):
            raise ModelRequestError("Provider returned an invalid model list.") from None
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ModelRequestError("Provider does not expose a compatible /models catalog; enter the model ID manually.")
        ids = [_safe_provider_id(item.get("id")) for item in data["data"][:200]
               if isinstance(item, dict)]
        return sorted({value for value in ids if value})

    def complete_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        thinking: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        if not model.strip():
            raise ValueError("Model name cannot be blank")
        payload = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": max_tokens,
                "stream": False,
                "response_format": {"type": "json_object"},
                **({"thinking": {"type": thinking}} if thinking else {}),
                **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            self._endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        stop_heartbeat = threading.Event()
        started = time.monotonic()

        def heartbeat() -> None:
            while not stop_heartbeat.wait(30):
                if self._progress:
                    elapsed = int(time.monotonic() - started)
                    self._progress(f"Waiting for model response ({elapsed}s elapsed; timeout {self._timeout_seconds}s).")

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        if self._progress:
            heartbeat_thread.start()
        try:
            sender = self._opener.open if self._opener is not None else urlopen
            with sender(request, timeout=self._timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ModelRequestError(_http_error_message(exc, model=model)) from None
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise ModelRequestError("Model service request timed out.") from None
            raise ModelRequestError("Could not connect to the model service.") from None
        except TimeoutError:
            raise ModelRequestError("Model service request timed out.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelRequestError("Model service returned an invalid JSON response.") from None
        except OSError:
            raise ModelRequestError("Network error while contacting the model service.") from None
        finally:
            stop_heartbeat.set()
            if heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=1)

        try:
            choice = response_data["choices"][0]
            if choice.get("finish_reason") == "length":
                usage = response_data.get("usage", {})
                details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
                thought = details.get("reasoning_tokens") if isinstance(details, dict) else None
                reasoning_note = (
                    f" (reasoning_tokens={thought})"
                    if type(thought) is int and 0 <= thought <= 1000000 else ""
                )
                raise ModelRequestError(
                    "Model reached max_completion_tokens before completing valid JSON"
                    + reasoning_note
                    + ". The provider may have spent part of the budget reasoning. "
                      "No incomplete result was saved; no automatic paid retry."
                )
            content = choice["message"]["content"]
        except ModelRequestError:
            raise
        except (KeyError, IndexError, TypeError):
            raise ModelRequestError("Model response did not contain a chat completion.") from None

        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise ModelRequestError(
                "Model returned no final text (reasoning-only/empty content). "
                "Try an appropriate non-reasoning or lower-effort model, or "
                "check output token budget. No automatic retry was made."
            )
        return content.strip()
