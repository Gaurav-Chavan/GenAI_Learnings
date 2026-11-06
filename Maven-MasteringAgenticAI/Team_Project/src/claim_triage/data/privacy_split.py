"""Privacy split: the ONLY step that reads personal data.

raw.duckdb  ->  vault.duckdb   (real-looking identity; nothing in the app may read it)
            ->  silver.duckdb  (tokens + coarse bands; the only layer the app and agents see)
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter

import duckdb

from .config import paths, when
from .generate import LABEL_COLUMNS, TABLES, write_table  # noqa: F401  (TABLES reused for pass-through tables)
from .reference import age_band, income_band, tenure_band
from .tokens import make_token, token_key

SILVER_SPECS = {
    "customers": [("customer_id", "VARCHAR"), ("age_band", "VARCHAR"), ("gender", "VARCHAR"), ("occupation_category", "VARCHAR"),
                  ("income_band", "VARCHAR"), ("state", "VARCHAR"), ("district", "VARCHAR"), ("pincode", "INTEGER"),
                  ("urban_rural", "VARCHAR"), ("channel", "VARCHAR"), ("kyc_status", "VARCHAR"), ("signup_date", "DATE"),
                  ("tenure_band", "VARCHAR"), ("phone_token", "VARCHAR"), ("email_token", "VARCHAR"), ("address_token", "VARCHAR"),
                  ("bank_token", "VARCHAR"), ("kyc_token", "VARCHAR")],
    "policies": [("policy_id", "VARCHAR"), ("customer_id", "VARCHAR"), ("line", "VARCHAR"), ("start_date", "DATE"), ("end_date", "DATE"),
                 ("sum_insured", "BIGINT"), ("premium", "BIGINT"), ("status", "VARCHAR"), ("lapse_date", "DATE"),
                 ("nominee_relation", "VARCHAR"), ("nominee_token", "VARCHAR"), ("vehicle_token", "VARCHAR"),
                 ("vehicle_segment", "VARCHAR"), ("declared_conditions", "VARCHAR")],
    "claims": [("claim_id", "VARCHAR"), ("policy_id", "VARCHAR"), ("customer_id", "VARCHAR"), ("line", "VARCHAR"), ("filed_date", "DATE"),
               ("incident_date", "DATE"), ("admit_date", "DATE"), ("discharge_date", "DATE"), ("procedure_code", "VARCHAR"),
               ("condition_tag", "VARCHAR"), ("damage_items", "VARCHAR"), ("amount", "BIGINT"), ("provider_id", "VARCHAR"),
               ("invoice_no", "VARCHAR"), ("incident_district", "VARCHAR"), ("status", "VARCHAR"), ("decided_date", "DATE"),
               ("outcome", "VARCHAR"), ("payout_token", "VARCHAR"), ("vehicle_token", "VARCHAR"), ("cert_date", "DATE"),
               ("description", "VARCHAR"), ("source", "VARCHAR"), ("case_id", "VARCHAR")],
    "events": [("event_id", "VARCHAR"), ("customer_id", "VARCHAR"), ("policy_id", "VARCHAR"), ("claim_id", "VARCHAR"),
               ("event_type", "VARCHAR"), ("event_date", "DATE"), ("details", "VARCHAR")],
    "documents": [("doc_id", "VARCHAR"), ("claim_id", "VARCHAR"), ("doc_type", "VARCHAR"), ("doc_date", "DATE"), ("text", "VARCHAR")],
    "providers": TABLES["providers"],
    "areas": TABLES["areas"],
    "procedures": TABLES["procedures"],
    "entity_tokens": [("customer_id", "VARCHAR"), ("token_type", "VARCHAR"), ("token", "VARCHAR"), ("source", "VARCHAR"), ("first_seen", "DATE")],
    "reviewer_customers": [("customer_id", "VARCHAR"), ("age_band", "VARCHAR"), ("gender", "VARCHAR"), ("occupation_category", "VARCHAR"),
                           ("income_band", "VARCHAR"), ("state", "VARCHAR"), ("district", "VARCHAR"), ("urban_rural", "VARCHAR"),
                           ("channel", "VARCHAR"), ("kyc_status", "VARCHAR"), ("tenure_band", "VARCHAR"), ("generalisation_level", "INTEGER")],
}
VAULT_SPECS = {
    "id_map": [("raw_id", "INTEGER"), ("customer_id", "VARCHAR")],
    "customers_pii": [("customer_id", "VARCHAR"), ("name", "VARCHAR"), ("phone", "VARCHAR"), ("email", "VARCHAR"), ("address", "VARCHAR"),
                      ("dob", "DATE"), ("bank_account", "VARCHAR"), ("kyc_id", "VARCHAR"), ("pincode", "INTEGER")],
    "pii_values": [("customer_id", "VARCHAR"), ("kind", "VARCHAR"), ("value", "VARCHAR")],
}
COARSE_AGE = {"18-29": "<40", "30-39": "<40", "40-49": "40+", "50-59": "40+", "60+": "40+"}
GENERIC = [(re.compile(r"\+91\d{10}"), "[PHONE]"), (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"), (re.compile(r"\b\d{12}\b"), "[ACCOUNT]")]


def fetch(con, table: str) -> list[dict]:
    cur = con.execute(f"SELECT * FROM {table}")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


class Scrubber:
    """Replaces a customer's personal values in free text. Vehicle registrations become their token, so text stays linkable."""

    def __init__(self, c: dict, nominees: set[str], vehicles: dict[str, str]):
        pairs = [(c["name"], "[CUSTOMER]"), (c["email"], "[EMAIL]"), (c["phone"], "[PHONE]"), (c["address"], "[ADDRESS]"),
                 (c["bank_account"], "[ACCOUNT]"), (c["kyc_id"], "[KYC]")] + [(n, "[NOMINEE]") for n in nominees if n]
        pairs += [(n, "[CUSTOMER]") for n in (c.get("former_names") or "").split("|") if n]  # a name the customer used earlier is still their name
        pairs += list(vehicles.items())
        self.pairs = sorted(pairs, key=lambda p: -len(p[0]))

    def __call__(self, text: str | None) -> str | None:
        if text is None:
            return None
        for old, new in self.pairs:
            text = text.replace(old, new)
        for rx, new in GENERIC:
            text = rx.sub(new, text)
        return text


def reviewer_view(customers: list[dict], k: int) -> tuple[dict, dict]:
    """Generalise location / age / gender until every (location, gender, age) group has >= k customers."""
    def shown(c, level):
        if level >= 4:
            return ("withheld", "withheld", "withheld")
        loc = c["district"] if level == 0 else c["state"]
        age = c["age_band"] if level < 2 else COARSE_AGE[c["age_band"]]
        return (loc, c["gender"] if level < 3 else "withheld", age)

    levels = {c["customer_id"]: 0 for c in customers}
    for _ in range(5):
        groups = Counter(shown(c, levels[c["customer_id"]]) for c in customers)
        small = [c["customer_id"] for c in customers
                 if groups[shown(c, levels[c["customer_id"]])] < k and levels[c["customer_id"]] < 4]
        if not small:
            break
        for cid in small:
            levels[cid] += 1
    rows = []
    for c in customers:
        lvl = levels[c["customer_id"]]
        loc, gender, age = shown(c, lvl)
        rows.append(dict(customer_id=c["customer_id"], age_band=age, gender=gender, occupation_category=c["occupation_category"],
                         income_band=c["income_band"], state=c["state"] if lvl < 4 else "withheld",
                         district=c["district"] if lvl == 0 else "(state level)" if lvl < 4 else "withheld",
                         urban_rural=c["urban_rural"], channel=c["channel"], kyc_status=c["kyc_status"], tenure_band=c["tenure_band"],
                         generalisation_level=lvl))
    # fully withheld customers show no location, gender or age at all, so they form no identifying group
    final = Counter((r["state"], r["district"], r["gender"], r["age_band"]) for r in rows if r["generalisation_level"] < 4)
    full = Counter((c["district"], c["gender"], c["age_band"], c["occupation_category"], c["income_band"]) for c in customers)
    stats = dict(level_counts=dict(Counter(levels.values())), min_group=min(final.values()) if final else None,
                 full_qi_share_below_k=round(sum(1 for c in customers if full[(c["district"], c["gender"], c["age_band"], c["occupation_category"], c["income_band"])] < k) / len(customers), 3))
    return {r["customer_id"]: r for r in rows}, stats


def run_privacy_split(cfg: dict) -> dict:
    p = paths(cfg)
    for name in ("vault", "silver"):
        p[name].unlink(missing_ok=True)
    raw = duckdb.connect(str(p["raw"]), read_only=True)
    key, today = token_key(cfg["seed"]), when(cfg, "dataset_today")
    tok = lambda kind, value: make_token(key, kind, value) if value else None  # noqa: E731

    customers = fetch(raw, "customers")
    policies, claims, events, docs = fetch(raw, "policies"), fetch(raw, "claims"), fetch(raw, "events"), fetch(raw, "documents")
    areas = {a["pincode"]: a for a in fetch(raw, "areas")}

    # opaque customer ids, numbered in random order so the number does not reveal signup order
    rng = random.Random(cfg["seed"] + 1)
    numbers = list(range(1, len(customers) + 1))
    rng.shuffle(numbers)
    cid_of = {c["raw_id"]: f"CUST-{numbers[i]:06d}" for i, c in enumerate(sorted(customers, key=lambda c: c["raw_id"]))}

    nominees: dict[int, set[str]] = {c["raw_id"]: set() for c in customers}
    for pol in policies:
        if pol["nominee_name"]:
            nominees[pol["raw_id"]].add(pol["nominee_name"])
    event_details = {}
    for e in events:
        d = json.loads(e["details"])
        event_details[e["event_id"]] = d
        if d.get("new_nominee_name"):
            nominees[e["raw_id"]].add(d["new_nominee_name"])
    vehicles: dict[int, dict[str, str]] = {c["raw_id"]: {} for c in customers}
    for pol in policies:
        if pol["vehicle_reg"]:
            vehicles[pol["raw_id"]][pol["vehicle_reg"]] = tok("vehicle", pol["vehicle_reg"])
    scrub = {c["raw_id"]: Scrubber(c, nominees[c["raw_id"]], vehicles[c["raw_id"]]) for c in customers}
    claim_owner = {cl["claim_id"]: cl["raw_id"] for cl in claims}

    # ---- silver tables
    s_customers, s_tokens, vault_pii, vault_values = [], [], [], []
    for c in customers:
        cid = cid_of[c["raw_id"]]
        age = (today - c["dob"]).days // 365
        tokens = dict(phone=tok("phone", c["phone"]), email=tok("email", c["email"]), address=tok("address", c["address"]),
                      bank=tok("bank", c["bank_account"]), kyc=tok("kyc", c["kyc_id"]))
        s_customers.append(dict(customer_id=cid, age_band=age_band(age), gender=c["gender"], occupation_category=c["occupation"],
                                income_band=income_band(c["annual_income"]), state=areas[c["pincode"]]["state"],
                                district=areas[c["pincode"]]["district"], pincode=c["pincode"], urban_rural=areas[c["pincode"]]["urban_rural"],
                                channel=c["channel"], kyc_status=c["kyc_status"], signup_date=c["signup_date"],
                                tenure_band=tenure_band(c["signup_date"], today), phone_token=tokens["phone"], email_token=tokens["email"],
                                address_token=tokens["address"], bank_token=tokens["bank"], kyc_token=tokens["kyc"]))
        for kind, t in tokens.items():
            s_tokens.append(dict(customer_id=cid, token_type=kind, token=t, source="profile", first_seen=c["signup_date"]))
        vault_pii.append(dict(customer_id=cid, name=c["name"], phone=c["phone"], email=c["email"], address=c["address"], dob=c["dob"],
                              bank_account=c["bank_account"], kyc_id=c["kyc_id"], pincode=c["pincode"]))
        for kind in ("name", "phone", "email", "address", "bank_account", "kyc_id"):
            vault_values.append(dict(customer_id=cid, kind=kind, value=c[kind]))
        for n in sorted(nominees[c["raw_id"]]):
            vault_values.append(dict(customer_id=cid, kind="nominee", value=n))
        for n in (c.get("former_names") or "").split("|"):
            if n:
                vault_values.append(dict(customer_id=cid, kind="former_name", value=n))
        for reg in sorted(vehicles[c["raw_id"]]):
            vault_values.append(dict(customer_id=cid, kind="vehicle_reg", value=reg))

    s_policies = [dict(policy_id=x["policy_id"], customer_id=cid_of[x["raw_id"]], line=x["line"], start_date=x["start_date"],
                       end_date=x["end_date"], sum_insured=x["sum_insured"], premium=x["premium"], status=x["status"],
                       lapse_date=x["lapse_date"], nominee_relation=x["nominee_relation"], nominee_token=tok("nominee", x["nominee_name"]),
                       vehicle_token=tok("vehicle", x["vehicle_reg"]), vehicle_segment=x["vehicle_segment"],
                       declared_conditions=x["declared_conditions"]) for x in policies]
    s_claims = [dict(claim_id=x["claim_id"], policy_id=x["policy_id"], customer_id=cid_of[x["raw_id"]], line=x["line"],
                     filed_date=x["filed_date"], incident_date=x["incident_date"], admit_date=x["admit_date"],
                     discharge_date=x["discharge_date"], procedure_code=x["procedure_code"], condition_tag=x["condition_tag"],
                     damage_items=x["damage_items"], amount=x["amount"], provider_id=x["provider_id"], invoice_no=x["invoice_no"],
                     incident_district=x["incident_district"], status=x["status"], decided_date=x["decided_date"], outcome=x["outcome"],
                     payout_token=tok("bank", x["payout_account"]), vehicle_token=tok("vehicle", x["vehicle_reg"]), cert_date=x["cert_date"],
                     description=scrub[x["raw_id"]](x["description"]), source=x["source"], case_id=x["case_id"]) for x in claims]
    s_events = []
    token_keys = {"new_phone": ("phone_token", "phone"), "new_address": ("address_token", "address"),
                  "new_bank_account": ("bank_token", "bank"), "new_nominee_name": ("nominee_token", "nominee"),
                  "vehicle": ("vehicle_token", "vehicle")}
    for e in events:
        d, cid = dict(event_details[e["event_id"]]), cid_of[e["raw_id"]]
        for raw_key, (new_key, kind) in token_keys.items():
            if raw_key in d:
                value = d.pop(raw_key)
                d[new_key] = tok(kind, value)
                if kind in ("phone", "address", "bank"):
                    s_tokens.append(dict(customer_id=cid, token_type=kind, token=d[new_key], source="event", first_seen=e["event_date"]))
                    vault_values.append(dict(customer_id=cid, kind=raw_key, value=value))
        s_events.append(dict(event_id=e["event_id"], customer_id=cid, policy_id=e["policy_id"], claim_id=e["claim_id"],
                             event_type=e["event_type"], event_date=e["event_date"], details=json.dumps(d, sort_keys=True)))
    s_docs = [dict(doc_id=x["doc_id"], claim_id=x["claim_id"], doc_type=x["doc_type"], doc_date=x["doc_date"],
                   text=scrub[claim_owner[x["claim_id"]]](x["text"])) for x in docs]
    review, k_stats = reviewer_view(s_customers, cfg["privacy"]["k"])

    # ---- write vault first (separate file), then silver
    vault = duckdb.connect(str(p["vault"]))
    write_table(vault, "id_map", VAULT_SPECS["id_map"], [dict(raw_id=r, customer_id=c) for r, c in cid_of.items()])
    write_table(vault, "customers_pii", VAULT_SPECS["customers_pii"], vault_pii)
    write_table(vault, "pii_values", VAULT_SPECS["pii_values"], vault_values)
    vault.close()
    silver = duckdb.connect(str(p["silver"]))
    rows = {"customers": s_customers, "policies": s_policies, "claims": s_claims, "events": s_events, "documents": s_docs,
            "providers": fetch(raw, "providers"), "areas": list(areas.values()), "procedures": fetch(raw, "procedures"),
            "entity_tokens": s_tokens, "reviewer_customers": list(review.values())}
    for name, spec in SILVER_SPECS.items():
        write_table(silver, name, spec, rows[name])
    silver.close()
    raw.close()
    return {"silver_rows": {k: len(v) for k, v in rows.items()}, "vault_values": len(vault_values), "k_anonymity": k_stats}
