"""Small shared helpers for the agents."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm import LLMResult


def parse_json_object(text: str | None) -> dict:
    """Parse the first JSON object in a model reply (tolerates ```json fences and surrounding words)."""
    if not text:
        raise ValueError("empty reply")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_known: bool = True
    latency_ms: int = 0
    models: list[str] = field(default_factory=list)
    fell_back: bool = False

    def add(self, r: LLMResult) -> None:
        self.calls += 1
        self.input_tokens += r.input_tokens
        self.output_tokens += r.output_tokens
        self.latency_ms += r.latency_ms
        if r.cost_usd is None:
            self.cost_known = False
        else:
            self.cost_usd += r.cost_usd
        if r.model not in self.models:
            self.models.append(r.model)
        self.fell_back = self.fell_back or r.fell_back

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def summary(self) -> dict:
        return dict(calls=self.calls, tokens=self.tokens, cost_usd=round(self.cost_usd, 5) if self.cost_known else None,
                    latency_ms=self.latency_ms, models=self.models, fell_back=self.fell_back)
