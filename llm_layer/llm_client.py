# llm_layer/llm_client.py

import asyncio
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from loguru import logger
from pydantic import SecretStr

load_dotenv()

# ─────────────────────────────────────────────────────────────
# Configuration — all values sourced from .env via config.py.
# Env-var overrides are checked first so callers can set them
# without modifying the config file.
# ─────────────────────────────────────────────────────────────
try:
    from config.config import setting as _cfg
    _DEFAULT_MODEL       = os.getenv("OPENAI_MODEL",       str(getattr(_cfg, "OPENAI_MODEL",  "gpt-5.4-mini")))
    _DEFAULT_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", str(getattr(_cfg, "TEMPERATURE",   0))))
    _DEFAULT_MAX_TOKENS  = int(os.getenv("OPENAI_MAX_TOKENS",    str(getattr(_cfg, "MAX_TOKENS", 4096))))
except Exception:
    # Fallback when running outside the full app context (tests, scripts)
    _DEFAULT_MODEL       = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    _DEFAULT_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0"))
    _DEFAULT_MAX_TOKENS  = int(os.getenv("OPENAI_MAX_TOKENS", "4096"))

_MAX_RETRIES        = int(os.getenv("LLM_MAX_RETRIES", "3"))
_RETRY_BACKOFF_BASE = int(os.getenv("LLM_BACKOFF_BASE", "2"))


class LLMClient:

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")

        self.model_name = model
        self.llm = ChatOpenAI(
            api_key=SecretStr(api_key),
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def generate_response(self, prompt: str, parser=None):
        """
        Invoke the LLM with retry on transient errors.

        - With parser  : returns parsed structured output (dict/object)
        - Without parser: returns raw response content (str) — used for
          CoT Step 1 (clinical reader) which produces free-text reasoning
        """
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                system_msg = (
                    "Return ONLY valid JSON output."
                    if parser
                    else "You are a helpful medical billing assistant."
                )
                messages = [
                    SystemMessage(content=system_msg),
                    HumanMessage(content=prompt),
                ]

                response = await self.llm.ainvoke(messages)

                if not response.content:
                    raise RuntimeError("Empty LLM response")

                if parser:
                    return parser.parse(response.content)

                return response.content

            except Exception as e:
                last_exc = e
                is_last = attempt == _MAX_RETRIES - 1
                if is_last:
                    break
                delay = _RETRY_BACKOFF_BASE ** (attempt + 1)
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{_MAX_RETRIES}): {e} "
                    f"— retrying in {delay}s"
                )
                await asyncio.sleep(delay)

        logger.exception(f"LLM call failed after {_MAX_RETRIES} attempts: {last_exc}")
        raise RuntimeError(f"LLM generation failed: {last_exc}")
