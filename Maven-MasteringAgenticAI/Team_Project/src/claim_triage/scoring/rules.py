"""Deterministic detection rules (the rules-only baseline).

Each rule reads the claim's prepared fact sheet and fetches its evidence THROUGH THE TOOLS, so every evidence ID it cites was
really returned by a tool. A rule returns an Indicator (with a plain-words explanation) or None.
"""
from __future__ import annotations

from types import SimpleNamespace

from ..contracts import Indicator, MissingItem
from ..tools import ToolBox


class Ctx:
    def __init__(self, box: ToolBox, claim_id: str, T: SimpleNamespace):
        self.box, self.T, self.claim_id = box, T, claim_id
        self.claim = box.call("get_claim", claim_id=claim_id)["records"][0]
        self.f = box.call("get_claim_facts", claim_id=claim_id)["records"][0]
        self.docs = {d["doc_type"] for d in self.claim["documents"]}
        self._similar = None

    def other_claim(self, claim_id: str) -> str:
        """Look a second claim up through the tool, so its ID enters the evidence trail."""
        return self.box.call("get_claim", claim_id=claim_id)["records"][0]["claim_id"]

    def similar(self) -> list[dict]:
        if self._similar is None:
            self._similar = self.box.call("find_similar_claims", claim_id=self.claim_id, limit=25)["records"]
        return self._similar

    def doc_ids(self, *types: str) -> list[str]:
        return [d["doc_id"] for d in self.claim["documents"] if d["doc_type"] in types]


def duplicate(c: Ctx):
    other = c.f.get("dup_candidate_claim_id")
    if not other:
        return None
    exact = bool(c.f.get("dup_exact_invoice"))
    oid = c.other_claim(other)
    why = "same provider, procedure, admission dates and amount" + (", and the same invoice number" if exact else "")
    return Indicator(code="DUPLICATE_CLAIM", confidence=1.0 if exact else 0.85, evidence_ids=[c.claim_id, oid],
                     explanation=f"This claim matches the earlier claim {oid}: {why}.", hard_rule=exact)


def bill_outlier(c: Ctx):
    ratio = c.f.get("bill_vs_peer_median")
    if ratio is None or ratio < c.T.bill_ratio:
        return None
    stats = c.box.call("get_provider_stats", provider_id=c.claim["provider_id"], procedure_code=c.f["procedure_code"], as_of=c.f["filed_date"])
    return Indicator(code="BILL_OUTLIER_VS_PEERS", confidence=min(1.0, (ratio - 1) / 2), evidence_ids=[c.claim_id] + stats["evidence_ids"],
                     explanation=f"The bill is {ratio:.1f}x the typical amount for this procedure at comparable hospitals "
                                 f"({c.f.get('bill_peer_n')} comparable claims, {c.f.get('bill_peer_basis')} level).")


def nominee_change(c: Ctx):
    days = c.f.get("nominee_change_days_before_incident")
    if days is None or days > c.T.nominee_change_days:
        return None
    timeline = c.box.call("get_person_timeline", customer_id=c.claim["customer_id"], as_of=c.f["filed_date"], limit=100)
    event = c.f.get("nominee_change_event_id")
    non_family = bool(c.f.get("nominee_change_to_non_family"))
    return Indicator(code="NOMINEE_CHANGE_NEAR_EVENT", confidence=1.0 if non_family else 0.35,
                     evidence_ids=[c.claim_id] + ([event] if event in timeline["evidence_ids"] else []),
                     explanation=f"The nominee was changed {days} days before the death, to a {'non-family member' if non_family else 'family member'}.")


def document_dates(c: Ctx):
    gap = c.f.get("doc_date_gap_days")
    if gap is None or gap < c.T.doc_date_gap_days:
        return None
    return Indicator(code="DOC_DATE_MISMATCH", confidence=min(1.0, gap / 14), evidence_ids=[c.claim_id] + c.doc_ids("death_certificate", "claim_form"),
                     explanation=f"The date of death on the claim form and on the death certificate differ by {gap} days.")


def repeat_incident(c: Ctx):
    other = c.f.get("same_vehicle_same_date_claim")
    if not other:
        return None
    oid = c.other_claim(other)
    same_garage = bool(c.f.get("same_vehicle_same_garage"))
    return Indicator(code="REPEAT_INCIDENT_CLAIM", confidence=1.0, evidence_ids=[c.claim_id, oid],
                     explanation=f"The same vehicle was already claimed for an accident on the same date in {oid}" + (", at the same garage." if same_garage else "."))


def prior_damage(c: Ctx):
    overlap = c.f.get("prior_inspection_overlap_items") or 0
    if overlap < c.T.prior_overlap_items or c.f.get("repaired_since_inspection") or not c.claim["vehicle_token"]:
        return None
    inspections = c.box.call("get_vehicle_inspections", vehicle_token=c.claim["vehicle_token"], as_of=c.f["filed_date"], limit=100)
    event = c.f.get("prior_inspection_event_id")
    if event not in inspections["evidence_ids"]:
        return None
    return Indicator(code="DAMAGE_IN_PRIOR_INSPECTION", confidence=1.0, evidence_ids=[c.claim_id, event],
                     explanation=f"{overlap} of the claimed damaged parts were already recorded at an inspection "
                                 f"{c.f.get('prior_inspection_days_before')} days before the accident, and no repair is on record.")


def non_disclosure(c: Ctx):
    n, age = c.f.get("prior_same_condition_before_start") or 0, c.f.get("policy_age_days")
    if not n or c.f.get("declared_has_condition") or age is None or age > c.T.non_disclosure_days:
        return None
    pid = c.other_claim(c.f["prior_condition_claim_id"])
    return Indicator(code="PRE_EXISTING_NEAR_INCEPTION", confidence=1.0, evidence_ids=[c.claim_id, pid],
                     explanation=f"This {c.f.get('condition_tag')} claim came {age} days after the policy started, and an earlier claim ({pid}) "
                                 "for the same condition exists that was not declared.")


def early_death(c: Ctx):
    days, n = c.f.get("days_policy_start_to_incident"), c.f.get("serious_health_claims_before_start") or 0
    if days is None or days > c.T.early_death_days or not n:
        return None
    sid = c.other_claim(c.f["serious_health_claim_id"])
    accident = "fir" in c.docs  # a police report on a death claim means a documented accident, which a hidden illness cannot explain
    return Indicator(code="EARLY_DEATH_VS_HEALTH_HISTORY", confidence=0.2 if accident else 1.0, evidence_ids=[c.claim_id, sid],
                     counter_evidence=["The death is documented as an accident (police report on file)."] if accident else [],
                     explanation=f"Death came {days} days after the policy started, and the customer had an earlier serious health claim ({sid}) "
                                 "from before the policy began." + (" The death is documented as an accident, so this is weak evidence." if accident else ""))


def cross_policy(c: Ctx):
    linked = c.f.get("linked_claim_id")
    if not linked:
        return None
    before, gap, mismatch = c.f.get("admit_before_linked_incident_days") or 0, abs(c.f.get("linked_incident_gap_days") or 0), bool(c.f.get("linked_district_mismatch"))
    if not (before >= 1 or gap >= 3 or mismatch):
        return None
    oid = c.other_claim(linked)
    parts = []
    if before >= 1:
        parts.append(f"the hospital admission was {before} days before the accident claimed under the motor policy")
    if gap >= 3:
        parts.append(f"the two claims give accident dates {gap} days apart")
    if mismatch:
        parts.append("the two claims give different places for the accident")
    return Indicator(code="CROSS_POLICY_INCIDENT_MISMATCH", confidence=1.0, evidence_ids=[c.claim_id, oid],
                     explanation=f"The same accident is claimed under two policies (see {oid}), but " + "; ".join(parts) + ".")


def shared_payout(c: Ctx):
    n = c.f.get("ring_unrelated_customers") or 0
    if n < c.T.ring_unrelated_min:
        return None
    matches = [m for m in c.similar() if any("same_account" in r for r in m["reasons"])]
    return Indicator(code="SHARED_ENTITY_CLUSTER", confidence=min(1.0, n / 4), evidence_ids=[c.claim_id] + [m["claim_id"] for m in matches[:10]],
                     explanation=f"{n} unrelated customers had claims paid to the same bank account in the last 90 days "
                                 f"({c.f.get('ring_same_provider_claims_90d') or 0} at the same garage).")


def area(c: Ctx):
    if not c.f.get("area_is_high"):
        return None
    signal = c.box.call("get_area_signal", claim_id=c.claim_id)["records"][0]
    return Indicator(code="HIGH_FRAUD_AREA", severity="low", confidence=1.0, evidence_ids=[c.claim_id], explanation=signal["reason"])


def cover_vs_income(c: Ctx):
    ratio = c.f.get("sum_insured_to_income_ratio")
    if ratio is None or ratio < c.T.cover_vs_income_ratio[c.f["line"]]:
        return None
    policy = c.box.call("get_policy", policy_id=c.f["policy_id"])
    return Indicator(code="COVER_VS_INCOME_MISMATCH", severity="low", confidence=1.0, evidence_ids=[c.claim_id] + policy["evidence_ids"],
                     explanation=f"The sum insured is {ratio:.0f}x the midpoint of the customer's income band.")


RULES = [("duplicate claim", duplicate), ("bill vs peers", bill_outlier), ("nominee change", nominee_change), ("document dates", document_dates),
         ("repeat incident", repeat_incident), ("prior damage", prior_damage), ("non-disclosure", non_disclosure), ("early death", early_death),
         ("cross-policy accident", cross_policy), ("shared payout account", shared_payout), ("area history", area), ("cover vs income", cover_vs_income)]


def missing_documents(c: Ctx) -> list[MissingItem]:
    """Required documents that are not on file. Which documents are required depends on the claim (see weights.yaml thresholds)."""
    f, T = c.f, c.T
    need: list[tuple[str, str, bool]] = []  # (document, reason, is_on_file)
    on = lambda d: d in c.docs  # noqa: E731
    if f["line"] == "health":
        need += [("bill", "every health claim needs the original bill", on("bill")),
                 ("discharge_summary", "an inpatient claim needs the discharge summary", on("discharge_summary"))]
        if (f.get("bill_vs_peer_median") or 0) >= T.itemised_bill_ratio:
            need.append(("itemised_bill", "the bill is far above comparable claims", on("itemised_bill")))
        if f.get("linked_claim_id"):
            need.append(("fir", "the linked motor claim has no police report confirming the accident", bool(f.get("linked_doc_fir"))))
    elif f["line"] == "motor":
        need += [("repair_estimate", "every motor claim needs a repair estimate", on("repair_estimate")),
                 ("fir", "an accident claim needs the police report", on("fir")),
                 ("inspection_report", "the claimed damage needs an inspection report", on("inspection_report"))]
        if (f.get("payout_shared_customers_90d") or 0) >= T.shared_payout_min_for_registration:
            need.append(("provider_registration", "the payout account is shared with other customers' claims", on("provider_registration")))
    else:
        need += [("death_certificate", "a death claim needs the death certificate", on("death_certificate")),
                 ("claim_form", "a death claim needs the claim form", on("claim_form")),
                 ("cause_of_death", "a death claim needs the cause-of-death report", on("cause_of_death"))]
        days = f.get("nominee_change_days_before_incident")
        if days is not None and days <= T.nominee_consent_days:
            need.append(("nominee_consent", "the nominee was changed shortly before the death", on("nominee_consent")))
    return [MissingItem(document=d, reason=why) for d, why, present in need if not present]
