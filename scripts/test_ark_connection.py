"""One-call, low-output connectivity check for Ark Agent Plan."""

from __future__ import annotations

import argparse
import sys
import time

from sociology_research.config import Settings
from sociology_research.llm_client import ModelRequestError, OpenAICompatibleClient


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one tiny request to verify Agent Plan key, endpoint, model, and Chat Completions access."
    )
    parser.add_argument("--stage", choices=("lite", "pro"), default="lite")
    parser.add_argument("--timeout", type=int, default=60, help="Request timeout in seconds (default: 60)")
    args = parser.parse_args()

    try:
        settings = Settings.from_environment()
        api_key, base_url, lite_model, pro_model = settings.require_model_configuration()
        model = lite_model if args.stage == "lite" else pro_model
        client = OpenAICompatibleClient(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=max(10, min(args.timeout, 120)),
            progress=lambda message: print(message, flush=True),
        )
        started = time.monotonic()
        response = client.complete_json(
            model=model,
            system_prompt="Return the requested short confirmation exactly. Do not explain.",
            user_prompt='Reply with exactly: {"connection":"ok"}',
            max_tokens=24,
            thinking="disabled",
        )
        elapsed = time.monotonic() - started
        print(f"Connection successful | stage={args.stage} | model={model} | seconds={elapsed:.1f}")
        print(f"Response received ({len(response)} characters).")
        return 0
    except (ValueError, ModelRequestError) as exc:
        print(f"Connection check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
