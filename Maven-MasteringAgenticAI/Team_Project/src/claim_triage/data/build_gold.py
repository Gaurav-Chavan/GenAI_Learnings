"""Gold layer: customer_360, area risk and point-in-time claim features, built only from silver.

Point-in-time rule: every feature for a claim uses only what was known on that claim's filing date.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date, timedelta

import duckdb

from .config import paths, when
from .generate import write_table
from .privacy_split import fetch
from .reference import FAMILY, INCOME_MID, SERIOUS_CONDITIONS


def infer_spec(rows: list[dict]) -> list[tuple[str, str]]:
    spec = []
    for col in rows[0]:
        sample = next((r[col] for r in rows if r[col] is not None), None)
        kind = ("BOOLEAN" if isinstance(sample, bool) else "BIGINT" if isinstance(sample, int) else "DOUBLE" if isinstance(sample, float)
                else "DATE" if isinstance(sample, date) else "VARCHAR")
        spec.append((col, kind))
    return spec


class AreaRisk:
    """Confirmed-fraud rate per PIN and line over a rolling window, shrunk toward the district rate."""

    def __init__(self, decided: list[dict], areas: dict[int, dict], cfg: dict):
        self.decided, self.areas, self.cfg = decided, areas, cfg["area"]

    def at(self, pin: int, line: str, asof: date) -> dict:
        a = self.cfg
        lo = asof - timedelta(days=round(a["window_months"] * 30.4))
        pool = [d for d in self.decided if d["line"] == line and lo <= d["decided_date"] < asof]
        district = self.areas[pin]["district"]
        total, fraud = len(pool), sum(d["fraud"] for d in pool)
        portfolio = fraud / total if total else 0.0
        dist = [d for d in pool if self.areas[d["pincode"]]["district"] == district]
        n_d, f_d = len(dist), sum(d["fraud"] for d in dist)
        rate_d = f_d / n_d if n_d else portfolio
        here = [d for d in pool if d["pincode"] == pin]
        n_p, f_p = len(here), sum(d["fraud"] for d in here)
        if n_p >= a["min_pin_claims"]:
            rate, basis, n = (f_p + a["shrinkage_m"] * rate_d) / (n_p + a["shrinkage_m"]), "pin", n_p
        elif n_d >= a["min_district_claims"]:
            rate, basis, n = rate_d, "district", n_d
        else:
            rate, basis, n = portfolio, "portfolio", total
        ratio = rate / portfolio if portfolio else 0.0
        return dict(basis=basis, n=n, fraud=f_p if basis == "pin" else f_d if basis == "district" else fraud, n_pin=n_p, n_district=n_d,
                    rate=round(rate, 4), portfolio_rate=round(portfolio, 4), ratio=round(ratio, 2),
                    is_high=bool(basis != "portfolio" and ratio >= a["high_ratio"]))


def run_gold(cfg: dict) -> dict:
    p = paths(cfg)
    p["gold"].unlink(missing_ok=True)
    s = duckdb.connect(str(p["silver"]), read_only=True)
    today, cutoff = when(cfg, "dataset_today"), when(cfg, "history_cutoff")
    customers = {r["customer_id"]: r for r in fetch(s, "customers")}
    reviewer = {r["customer_id"]: r for r in fetch(s, "reviewer_customers")}
    policies = {r["policy_id"]: r for r in fetch(s, "policies")}
    claims = fetch(s, "claims")
    events = fetch(s, "events")
    docs = fetch(s, "documents")
    providers = {r["provider_id"]: r for r in fetch(s, "providers")}
    procs = {r["code"]: r for r in fetch(s, "procedures")}
    areas = {r["pincode"]: r for r in fetch(s, "areas")}
    tokens = fetch(s, "entity_tokens")
    s.close()

    cust_claims, cust_policies = defaultdict(list), defaultdict(list)
    for cl in sorted(claims, key=lambda x: (x["filed_date"], x["claim_id"])):
        cust_claims[cl["customer_id"]].append(cl)
    for pol in policies.values():
        cust_policies[pol["customer_id"]].append(pol)
    ev_by_cust, ev_by_policy = defaultdict(list), defaultdict(list)
    for e in events:
        e = dict(e, d=json.loads(e["details"]))
        ev_by_cust[e["customer_id"]].append(e)
        if e["policy_id"]:
            ev_by_policy[e["policy_id"]].append(e)
    docs_by_claim = defaultdict(set)
    for d in docs:
        docs_by_claim[d["claim_id"]].add(d["doc_type"])
    by_payout = defaultdict(list)
    for cl in claims:
        if cl["payout_token"]:
            by_payout[cl["payout_token"]].append(cl)
    cust_by_phone = defaultdict(set)
    shared = defaultdict(lambda: defaultdict(set))
    for t in tokens:
        shared[t["token_type"]][t["token"]].add(t["customer_id"])
        if t["token_type"] == "phone":
            cust_by_phone[t["token"]].add(t["customer_id"])

    decided = [dict(line=cl["line"], decided_date=cl["decided_date"], pincode=customers[cl["customer_id"]]["pincode"],
                    fraud=int(cl["outcome"] == "confirmed_fraud"))
               for cl in claims if cl["source"] == "history" and cl["status"] == "closed" and cl["outcome"]]
    area = AreaRisk(decided, areas, cfg)

    health_hist = [cl for cl in claims if cl["line"] == "health" and cl["procedure_code"]]

    def days_since(cid: str, etype: str, asof: date):
        ds = [e["event_date"] for e in ev_by_cust[cid] if e["event_type"] == etype and e["event_date"] <= asof]
        return (asof - max(ds)).days if ds else None

    def bill_peers(cl: dict) -> tuple[float | None, int, str]:
        prov = providers[cl["provider_id"]]
        pool = [x for x in health_hist if x["procedure_code"] == cl["procedure_code"] and x["claim_id"] != cl["claim_id"]
                and x["filed_date"] < cl["filed_date"] and x["provider_id"] != cl["provider_id"]]
        same_d = [x["amount"] for x in pool if providers[x["provider_id"]]["district"] == prov["district"]]
        if len(same_d) >= 5:
            return statistics.median(same_d), len(same_d), "district"
        if len(pool) >= 5:
            return statistics.median(x["amount"] for x in pool), len(pool), "portfolio"
        return float(procs[cl["procedure_code"]]["median_cost"]), 0, "reference"

    def feature_row(cl: dict) -> dict:
        cid, pol, filed, line = cl["customer_id"], policies[cl["policy_id"]], cl["filed_date"], cl["line"]
        c = customers[cid]
        prior = [x for x in cust_claims[cid] if x["claim_id"] != cl["claim_id"] and x["filed_date"] < filed]
        prov = providers.get(cl["provider_id"])
        a = area.at(c["pincode"], line, filed)
        f = dict(claim_id=cl["claim_id"], case_id=cl["case_id"], customer_id=cid, policy_id=cl["policy_id"], line=line, filed_date=filed,
                 amount=cl["amount"], procedure_code=cl["procedure_code"], condition_tag=cl["condition_tag"],
                 location_shown=reviewer[cid]["district"] if reviewer[cid]["district"] != "(state level)" else reviewer[cid]["state"],
                 policy_age_days=(filed - pol["start_date"]).days,
                 claims_prior_12m=sum(1 for x in prior if (filed - x["filed_date"]).days <= 365),
                 claims_prior_36m=sum(1 for x in prior if (filed - x["filed_date"]).days <= 1095),
                 active_policies_at_filing=sum(1 for q in cust_policies[cid] if q["start_date"] <= filed and (q["lapse_date"] is None or q["lapse_date"] > filed)),
                 sum_insured_to_income_ratio=round(pol["sum_insured"] / INCOME_MID[c["income_band"]], 2),
                 provider_unverified=bool(prov and prov["reg_status"] == "unverified"),
                 days_since_phone_change=days_since(cid, "phone_changed", filed), days_since_address_change=days_since(cid, "address_changed", filed),
                 days_since_bank_change=days_since(cid, "bank_changed", filed), days_since_nominee_change=days_since(cid, "nominee_changed", filed),
                 area_basis=a["basis"], area_n=a["n"], area_fraud=a["fraud"], area_rate=a["rate"], area_portfolio_rate=a["portfolio_rate"],
                 area_ratio=a["ratio"], area_is_high=a["is_high"])
        # documents on file
        present = docs_by_claim[cl["claim_id"]]
        for dtype in ("bill", "itemised_bill", "discharge_summary", "repair_estimate", "fir", "inspection_report", "death_certificate",
                      "claim_form", "nominee_consent", "cause_of_death", "correction_affidavit", "provider_registration"):
            f[f"doc_{dtype}"] = dtype in present
        # health: bill vs peers, duplicates, non-disclosure
        if cl["line"] == "health" and cl["procedure_code"]:
            med, n, basis = bill_peers(cl)
            f.update(bill_vs_peer_median=round(cl["amount"] / med, 2), bill_peer_n=n, bill_peer_basis=basis)
            best = None
            for x in prior:
                if x["line"] != "health" or x["procedure_code"] != cl["procedure_code"] or x["provider_id"] != cl["provider_id"]:
                    continue
                if not (x["admit_date"] and cl["admit_date"]) or abs((x["admit_date"] - cl["admit_date"]).days) > 3:
                    continue
                if abs(x["amount"] / cl["amount"] - 1) > 0.05:
                    continue
                best = x
            f.update(dup_candidate_claim_id=best["claim_id"] if best else None, dup_exact_invoice=bool(best and best["invoice_no"] == cl["invoice_no"]))
            prior_cond = [x for x in prior if cl["condition_tag"] and x["line"] == "health" and x["condition_tag"] == cl["condition_tag"]
                          and x["filed_date"] < pol["start_date"]]
            f.update(prior_same_condition_before_start=len(prior_cond), prior_condition_claim_id=prior_cond[0]["claim_id"] if prior_cond else None,
                     declared_has_condition=bool(cl["condition_tag"] and cl["condition_tag"] in (pol["declared_conditions"] or "")))
        # motor: repeats and prior inspections
        if line == "motor" and cl["vehicle_token"]:
            same = [x for x in prior if x["vehicle_token"] == cl["vehicle_token"] and (filed - x["filed_date"]).days <= 120]
            inc = cl["incident_date"]
            twin = next((x for x in same if inc and x["incident_date"] and abs((x["incident_date"] - inc).days) <= 1), None)
            claimed = set((cl["damage_items"] or "").split(";")) - {""}
            f.update(same_vehicle_claims_120d=len(same), same_vehicle_same_date_claim=twin["claim_id"] if twin else None,
                     same_vehicle_same_garage=bool(twin and twin["provider_id"] == cl["provider_id"]))
            insp = [e for e in ev_by_cust[cid] if e["event_type"] == "vehicle_inspected" and e["d"].get("vehicle_token") == cl["vehicle_token"]
                    and e["claim_id"] != cl["claim_id"] and inc and e["event_date"] < inc]
            repairs = [e["event_date"] for e in ev_by_cust[cid] if e["event_type"] == "vehicle_repaired" and e["d"].get("vehicle_token") == cl["vehicle_token"]]
            # compare the claimed damage with EVERY earlier inspection, not only the latest: prefer a match that has not been repaired since
            cands = [(len(claimed & set(e["d"].get("damage", []))), not any(e["event_date"] < r <= inc for r in repairs), e) for e in insp]
            best = max(cands, key=lambda t: (t[0] if t[1] else -1, t[0], t[2]["event_date"]), default=None)
            overlap, unrepaired, last = (best[0], best[1], best[2]) if best else (0, False, None)
            f.update(prior_inspection_days_before=(inc - last["event_date"]).days if last else None, prior_inspection_overlap_items=overlap,
                     repaired_since_inspection=bool(last and not unrepaired), prior_inspection_event_id=last["event_id"] if last else None)
            # rings: other customers paid to the same account recently
            others = [x for x in by_payout[cl["payout_token"]] if x["customer_id"] != cid and filed - timedelta(days=90) <= x["filed_date"] < filed]
            ring = {x["customer_id"] for x in others}
            unrelated = {o for o in ring if customers[o]["address_token"] != c["address_token"]}
            phone_mates = cust_by_phone[c["phone_token"]] - {cid}
            f.update(payout_shared_customers_90d=len(ring), ring_unrelated_customers=len(unrelated),
                     ring_same_provider_claims_90d=sum(1 for x in others if x["provider_id"] == cl["provider_id"]),
                     shared_phone_unrelated=sum(1 for o in phone_mates if customers[o]["address_token"] != c["address_token"]),
                     shared_phone_related=sum(1 for o in phone_mates if customers[o]["address_token"] == c["address_token"]))
        # term: nominee changes, early death, document dates
        if line == "term" and cl["incident_date"]:
            inc = cl["incident_date"]
            changes = [e for e in ev_by_policy[cl["policy_id"]] if e["event_type"] == "nominee_changed" and e["event_date"] <= inc]
            last = max(changes, key=lambda e: e["event_date"]) if changes else None
            serious = [x for x in prior if x["line"] == "health" and x["condition_tag"] in SERIOUS_CONDITIONS and x["filed_date"] < pol["start_date"]]
            f.update(nominee_change_event_id=last["event_id"] if last else None, nominee_change_days_before_incident=(inc - last["event_date"]).days if last else None,
                     nominee_change_to_non_family=bool(last and last["d"].get("relation") not in FAMILY),
                     days_policy_start_to_incident=(inc - pol["start_date"]).days,
                     serious_health_claims_before_start=len(serious), serious_health_claim_id=serious[0]["claim_id"] if serious else None,
                     doc_date_gap_days=abs((inc - cl["cert_date"]).days) if cl["cert_date"] else None)
        # cross-policy: the same accident claimed under another line
        if cl["incident_date"] and line == "health":
            near = [x for x in cust_claims[cid] if x["line"] == "motor" and x["incident_date"] and x["claim_id"] != cl["claim_id"]
                    and abs((x["incident_date"] - cl["incident_date"]).days) <= 10]
            if near:
                m = min(near, key=lambda x: abs((x["incident_date"] - cl["incident_date"]).days))
                f.update(linked_claim_id=m["claim_id"], linked_incident_gap_days=(cl["incident_date"] - m["incident_date"]).days,
                         admit_before_linked_incident_days=(m["incident_date"] - cl["admit_date"]).days if cl["admit_date"] else None,
                         linked_district_mismatch=bool(m["incident_district"] != cl["incident_district"]),
                         linked_doc_fir="fir" in docs_by_claim[m["claim_id"]])
        return f

    eval_claims = [cl for cl in claims if cl["source"] == "eval"]
    feature_rows = [feature_row(cl) for cl in eval_claims]
    all_cols = list(dict.fromkeys(col for r in feature_rows for col in r))
    feature_rows = [{col: r.get(col) for col in all_cols} for r in feature_rows]

    # ---- customer_360 (reviewer-safe snapshot as of the dataset date; no PIN, generalised location)
    c360 = []
    for cid, c in customers.items():
        r = reviewer[cid]
        pols, cls = cust_policies[cid], cust_claims[cid]
        history = [x for x in cls if x["filed_date"] <= today]
        sh = lambda kind, key: len(shared[kind][c[key]] - {cid})  # noqa: E731
        c360.append(dict(customer_id=cid, age_band=r["age_band"], gender=r["gender"], occupation_category=r["occupation_category"],
                         income_band=r["income_band"], state=r["state"], district=r["district"], urban_rural=r["urban_rural"],
                         channel=r["channel"], kyc_status=r["kyc_status"], tenure_band=r["tenure_band"],
                         policies_total=len(pols), policies_active=sum(1 for q in pols if q["status"] == "active"),
                         policies_lapsed=sum(1 for q in pols if q["status"] == "lapsed"), lines=",".join(sorted({q["line"] for q in pols})),
                         deceased=any(q["status"] == "terminated" for q in pols),
                         total_sum_insured=sum(q["sum_insured"] for q in pols), claims_total=len(history),
                         claims_last_12m=sum(1 for x in history if (today - x["filed_date"]).days <= 365),
                         claims_last_36m=sum(1 for x in history if (today - x["filed_date"]).days <= 1095),
                         claimed_amount_36m=sum(x["amount"] for x in history if (today - x["filed_date"]).days <= 1095),
                         last_claim_date=max((x["filed_date"] for x in history), default=None),
                         nominee_changes=sum(1 for e in ev_by_cust[cid] if e["event_type"] == "nominee_changed"),
                         contact_changes=sum(1 for e in ev_by_cust[cid] if e["event_type"] in ("phone_changed", "address_changed", "bank_changed")),
                         customers_sharing_phone=sh("phone", "phone_token"), customers_sharing_bank=sh("bank", "bank_token"),
                         customers_sharing_address=sh("address", "address_token")))

    # ---- area risk snapshots (as of the history cutoff)
    area_rows, district_rows = [], []
    for pin, a in areas.items():
        for line in ("health", "term", "motor"):
            r = area.at(pin, line, cutoff + timedelta(days=1))
            area_rows.append(dict(pincode=pin, district=a["district"], state=a["state"], line=line, basis=r["basis"], n_claims=r["n"], n_pin_claims=r["n_pin"],
                                  rate=r["rate"], portfolio_rate=r["portfolio_rate"], ratio=r["ratio"], is_high=r["is_high"]))
    for district in sorted({a["district"] for a in areas.values()}):
        for line in ("health", "term", "motor"):
            pool = [d for d in decided if d["line"] == line and areas[d["pincode"]]["district"] == district and d["decided_date"] <= cutoff]
            district_rows.append(dict(district=district, line=line, n_claims=len(pool), confirmed_fraud=sum(d["fraud"] for d in pool),
                                      rate=round(sum(d["fraud"] for d in pool) / len(pool), 4) if pool else None))
    prov_rows = []
    by_pp = defaultdict(list)
    for x in health_hist:
        if x["filed_date"] <= cutoff:
            by_pp[(x["provider_id"], x["procedure_code"])].append(x["amount"])
    for (prov, code), amounts in sorted(by_pp.items()):
        prov_rows.append(dict(provider_id=prov, procedure_code=code, n_claims=len(amounts), median_amount=statistics.median(amounts), max_amount=max(amounts)))

    con = duckdb.connect(str(p["gold"]))
    tables = {"customer_360": c360, "claim_features": feature_rows, "area_risk": area_rows, "area_risk_district": district_rows,
              "provider_stats": prov_rows,
              "meta": [dict(key="as_of_features", value="each claim's filing date"), dict(key="as_of_snapshots", value=str(cutoff)),
                       dict(key="sources", value="silver.customers, policies, claims, events, documents, providers, entity_tokens")]}
    for name, rows in tables.items():
        write_table(con, name, infer_spec(rows), rows)
    con.close()
    return {k: len(v) for k, v in tables.items()}
