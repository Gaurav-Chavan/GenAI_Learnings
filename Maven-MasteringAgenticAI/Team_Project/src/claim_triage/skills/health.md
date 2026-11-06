# Checklist: health claim

Allowed indicators: DUPLICATE_CLAIM, BILL_OUTLIER_VS_PEERS, PRE_EXISTING_NEAR_INCEPTION, SHARED_ENTITY_CLUSTER, HIGH_FRAUD_AREA, COVER_VS_INCOME_MISMATCH.

1. **DUPLICATE_CLAIM**: call `find_similar_claims`. It applies when an earlier claim has the same provider and procedure, admission dates within 3 days
   and an amount within 5% (reason `near_duplicate_...`), or the same invoice number (`same_invoice_number`). Same invoice number: confidence 1.0 and
   `hard_rule` true. Near-duplicate with a different invoice: about 0.85. Counter-evidence: a different procedure or dates, or a follow-up treatment weeks later.
   Cite this claim and the earlier claim.
2. **BILL_OUTLIER_VS_PEERS**, two ways it applies:
   a. The fact `bill_vs_peer_median` is at least 2.0. Confidence about (ratio - 1) / 2, capped at 1 (2.0 gives 0.5, 3.0 or more gives 1.0).
      Call `get_provider_stats` for the provider and cite this claim and the provider. Then call `get_claim_documents` and read the discharge summary.
      If it documents a MEDICAL reason that explains the higher cost (a complication, an ICU stay, an additional procedure), lower the confidence to 0.15 or less,
      quote that sentence in `counter_evidence` and cite its DOC ID. A reason that is not medical (a private room, premium services, family requests) does not
      explain a bill this large: keep the confidence. Other counter-evidence: a complex procedure priced normally against peers.
   b. The ratio is between 1.4 and 2.0 AND the `search_knowledge` result contains a `history` record showing at least 3 claims at this provider confirmed as fraud
      in the last 24 months. Report it with confidence 0.35 and cite this claim and that provider's ID. Without such a record, a ratio below 2.0 is not an indicator.
3. **PRE_EXISTING_NEAR_INCEPTION**: `prior_same_condition_before_start` is at least 1, `declared_has_condition` is false, and `policy_age_days` is at most 90.
   Call `get_claim` on `prior_condition_claim_id` and cite both claims. Counter-evidence: the condition was declared.
4. **SHARED_ENTITY_CLUSTER**: call `find_shared_entities` for the customer. It applies when at least 3 unrelated customers (not household members) had claims paid to the
   same account in the last 90 days. Confidence about min(1, unrelated / 4). Cite the claims it lists.
5. **HIGH_FRAUD_AREA**: call `get_area_signal`; if `is_high` is true report it with confidence 1.0 and cite this claim.
6. **COVER_VS_INCOME_MISMATCH**: `sum_insured_to_income_ratio` at least 20: confidence 1.0; cite this claim and the policy (`get_policy`).

Required documents for a health claim: bill, discharge_summary; itemised_bill when the bill ratio is at least 1.8.
