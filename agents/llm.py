"""One `ask()` for every LLM provider the agents use.

Providers, each through its own official SDK:
  anthropic   Claude via `anthropic` (beta messages, server-side refusal fallbacks on)
  deepseek    DeepSeek via `openai` pointed at https://api.deepseek.com, the client
              DeepSeek's own docs recommend

Configuration is environment only (`.env` on the host, never committed):
  LLM_PROVIDER              default provider (anthropic)
  ANTHROPIC_API_KEY / DEEPSEEK_API_KEY
  LLM_MODEL_ANTHROPIC       default claude-opus-5-5
  LLM_MODEL_DEEPSEEK        default deepseek-v4-pro

Every failure, including a model refusal, raises LLMError. Callers are expected to fall
back to deterministic output: an agent must never be the reason a report is missing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "deepseek": "deepseek-v4-pro"}
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
TIMEOUT_S = 100.0          # must outlast agents/shadow.TIMEOUT_S, so the shadow decides


class LLMError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def _model(provider, model):
    return model or os.getenv(f"LLM_MODEL_{provider.upper()}") or DEFAULT_MODELS[provider]


def _ask_anthropic(prompt, system, model, max_tokens, client):
    if client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY is not set")
        import anthropic
        client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=2)
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            # Thinking cannot be disabled on Opus 5.5; low effort keeps a summary cheap.
            output_config={"effort": "low"},
            # A declined request is re-run server-side on Anthropic's recommended model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as exc:  # SDK error classes vary; the caller only needs "it failed"
        raise LLMError(f"anthropic: {exc}") from exc
    if resp.stop_reason == "refusal":
        raise LLMError("anthropic: request refused")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise LLMError("anthropic: empty reply")
    return LLMReply(text, "anthropic", resp.model, resp.usage.input_tokens,
                    resp.usage.output_tokens)


def _ask_deepseek(prompt, system, model, max_tokens, client):
    if client is None:
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise LLMError("DEEPSEEK_API_KEY is not set")
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, timeout=TIMEOUT_S,
                        max_retries=2)
    messages = ([{"role": "system", "content": system}] if system else []) + \
        [{"role": "user", "content": prompt}]
    try:
        resp = client.chat.completions.create(model=model, messages=messages,
                                              max_tokens=max_tokens)
    except Exception as exc:
        raise LLMError(f"deepseek: {exc}") from exc
    text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    if not text:
        raise LLMError("deepseek: empty reply")
    u = getattr(resp, "usage", None)
    return LLMReply(text, "deepseek", resp.model, getattr(u, "prompt_tokens", 0),
                    getattr(u, "completion_tokens", 0))


_ADAPTERS = {"anthropic": _ask_anthropic, "deepseek": _ask_deepseek}


def ask(prompt, system=None, provider=None, model=None, max_tokens=4000, client=None):
    """Send one prompt; return an LLMReply or raise LLMError. `client` is for tests."""
    provider = (provider or os.getenv("LLM_PROVIDER") or "anthropic").lower()
    if provider not in _ADAPTERS:
        raise LLMError(f"unknown LLM provider {provider!r}")
    return _ADAPTERS[provider](prompt, system, _model(provider, model), max_tokens, client)
