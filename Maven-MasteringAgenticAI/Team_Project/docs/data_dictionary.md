# Data dictionary

Generated from the silver and gold schemas by `claim_triage.data.dictionary`. Gold features are computed as of each claim's filing date.

## silver

### silver.areas (25 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| pincode | INTEGER | backend only |  | PIN code. Backend only: used to compute area risk, never shown to reviewers |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| state | VARCHAR | reviewer / agents |  | State (hidden when the reviewer view is fully generalised) |
| urban_rural | VARCHAR | reviewer / agents |  | Urban, semi-urban or rural class of the area |

### silver.claims (2,253 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| claim_id | VARCHAR | reviewer / agents |  |  |
| policy_id | VARCHAR | reviewer / agents |  |  |
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| line | VARCHAR | reviewer / agents |  |  |
| filed_date | DATE | reviewer / agents |  |  |
| incident_date | DATE | reviewer / agents |  |  |
| admit_date | DATE | reviewer / agents |  |  |
| discharge_date | DATE | reviewer / agents |  |  |
| procedure_code | VARCHAR | reviewer / agents |  |  |
| condition_tag | VARCHAR | reviewer / agents |  |  |
| damage_items | VARCHAR | reviewer / agents |  |  |
| amount | BIGINT | reviewer / agents |  |  |
| provider_id | VARCHAR | reviewer / agents |  |  |
| invoice_no | VARCHAR | reviewer / agents |  |  |
| incident_district | VARCHAR | reviewer / agents |  |  |
| status | VARCHAR | reviewer / agents |  | Policy status: active, lapsed, or terminated (the insured died); claim status: open, pending or closed |
| decided_date | DATE | reviewer / agents |  |  |
| outcome | VARCHAR | reviewer / agents |  | Reviewer-confirmed result of a closed historical claim (confirmed_fraud or cleared); empty for open claims |
| payout_token | VARCHAR | token |  | Keyed-hash token of the account the claim is paid to |
| vehicle_token | VARCHAR | token |  | Keyed-hash token of the vehicle registration |
| cert_date | DATE | reviewer / agents |  |  |
| description | VARCHAR | reviewer / agents |  |  |
| source | VARCHAR | reviewer / agents |  | history = past claim; eval = claim to be assessed |
| case_id | VARCHAR | reviewer / agents |  | Case ID of a claim to be assessed (evaluation claims only) |

### silver.customers (600 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| age_band | VARCHAR | reviewer / agents |  | Age in coarse bands; no exact age or date of birth is stored outside the vault |
| gender | VARCHAR | reviewer (context) | never | Shown to reviewers as context; never used by the scorer; used only in fairness checks |
| occupation_category | VARCHAR | reviewer / agents |  | Occupation group |
| income_band | VARCHAR | reviewer / agents |  | Annual income band; feeds the cover-vs-income signal |
| state | VARCHAR | reviewer / agents |  | State (hidden when the reviewer view is fully generalised) |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| pincode | INTEGER | backend only |  | PIN code. Backend only: used to compute area risk, never shown to reviewers |
| urban_rural | VARCHAR | reviewer / agents |  | Urban, semi-urban or rural class of the area |
| channel | VARCHAR | reviewer / agents |  | How the customer joined: agent, online or bank |
| kyc_status | VARCHAR | reviewer / agents |  | KYC verification level |
| signup_date | DATE | reviewer / agents |  |  |
| tenure_band | VARCHAR | reviewer / agents |  | How long the customer has been with the insurer |
| phone_token | VARCHAR | token |  | Keyed-hash token of the phone number; equal numbers give equal tokens |
| email_token | VARCHAR | token |  | Keyed-hash token of the email address |
| address_token | VARCHAR | token |  | Keyed-hash token of the address |
| bank_token | VARCHAR | token |  | Keyed-hash token of the bank account |
| kyc_token | VARCHAR | token |  | Keyed-hash token of the KYC identifier |

### silver.documents (4,027 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| doc_id | VARCHAR | reviewer / agents |  |  |
| claim_id | VARCHAR | reviewer / agents |  |  |
| doc_type | VARCHAR | reviewer / agents |  |  |
| doc_date | DATE | reviewer / agents |  |  |
| text | VARCHAR | reviewer / agents |  | Document text with names, numbers and addresses scrubbed |

### silver.entity_tokens (3,257 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| token_type | VARCHAR | reviewer / agents |  |  |
| token | VARCHAR | token |  |  |
| source | VARCHAR | reviewer / agents |  | history = past claim; eval = claim to be assessed |
| first_seen | DATE | reviewer / agents |  |  |

### silver.events (12,850 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| event_id | VARCHAR | reviewer / agents |  |  |
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| policy_id | VARCHAR | reviewer / agents |  |  |
| claim_id | VARCHAR | reviewer / agents |  |  |
| event_type | VARCHAR | reviewer / agents |  |  |
| event_date | DATE | reviewer / agents |  |  |
| details | VARCHAR | reviewer / agents |  | JSON payload of the event with personal values replaced by tokens |

### silver.policies (1,406 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| policy_id | VARCHAR | reviewer / agents |  |  |
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| line | VARCHAR | reviewer / agents |  |  |
| start_date | DATE | reviewer / agents |  |  |
| end_date | DATE | reviewer / agents |  |  |
| sum_insured | BIGINT | reviewer / agents |  |  |
| premium | BIGINT | reviewer / agents |  |  |
| status | VARCHAR | reviewer / agents |  | Policy status: active, lapsed, or terminated (the insured died); claim status: open, pending or closed |
| lapse_date | DATE | reviewer / agents |  |  |
| nominee_relation | VARCHAR | reviewer / agents |  |  |
| nominee_token | VARCHAR | token |  | Keyed-hash token of the nominee's name |
| vehicle_token | VARCHAR | token |  | Keyed-hash token of the vehicle registration |
| vehicle_segment | VARCHAR | reviewer / agents |  |  |
| declared_conditions | VARCHAR | reviewer / agents |  |  |

### silver.procedures (13 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| code | VARCHAR | reviewer / agents |  |  |
| name | VARCHAR | reviewer / agents |  |  |
| category | VARCHAR | reviewer / agents |  |  |
| median_cost | BIGINT | reviewer / agents |  |  |
| is_complex | BOOLEAN | reviewer / agents |  |  |
| condition_tag | VARCHAR | reviewer / agents |  |  |

### silver.providers (72 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| provider_id | VARCHAR | reviewer / agents |  |  |
| name | VARCHAR | reviewer / agents |  |  |
| kind | VARCHAR | reviewer / agents |  |  |
| pincode | INTEGER | backend only |  | PIN code. Backend only: used to compute area risk, never shown to reviewers |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| reg_status | VARCHAR | reviewer / agents |  |  |

### silver.reviewer_customers (600 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| age_band | VARCHAR | reviewer / agents |  | Age in coarse bands; no exact age or date of birth is stored outside the vault |
| gender | VARCHAR | reviewer (context) | never | Shown to reviewers as context; never used by the scorer; used only in fairness checks |
| occupation_category | VARCHAR | reviewer / agents |  | Occupation group |
| income_band | VARCHAR | reviewer / agents |  | Annual income band; feeds the cover-vs-income signal |
| state | VARCHAR | reviewer / agents |  | State (hidden when the reviewer view is fully generalised) |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| urban_rural | VARCHAR | reviewer / agents |  | Urban, semi-urban or rural class of the area |
| channel | VARCHAR | reviewer / agents |  | How the customer joined: agent, online or bank |
| kyc_status | VARCHAR | reviewer / agents |  | KYC verification level |
| tenure_band | VARCHAR | reviewer / agents |  | How long the customer has been with the insurer |
| generalisation_level | INTEGER | reviewer / agents |  | 0 = exact district/gender/age shown; higher = more generalised so the group has at least k customers |

## gold

### gold.area_risk (75 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| pincode | BIGINT | backend only |  | PIN code. Backend only: used to compute area risk, never shown to reviewers |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| state | VARCHAR | reviewer / agents |  | State (hidden when the reviewer view is fully generalised) |
| line | VARCHAR | reviewer / agents |  |  |
| basis | VARCHAR | reviewer / agents |  |  |
| n_claims | BIGINT | reviewer / agents |  |  |
| n_pin_claims | BIGINT | reviewer / agents |  |  |
| rate | DOUBLE | reviewer / agents |  |  |
| portfolio_rate | DOUBLE | reviewer / agents |  |  |
| ratio | DOUBLE | reviewer / agents |  |  |
| is_high | BOOLEAN | reviewer / agents |  |  |

### gold.area_risk_district (18 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| line | VARCHAR | reviewer / agents |  |  |
| n_claims | BIGINT | reviewer / agents |  |  |
| confirmed_fraud | BIGINT | reviewer / agents |  |  |
| rate | DOUBLE | reviewer / agents |  |  |

### gold.claim_features (380 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| claim_id | VARCHAR | reviewer / agents |  |  |
| case_id | VARCHAR | reviewer / agents |  | Case ID of a claim to be assessed (evaluation claims only) |
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| policy_id | VARCHAR | reviewer / agents |  |  |
| line | VARCHAR | reviewer / agents |  |  |
| filed_date | DATE | reviewer / agents |  |  |
| amount | BIGINT | reviewer / agents |  |  |
| procedure_code | VARCHAR | reviewer / agents |  |  |
| condition_tag | VARCHAR | reviewer / agents |  |  |
| location_shown | VARCHAR | reviewer / agents |  |  |
| policy_age_days | BIGINT | reviewer / agents |  | Days from policy start to filing |
| claims_prior_12m | BIGINT | reviewer / agents |  |  |
| claims_prior_36m | BIGINT | reviewer / agents |  |  |
| active_policies_at_filing | BIGINT | reviewer / agents |  |  |
| sum_insured_to_income_ratio | DOUBLE | reviewer / agents | signal input | Sum insured divided by the midpoint of the income band; feeds the cover-vs-income signal |
| provider_unverified | BOOLEAN | reviewer / agents |  |  |
| days_since_phone_change | BIGINT | reviewer / agents |  |  |
| days_since_address_change | BIGINT | reviewer / agents |  |  |
| days_since_bank_change | BIGINT | reviewer / agents |  |  |
| days_since_nominee_change | BIGINT | reviewer / agents |  |  |
| area_basis | VARCHAR | reviewer / agents | signal input | Level the area rate is based on: pin, district or portfolio |
| area_n | BIGINT | reviewer / agents | signal input | Past closed claims behind the area rate (24-month window, decided before filing) |
| area_fraud | BIGINT | reviewer / agents | signal input |  |
| area_rate | DOUBLE | reviewer / agents | signal input | Confirmed-fraud rate for the area and line, shrunk toward the district rate when a PIN has few claims |
| area_portfolio_rate | DOUBLE | reviewer / agents | signal input |  |
| area_ratio | DOUBLE | reviewer / agents | signal input | Area rate divided by the portfolio rate |
| area_is_high | BOOLEAN | reviewer / agents | signal input | Area flag: ratio at or above the configured threshold with enough claims behind it |
| doc_bill | BOOLEAN | reviewer / agents |  |  |
| doc_itemised_bill | BOOLEAN | reviewer / agents |  |  |
| doc_discharge_summary | BOOLEAN | reviewer / agents |  |  |
| doc_repair_estimate | BOOLEAN | reviewer / agents |  |  |
| doc_fir | BOOLEAN | reviewer / agents |  |  |
| doc_inspection_report | BOOLEAN | reviewer / agents |  |  |
| doc_death_certificate | BOOLEAN | reviewer / agents |  |  |
| doc_claim_form | BOOLEAN | reviewer / agents |  |  |
| doc_nominee_consent | BOOLEAN | reviewer / agents |  |  |
| doc_cause_of_death | BOOLEAN | reviewer / agents |  |  |
| doc_correction_affidavit | BOOLEAN | reviewer / agents |  |  |
| doc_provider_registration | BOOLEAN | reviewer / agents |  |  |
| bill_vs_peer_median | DOUBLE | reviewer / agents |  | Claim amount divided by the median for the same procedure at other providers (point in time) |
| bill_peer_n | BIGINT | reviewer / agents |  |  |
| bill_peer_basis | VARCHAR | reviewer / agents |  |  |
| dup_candidate_claim_id | VARCHAR | reviewer / agents |  |  |
| dup_exact_invoice | BOOLEAN | reviewer / agents |  |  |
| prior_same_condition_before_start | BIGINT | reviewer / agents |  |  |
| prior_condition_claim_id | VARCHAR | reviewer / agents |  |  |
| declared_has_condition | BOOLEAN | reviewer / agents |  |  |
| nominee_change_event_id | VARCHAR | reviewer / agents |  |  |
| nominee_change_days_before_incident | BIGINT | reviewer / agents |  |  |
| nominee_change_to_non_family | BOOLEAN | reviewer / agents |  |  |
| days_policy_start_to_incident | BIGINT | reviewer / agents |  |  |
| serious_health_claims_before_start | BIGINT | reviewer / agents |  |  |
| serious_health_claim_id | VARCHAR | reviewer / agents |  |  |
| doc_date_gap_days | BIGINT | reviewer / agents |  |  |
| same_vehicle_claims_120d | BIGINT | reviewer / agents |  |  |
| same_vehicle_same_date_claim | VARCHAR | reviewer / agents |  |  |
| same_vehicle_same_garage | BOOLEAN | reviewer / agents |  |  |
| prior_inspection_days_before | BIGINT | reviewer / agents |  |  |
| prior_inspection_overlap_items | BIGINT | reviewer / agents |  |  |
| repaired_since_inspection | BOOLEAN | reviewer / agents |  |  |
| prior_inspection_event_id | VARCHAR | reviewer / agents |  |  |
| payout_shared_customers_90d | BIGINT | reviewer / agents |  |  |
| ring_unrelated_customers | BIGINT | reviewer / agents |  |  |
| ring_same_provider_claims_90d | BIGINT | reviewer / agents |  |  |
| shared_phone_unrelated | BIGINT | reviewer / agents |  |  |
| shared_phone_related | BIGINT | reviewer / agents |  |  |
| linked_claim_id | VARCHAR | reviewer / agents |  |  |
| linked_incident_gap_days | BIGINT | reviewer / agents |  |  |
| admit_before_linked_incident_days | BIGINT | reviewer / agents |  |  |
| linked_district_mismatch | BOOLEAN | reviewer / agents |  |  |
| linked_doc_fir | BOOLEAN | reviewer / agents |  |  |

### gold.customer_360 (600 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| customer_id | VARCHAR | reviewer / agents |  | Opaque customer ID (CUST-000123), numbered in random order |
| age_band | VARCHAR | reviewer / agents |  | Age in coarse bands; no exact age or date of birth is stored outside the vault |
| gender | VARCHAR | reviewer (context) | never | Shown to reviewers as context; never used by the scorer; used only in fairness checks |
| occupation_category | VARCHAR | reviewer / agents |  | Occupation group |
| income_band | VARCHAR | reviewer / agents |  | Annual income band; feeds the cover-vs-income signal |
| state | VARCHAR | reviewer / agents |  | State (hidden when the reviewer view is fully generalised) |
| district | VARCHAR | reviewer / agents |  | District; in the reviewer view it falls back to the state when a group would be too small |
| urban_rural | VARCHAR | reviewer / agents |  | Urban, semi-urban or rural class of the area |
| channel | VARCHAR | reviewer / agents |  | How the customer joined: agent, online or bank |
| kyc_status | VARCHAR | reviewer / agents |  | KYC verification level |
| tenure_band | VARCHAR | reviewer / agents |  | How long the customer has been with the insurer |
| policies_total | BIGINT | reviewer / agents |  |  |
| policies_active | BIGINT | reviewer / agents |  |  |
| policies_lapsed | BIGINT | reviewer / agents |  |  |
| lines | VARCHAR | reviewer / agents |  |  |
| deceased | BOOLEAN | reviewer / agents |  | True when the customer has died: every policy is terminated on the death date and no later activity exists |
| total_sum_insured | BIGINT | reviewer / agents |  |  |
| claims_total | BIGINT | reviewer / agents |  |  |
| claims_last_12m | BIGINT | reviewer / agents |  |  |
| claims_last_36m | BIGINT | reviewer / agents |  |  |
| claimed_amount_36m | BIGINT | reviewer / agents |  |  |
| last_claim_date | DATE | reviewer / agents |  |  |
| nominee_changes | BIGINT | reviewer / agents |  |  |
| contact_changes | BIGINT | reviewer / agents |  |  |
| customers_sharing_phone | BIGINT | reviewer / agents |  |  |
| customers_sharing_bank | BIGINT | reviewer / agents |  |  |
| customers_sharing_address | BIGINT | reviewer / agents |  |  |

### gold.meta (3 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| key | VARCHAR | reviewer / agents |  |  |
| value | VARCHAR | reviewer / agents |  |  |

### gold.provider_stats (449 rows)

| Column | Type | Visibility | Scored | Meaning |
|---|---|---|---|---|
| provider_id | VARCHAR | reviewer / agents |  |  |
| procedure_code | VARCHAR | reviewer / agents |  |  |
| n_claims | BIGINT | reviewer / agents |  |  |
| median_amount | DOUBLE | reviewer / agents |  |  |
| max_amount | BIGINT | reviewer / agents |  |  |
