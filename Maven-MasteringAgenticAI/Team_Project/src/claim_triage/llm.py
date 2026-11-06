"""Model access through LiteLLM: one place for model names, retries, fallbacks, cost and latency.

Rules: retry only transient failures (timeouts, rate limits, provider outages); never retry bad requests or auth errors; fall back to the
other vendor once; always report which model really answered. If everything fails the caller gets LLMUnavailable and must not invent output.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # do not fetch the price list from the internet at start-up
try:  # use the operating system's certificate store (needed behind TLS inspection); harmless elsewhere
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass

from . import tracing
from .apikeys import load_api_keys
from .data.config import ROOT


class LLMUnavailable(RuntimeError):
    """Every model for this role failed. The caller must report 'analysis unavailable', never fabricate a result."""


@lru_cache(maxsize=1)
def load_models(path: str | None = None) -> dict:
    with open(path or ROOT / "config" / "models.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[dict]
    model: str
    fell_back: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    raw_message: dict = field(default_factory=dict, repr=False)  # the assistant message, to append to a conversation


def _transient(exc: Exception) -> bool:
    import litellm
    return isinstance(exc, (litellm.RateLimitError, litellm.APIConnectionError, litellm.Timeout, litellm.ServiceUnavailableError,
                            litellm.InternalServerError))


class LLM:
    def __init__(self, role: str, models: dict | None = None):
        load_api_keys()
        import litellm
        litellm.suppress_debug_info = True
        tracing.enable()
        self.cfg = models or load_models()
        self.role = role
        self.agent = self.cfg["agents"][role]
        self.limits = self.cfg["limits"]

    def model_for(self, variant: str = "default") -> str:
        return self.agent.get(variant) or self.agent["default"]

    def chat(self, messages: list[dict], *, variant: str = "default", tools: list[dict] | None = None, json_mode: bool = False,
             max_tokens: int | None = None) -> LLMResult:
        import litellm
        order = [self.model_for(variant)] + ([self.agent["fallback"]] if self.agent.get("fallback") else [])
        last: Exception | None = None
        for i, model in enumerate(order):
            for attempt in range(2):  # one retry for a transient failure, then move to the fallback model
                t0 = time.time()
                try:
                    kwargs = dict(model=model, messages=messages, max_tokens=max_tokens or self.limits["max_output_tokens"],
                                  timeout=self.limits["timeout_seconds"])
                    if not model.startswith("anthropic/"):  # Claude 5.5 models accept only their default temperature
                        kwargs["temperature"] = self.limits["temperature"]
                    kwargs.update(tracing.metadata(self.role))
                    if tools:
                        kwargs["tools"] = tools
                    if json_mode:
                        kwargs["response_format"] = {"type": "json_object"}
                    resp = litellm.completion(**kwargs)
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if _transient(exc) and attempt == 0:
                        time.sleep(1.5)
                        continue
                    break
                msg = resp.choices[0].message
                calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments} for c in (msg.tool_calls or [])]
                try:
                    cost = float(litellm.completion_cost(completion_response=resp))
                except Exception:  # noqa: BLE001 - unknown price for a brand-new model
                    cost = None
                usage = resp.usage
                return LLMResult(content=msg.content, tool_calls=calls, model=model, fell_back=i > 0, latency_ms=round((time.time() - t0) * 1000),
                                 input_tokens=getattr(usage, "prompt_tokens", 0) or 0, output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                                 cost_usd=cost, raw_message=msg.model_dump(exclude_none=True))
        raise LLMUnavailable(f"all models failed for role '{self.role}': {type(last).__name__}")
