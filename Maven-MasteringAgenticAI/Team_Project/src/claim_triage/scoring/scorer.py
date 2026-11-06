"""The scorer: pure Python. Indicators in, trust score and band out.

trust = 1 - risk;  risk = 1 - prod(1 - weight * confidence)   (noisy-OR: stacked signals add up with diminishing returns)
Only indicators backed by at least one verified evidence ID are counted. Hard rules and the evidence gate cap the trust score.
"""
from __future__ import annotations

import math
from functools import lru_cache
from types import SimpleNamespace

import yaml

from ..contracts import FinalResult, Indicator, MissingItem
from ..data.config import ROOT

DOC_NAMES = {"bill": "original bill", "itemised_bill": "itemised bill", "discharge_summary": "discharge summary", "repair_estimate": "repair estimate",
             "fir": "FIR (police report)", "inspection_report": "inspection report", "death_certificate": "death certificate",
             "claim_form": "claim form", "cause_of_death": "cause-of-death report", "nominee_consent": "nominee consent form",
             "provider_registration": "provider registration certificate"}


@lru_cache(maxsize=1)
def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_weights(path: str | None = None) -> dict:
    return _load(path or str(ROOT / "config" / "weights.yaml"))


def thresholds(W: dict) -> SimpleNamespace:
    return SimpleNamespace(**W["thresholds"])


def band_of(trust: float, W: dict) -> str:
    if trust < W["bands"]["red_below"]:
        return "red"
    return "green" if trust > W["bands"]["green_above"] else "amber"


def trust_from(indicators: list[Indicator], W: dict) -> tuple[float, dict[str, float]]:
    contributions, survive = {}, 1.0
    for ind in indicators:
        w = W["indicators"][ind.code]["weight"] * ind.confidence
        contributions[ind.code] = round(w, 4)
        survive *= 1 - w
    return 1 - (1 - survive), contributions


def score(claim_id: str, case_id: str | None, indicators: list[Indicator], missing: list[MissingItem], W: dict,
          dropped: list[str] | None = None) -> FinalResult:
    verified = [i for i in indicators if i.evidence_ids]
    dropped = (dropped or []) + [i.code for i in indicators if not i.evidence_ids]
    trust, contributions = trust_from(verified, W)
    caps = W["caps"]
    if any(i.hard_rule for i in verified):
        trust = min(trust, caps["hard_rule_trust_max"])
    ungated = trust
    if missing:
        trust = min(trust, caps["insufficient_evidence_trust_max"])
    trust = round(trust, 4)
    band = band_of(trust, W)

    ordered = sorted(verified, key=lambda i: -contributions[i.code])
    reasons = [i.explanation for i in ordered]
    reasons += [f"Missing: {DOC_NAMES.get(m.document, m.document)} ({m.reason})." for m in missing]
    outstanding = [f"Obtain the {DOC_NAMES.get(m.document, m.document)}: {m.reason}." for m in missing]
    if band == "green":
        reasons = ["No fraud indicators were found and every required document is on file. This is not an approval; the reviewer decides."]
    counterfactual = None
    if missing and band_of(round(ungated, 4), W) == "green":
        counterfactual = "Would turn green if these are supplied: " + ", ".join(DOC_NAMES.get(m.document, m.document) for m in missing) + "."
    action = {"red": "Priority manual investigation.",
              "amber": "Review the flagged points" + (" and request the missing documents." if missing else "."),
              "green": "Proceed to normal review (the reviewer still decides)."}[band]
    return FinalResult(claim_id=claim_id, case_id=case_id, trust_score=trust, band=band, evidence_status="insufficient" if missing else "sufficient",
                       reasons=reasons, outstanding_checks=outstanding, counterfactual=counterfactual, next_action=action,
                       indicators=ordered, missing_evidence=missing, contributions=contributions, dropped_indicators=dropped)


def is_close(a: float, b: float) -> bool:
    return math.isclose(a, b, abs_tol=1e-9)
