# Checklist: cross-policy (the person holds more than one policy)

Additional allowed indicators: CROSS_POLICY_INCIDENT_MISMATCH, EARLY_DEATH_VS_HEALTH_HISTORY, PRE_EXISTING_NEAR_INCEPTION.
Look across this person's other policies and claims before concluding.

1. **CROSS_POLICY_INCIDENT_MISMATCH**: `linked_claim_id` is set: the same accident is claimed under another policy. It applies when the hospital admission was before the accident
   (`admit_before_linked_incident_days` at least 1), the two claims give accident dates at least 3 days apart (`linked_incident_gap_days`), or they give different places
   (`linked_district_mismatch`). Call `get_claim` on the linked claim and cite both claims. Confidence 1.0 when the condition is clearly met. A gap of 1-2 days with matching places is not enough.
2. **EARLY_DEATH_VS_HEALTH_HISTORY** (term claim): death within 180 days of the policy start and an earlier serious health claim from before the policy began.
   Use `get_person_timeline` and `get_claim` to cite the serious claim. A documented accident (police report on file) makes the signal weak (confidence 0.2).
3. **PRE_EXISTING_NEAR_INCEPTION** (health claim): an earlier claim for the same condition exists on another (often lapsed) policy, the condition was not declared on this policy,
   and the claim came within 90 days of its start. Cite both claims. If the earlier claim is for an unrelated condition, do not report it.
4. Also use `find_shared_entities` and `get_customer_summary` to see how the person's policies, claims and contacts connect.
