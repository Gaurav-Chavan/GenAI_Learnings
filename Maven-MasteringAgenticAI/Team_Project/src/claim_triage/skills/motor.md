# Checklist: motor claim

Allowed indicators: REPEAT_INCIDENT_CLAIM, DAMAGE_IN_PRIOR_INSPECTION, SHARED_ENTITY_CLUSTER, HIGH_FRAUD_AREA, COVER_VS_INCOME_MISMATCH.

1. **REPEAT_INCIDENT_CLAIM**: `same_vehicle_same_date_claim` is set: an earlier claim on the same vehicle gives the same accident date (within 1 day). Call `find_similar_claims`
   and cite this claim and the earlier claim. Confidence 1.0. Counter-evidence: clearly separate accidents on different dates.
2. **DAMAGE_IN_PRIOR_INSPECTION**: `prior_inspection_overlap_items` is at least 2 and `repaired_since_inspection` is false: the claimed damaged parts were already recorded at an earlier
   inspection and no repair is on record. Call `get_claim` for the vehicle token, then `get_vehicle_inspections` and cite this claim and the inspection event (`prior_inspection_event_id`).
   Confidence 1.0. Counter-evidence: a repair recorded between the inspection and the accident.
3. **SHARED_ENTITY_CLUSTER**: call `find_shared_entities` for the customer. It applies when at least 3 unrelated customers (not household members) had claims paid to the same
   account in the last 90 days. Confidence about min(1, unrelated / 4). Relatives sharing a phone and address are a household, not a ring. Cite the claims it lists.
4. **HIGH_FRAUD_AREA**: call `get_area_signal`; if `is_high` is true report it with confidence 1.0 and cite this claim.
5. **COVER_VS_INCOME_MISMATCH**: `sum_insured_to_income_ratio` at least 20: confidence 1.0; cite this claim and the policy.

Required documents for a motor claim: repair_estimate, fir, inspection_report; provider_registration when the payout account is shared with other customers' claims.
