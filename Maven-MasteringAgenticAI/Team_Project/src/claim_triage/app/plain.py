"""Plain-language names for the findings, so a reviewer never has to read a code like BILL_OUTLIER_VS_PEERS."""
from __future__ import annotations

from functools import lru_cache

import yaml

from ..data.config import ROOT

DOCUMENT_NAMES = {
    "bill": "Hospital bill", "itemised_bill": "Itemised bill", "discharge_summary": "Discharge summary", "repair_estimate": "Repair estimate",
    "fir": "Police report (FIR)", "inspection_report": "Inspection report", "death_certificate": "Death certificate", "claim_form": "Claim form",
    "medical_report": "Medical report", "nominee_consent": "Nominee consent form", "cause_of_death": "Cause-of-death report",
    "correction_affidavit": "Correction affidavit", "provider_registration": "Provider registration certificate", "original_bill": "Original bill",
}


@lru_cache(maxsize=1)
def _cards() -> dict:
    with open(ROOT / "knowledge" / "fraud_patterns.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def finding_name(code: str) -> str:
    card = _cards().get(code)
    return card["name"] if card else code.replace("_", " ").capitalize()


def finding_note(code: str) -> str:
    """What a finding means and the innocent explanations to keep in mind (from the pattern card)."""
    card = _cards().get(code)
    if not card:
        return ""
    exceptions = "\n".join(f"- {e}" for e in card.get("legitimate_exceptions", []))
    return f"{card['description']}\n\nInnocent explanations to check:\n{exceptions}" if exceptions else card["description"]


def document_name(doc_type: str) -> str:
    return DOCUMENT_NAMES.get(doc_type, doc_type.replace("_", " ").capitalize())
