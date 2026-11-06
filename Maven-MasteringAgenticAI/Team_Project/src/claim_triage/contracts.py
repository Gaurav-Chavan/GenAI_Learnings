"""Shared contracts: the same shapes are used by the rules-only baseline and (later) by the agents."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Band = Literal["red", "amber", "green"]
BAND_LABEL = {"red": "Red", "amber": "Review needed", "green": "Green"}


class Indicator(BaseModel):
    code: str
    severity: Literal["low", "medium", "high"] = "high"
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str
    counter_evidence: list[str] = Field(default_factory=list)
    hard_rule: bool = False


class MissingItem(BaseModel):
    document: str
    reason: str


class InvestigationResult(BaseModel):
    claim_id: str
    agent_type: str
    indicators: list[Indicator] = Field(default_factory=list)
    missing_evidence: list[MissingItem] = Field(default_factory=list)
    summary: str = ""
    trace_id: str | None = None


class FinalResult(BaseModel):
    claim_id: str
    case_id: str | None = None
    trust_score: float = Field(ge=0, le=1)
    band: Band
    evidence_status: Literal["sufficient", "insufficient"]
    reasons: list[str]
    outstanding_checks: list[str] = Field(default_factory=list)
    counterfactual: str | None = None
    next_action: str
    indicators: list[Indicator] = Field(default_factory=list)
    missing_evidence: list[MissingItem] = Field(default_factory=list)
    contributions: dict[str, float] = Field(default_factory=dict)
    dropped_indicators: list[str] = Field(default_factory=list)  # fired but had no verifiable evidence
    provider_used: str | None = None  # model provider; None for the rules-only baseline
    models_used: dict[str, str] = Field(default_factory=dict)  # role -> model that really answered (agentic pipeline only)
    tokens: int = 0
    cost_usd: float | None = None
    trace: dict = Field(default_factory=dict)  # compact per-step record (agents, tool calls, verifier changes, explainer status)

    @property
    def band_label(self) -> str:
        return BAND_LABEL[self.band]
