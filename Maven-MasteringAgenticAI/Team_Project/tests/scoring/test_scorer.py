"""The scorer is plain arithmetic: these tests pin down the rules in docs/DESIGN.md section 2."""
import pytest

from claim_triage.contracts import Indicator, MissingItem
from claim_triage.scoring.scorer import band_of, load_weights, score

W = load_weights()


def ind(code, conf=1.0, evidence=("CLM-000001",), hard=False):
    return Indicator(code=code, confidence=conf, evidence_ids=list(evidence), explanation=f"{code} fired", hard_rule=hard)


def final(indicators, missing=()):
    return score("CLM-000001", "CASE-0001", list(indicators), list(missing), W)


def test_band_boundaries_match_the_agreed_thresholds():
    assert band_of(0.2999, W) == "red" and band_of(0.30, W) == "amber"
    assert band_of(0.80, W) == "amber" and band_of(0.8001, W) == "green"


def test_noisy_or_combines_signals_with_diminishing_returns():
    r = final([ind("HIGH_FRAUD_AREA"), ind("BILL_OUTLIER_VS_PEERS", conf=0.5)])
    assert r.trust_score == pytest.approx((1 - 0.25) * (1 - 0.45), abs=1e-4)
    assert r.band == "amber"


def test_a_strong_confirmed_indicator_is_red_and_no_indicator_is_green():
    assert final([ind("DUPLICATE_CLAIM")]).band == "red"
    assert final([]).band == "green" and final([]).trust_score == 1.0


def test_the_area_signal_alone_is_review_needed_never_red():
    r = final([ind("HIGH_FRAUD_AREA")])
    assert r.band == "amber" and r.trust_score == pytest.approx(0.75)


def test_weak_signals_can_stack_into_review_needed():
    r = final([ind("HIGH_FRAUD_AREA"), ind("COVER_VS_INCOME_MISMATCH")])
    assert r.band == "amber" and r.trust_score == pytest.approx(0.6375, abs=1e-4)


def test_a_hard_rule_caps_trust_at_red_even_with_low_confidence():
    r = final([ind("DUPLICATE_CLAIM", conf=0.1, hard=True)])
    assert r.trust_score <= W["caps"]["hard_rule_trust_max"] and r.band == "red"


def test_missing_evidence_can_never_be_green_and_offers_a_counterfactual():
    r = final([], [MissingItem(document="bill", reason="every health claim needs the original bill")])
    assert r.trust_score == 0.8 and r.band == "amber" and r.evidence_status == "insufficient"
    assert "original bill" in r.counterfactual and r.outstanding_checks


def test_missing_evidence_does_not_hide_a_red():
    r = final([ind("DUPLICATE_CLAIM")], [MissingItem(document="bill", reason="x")])
    assert r.band == "red" and r.counterfactual is None


def test_an_indicator_without_verified_evidence_is_ignored_and_reported():
    r = final([ind("DUPLICATE_CLAIM", evidence=())])
    assert r.band == "green" and r.dropped_indicators == ["DUPLICATE_CLAIM"] and r.indicators == []


def test_green_is_never_worded_as_an_approval_or_as_proof():
    text = final([]).reasons[0].lower()
    assert "not an approval" in text and "fraud proven" not in text


def test_reasons_are_ordered_by_contribution():
    r = final([ind("HIGH_FRAUD_AREA"), ind("DUPLICATE_CLAIM")])
    assert r.reasons[0].startswith("DUPLICATE_CLAIM") and r.indicators[0].code == "DUPLICATE_CLAIM"
