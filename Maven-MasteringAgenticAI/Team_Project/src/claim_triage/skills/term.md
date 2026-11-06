# Checklist: term life (death) claim

Allowed indicators: NOMINEE_CHANGE_NEAR_EVENT, DOC_DATE_MISMATCH, EARLY_DEATH_VS_HEALTH_HISTORY, SHARED_ENTITY_CLUSTER, HIGH_FRAUD_AREA, COVER_VS_INCOME_MISMATCH.

1. **NOMINEE_CHANGE_NEAR_EVENT**: `nominee_change_days_before_incident` is at most 45. Call `get_person_timeline` (as_of the filing date) and cite the `nominee_changed`
   event ID (also available as `nominee_change_event_id`) and this claim. Change to a non-family member: confidence 1.0. Change to a family member: about 0.35.
2. **DOC_DATE_MISMATCH**: `doc_date_gap_days` is at least 7: the date of death on the claim form and on the death certificate differ. Confidence about min(1, gap / 14).
   Call `get_claim` and cite this claim and the death certificate and claim form document IDs. Counter-evidence: a correction affidavit on file.
3. **EARLY_DEATH_VS_HEALTH_HISTORY**: `days_policy_start_to_incident` is at most 180 and `serious_health_claims_before_start` is at least 1. Call `get_claim` on
   `serious_health_claim_id` and cite both claims. If the death is documented as an accident (a police report, `doc_fir`, is on file) the signal is weak: confidence 0.2 and say so in counter_evidence.
4. **SHARED_ENTITY_CLUSTER**, **HIGH_FRAUD_AREA**, **COVER_VS_INCOME_MISMATCH**: as in the motor and health checklists (cover mismatch applies at a sum-insured ratio of at least 30 for term).

Required documents for a death claim: death_certificate, claim_form, cause_of_death; nominee_consent when the nominee was changed within 90 days of the death.
