"""One opt-in live model call. Exits 2 when no OpenAI-compatible server is configured."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api"), str(ROOT / "packages" / "recovery_core")]

from recovery_api.llm import OpenAICompatibleCompleter
from recovery_api.settings import get_settings


def main() -> int:
    settings = get_settings()
    if settings.cir_investigator_mode != "live" or not settings.cir_llm_base_url or not settings.cir_llm_model:
        print("BLOCKED: set CIR_INVESTIGATOR_MODE=live, CIR_LLM_BASE_URL, and CIR_LLM_MODEL. A local Ollama server can be used with no API key.")
        return 2
    client = OpenAICompatibleCompleter(
        settings.cir_llm_base_url, settings.cir_llm_api_key, settings.cir_llm_model, timeout=settings.cir_llm_timeout_seconds
    )
    text, usage = client([{"role": "user", "content": "Reply with a JSON object {\"status\": \"ready\"}."}])
    print(f"model={settings.cir_llm_model} tokens={usage.get('total_tokens')} text={text[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
