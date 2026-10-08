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
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._progress = progress

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
            with urlopen(request, timeout=self._timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ModelRequestError(
                f"Model service rejected the request (HTTP {exc.code}); check the Agent Plan key, base URL, model name, and quota."
            ) from None
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
                raise ModelRequestError(
                    "Model output reached max_completion_tokens and was truncated; no incomplete result was saved."
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
            raise ModelRequestError("Model returned an empty response.")
        return content.strip()
