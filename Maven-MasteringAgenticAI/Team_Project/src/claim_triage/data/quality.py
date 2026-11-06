"""Data-quality and privacy checks. A failed check raises DataQualityError and stops the pipeline."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb

from .config import paths, when


class DataQualityError(RuntimeError):
    pass


def _res(name: str, ok: bool, detail: str = "") -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def _enforce(layer: str, results: list[dict]) -> list[dict]:
    failed = [r for r in results if not r["ok"]]
    if failed:
        lines = "\n".join(f"  - {r['check']}: {r['detail']}" for r in failed)
        raise DataQualityError(f"{layer}: {len(failed)} check(s) failed\n{lines}")
    return results


FORBIDDEN_COLUMNS = {"phone", "email", "address", "dob", "bank_account", "kyc_id", "nominee_name", "vehicle_reg", "raw_id",
                     "scenario_id", "scenario_name", "is_fraud", "expected_band", "expected_indicators", "expected_absent",
                     "expected_missing", "hotspot_pin", "split"}
TOKEN_COLUMNS = [("customers", "phone_token"), ("customers", "email_token"), ("customers", "address_token"), ("customers", "bank_token"),
                 ("customers", "kyc_token"), ("policies", "nominee_token"), ("policies", "vehicle_token"), ("claims", "payout_token"),
                 ("claims", "vehicle_token"), ("entity_tokens", "token")]
TOKEN_RX = "^(PH|EM|AD|BK|KY|VH|NM)-[0-9A-F]{8}$"


def silver_rules(cfg: dict) -> list[tuple[str, str]]:
    today = when(cfg, "dataset_today")
    return [
        ("claims -> policies", "select count(*) from claims c left join policies p using(policy_id) where p.policy_id is null"),
        ("claim customer matches its policy's customer", "select count(*) from claims c join policies p using(policy_id) where c.customer_id <> p.customer_id"),
        ("policies -> customers", "select count(*) from policies p left join customers c using(customer_id) where c.customer_id is null"),
        ("events -> customers", "select count(*) from events e left join customers c using(customer_id) where c.customer_id is null"),
        ("events -> claims", "select count(*) from events e left join claims c using(claim_id) where e.claim_id is not null and c.claim_id is null"),
        ("documents -> claims", "select count(*) from documents d left join claims c using(claim_id) where c.claim_id is null"),
        ("claims -> providers", "select count(*) from claims c left join providers p using(provider_id) where c.provider_id is not null and p.provider_id is null"),
        ("claim filed before its policy started", "select count(*) from claims c join policies p using(policy_id) where c.filed_date < p.start_date"),
        ("claim filed after its policy ended (death claims excepted)",
         "select count(*) from claims c join policies p using(policy_id) where p.lapse_date is not null and c.filed_date > p.lapse_date "
         "and not (c.line = 'term' and c.incident_date is not null and c.incident_date <= p.lapse_date)"),
        ("no claims after a customer's death",
         "select count(*) from claims c join (select customer_id, min(lapse_date) d from policies where status = 'terminated' group by customer_id) t "
         "using(customer_id) where c.filed_date > t.d and not (c.line = 'term' and c.incident_date is not null and c.incident_date <= t.d)"),
        ("no events after a customer's death except the death claim's",
         "select count(*) from events e join (select customer_id, min(lapse_date) d from policies where status = 'terminated' group by customer_id) t "
         "using(customer_id) where e.event_date > t.d and e.claim_id is null"),
        ("allowed policy status", "select count(*) from policies where status not in ('active','lapsed','terminated')"),
        ("claim filed in the future", f"select count(*) from claims where filed_date > DATE '{today}'"),
        ("admission after discharge", "select count(*) from claims where admit_date > discharge_date"),
        ("discharge after filing", "select count(*) from claims where discharge_date > filed_date"),
        ("incident after filing", "select count(*) from claims where incident_date > filed_date"),
        ("policy lapsed before it started", "select count(*) from policies where lapse_date is not null and lapse_date < start_date"),
        ("allowed lines", "select count(*) from policies where line not in ('health','term','motor')"),
        ("allowed claim status", "select count(*) from claims where status not in ('open','pending','closed')"),
        ("allowed outcomes", "select count(*) from claims where outcome is not null and outcome not in ('cleared','confirmed_fraud')"),
        ("outcomes only on closed history claims", "select count(*) from claims where outcome is not null and (source <> 'history' or status <> 'closed')"),
        ("evaluation claims carry a case id and are undecided",
         "select count(*) from claims where source = 'eval' and (case_id is null or decided_date is not null or outcome is not null)"),
        ("history claims carry no case id", "select count(*) from claims where source = 'history' and case_id is not null"),
        ("every customer appears in the reviewer view",
         "select count(*) from customers c left join reviewer_customers r using(customer_id) where r.customer_id is null"),
    ]


def check_silver(cfg: dict) -> list[dict]:
    p = paths(cfg)
    con = duckdb.connect(str(p["silver"]), read_only=True)
    res = []
    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    for table, col in [("customers", "customer_id"), ("policies", "policy_id"), ("claims", "claim_id"), ("events", "event_id"),
                       ("documents", "doc_id"), ("providers", "provider_id")]:
        dup = one(f"select count(*) - count(distinct {col}) from {table}")
        nulls = one(f"select count(*) from {table} where {col} is null")
        res.append(_res(f"unique, non-null {table}.{col}", dup == 0 and nulls == 0, f"{dup} duplicates, {nulls} nulls"))
    for name, sql in silver_rules(cfg):
        n = one(sql)
        res.append(_res(name, n == 0, f"{n} violations"))
    for table, col in TOKEN_COLUMNS:
        n = one(f"select count(*) from {table} where {col} is not null and not regexp_matches({col}, '{TOKEN_RX}')")
        res.append(_res(f"token format {table}.{col}", n == 0, f"{n} malformed"))
    cols = {(t, c) for t, c in con.execute("select table_name, column_name from information_schema.columns where table_schema='main'").fetchall()}
    bad = sorted(f"{t}.{c}" for t, c in cols if c in FORBIDDEN_COLUMNS or (t, c) == ("customers", "name"))
    res.append(_res("no personal or label columns in silver", not bad, ", ".join(bad)))
    tables = {t for t, _ in cols}
    res.append(_res("no labels table in silver", "labels" not in tables, ""))
    res.append(_res("reviewer view hides the PIN code", ("reviewer_customers", "pincode") not in cols, ""))
    min_group = one("select coalesce(min(n), 999) from (select count(*) n from reviewer_customers where generalisation_level < 4 "
                    "group by state, district, gender, age_band)")
    res.append(_res(f"reviewer view: every location/gender/age group has >= {cfg['privacy']['k']} customers", min_group >= cfg["privacy"]["k"],
                    f"smallest group = {min_group}"))
    n_eval = one("select count(*) from claims where source='eval'")
    expected = 10 * cfg["eval"]["per_scenario"] + 2 * (cfg.get("hard") or {}).get("per_scenario", 0)  # standard cases + hard tier
    res.append(_res("evaluation claim count", n_eval == expected, f"{n_eval} vs {expected}"))
    res.append(_res("customer count", one("select count(*) from customers") == cfg["n_customers"], ""))
    con.close()
    return _enforce("silver", res)


def canary(cfg: dict, layers=("silver", "gold"), extra_text: str = "") -> list[tuple[str, str]]:
    """Return every vault value that appears anywhere in the app-visible layers (must be empty)."""
    p = paths(cfg)
    vault = duckdb.connect(str(p["vault"]), read_only=True)
    values = [(k, v) for k, v in vault.execute("select kind, value from pii_values").fetchall() if v and len(v) >= 6]
    vault.close()
    chunks = [extra_text]
    for layer in layers:
        con = duckdb.connect(str(p[layer]), read_only=True)
        for (table,) in con.execute("select table_name from information_schema.tables where table_schema='main'").fetchall():
            for (col,) in con.execute(f"select column_name from information_schema.columns where table_name='{table}' and data_type='VARCHAR'").fetchall():
                chunks.append("\n".join(r[0] for r in con.execute(f'select "{col}" from {table} where "{col}" is not null').fetchall()))
        con.close()
    big = "\n".join(chunks).lower()
    return [(k, v) for k, v in values if v.lower() in big]


def check_privacy(cfg: dict, layers=("silver", "gold")) -> list[dict]:
    leaks = canary(cfg, layers)
    return _enforce("privacy", [_res(f"canary: no vault value appears in {' or '.join(layers)}", not leaks,
                                     f"{len(leaks)} leaked values, e.g. {[k for k, _ in leaks[:5]]}")])


# --- reference detection rules, one per scenario, used to prove the labels are supported by the data ---------------
def _flag(r: dict, sid: str) -> bool:
    g = r.get
    if sid == "H1":
        return g("dup_candidate_claim_id") is not None
    if sid == "H2":
        return (g("bill_vs_peer_median") or 0) >= 2.0
    if sid == "T1":
        return g("nominee_change_days_before_incident") is not None and g("nominee_change_days_before_incident") <= 45 and bool(g("nominee_change_to_non_family"))
    if sid == "T2":
        return (g("doc_date_gap_days") or 0) >= 7
    if sid == "M1":
        return g("same_vehicle_same_date_claim") is not None
    if sid == "M2":
        return (g("prior_inspection_overlap_items") or 0) >= 2 and not g("repaired_since_inspection")
    if sid == "X1":
        return (g("prior_same_condition_before_start") or 0) >= 1 and not g("declared_has_condition") and (g("policy_age_days") or 999) <= 90
    if sid == "X2":
        return (g("days_policy_start_to_incident") or 9999) <= 180 and (g("serious_health_claims_before_start") or 0) >= 1
    if sid == "X3":
        return g("linked_claim_id") is not None and ((g("admit_before_linked_incident_days") or 0) >= 1 or abs(g("linked_incident_gap_days") or 0) >= 3
                                                    or bool(g("linked_district_mismatch")))
    if sid == "X4":
        return (g("ring_unrelated_customers") or 0) >= 3
    return False


MISSING_DOC = {"original_bill": "doc_bill", "itemised_bill": "doc_itemised_bill", "nominee_consent": "doc_nominee_consent",
               "death_certificate": "doc_death_certificate", "fir": "doc_fir", "inspection_report": "doc_inspection_report",
               "discharge_summary": "doc_discharge_summary", "cause_of_death": "doc_cause_of_death",
               "provider_registration": "doc_provider_registration"}


def signal_report(cfg: dict) -> list[dict]:
    """Per scenario: how often a plain reference rule flags suspicious vs honest cases (the data must support the labels)."""
    p = paths(cfg)
    gold = duckdb.connect(str(p["gold"]), read_only=True)
    cur = gold.execute("select * from claim_features")
    names = [d[0] for d in cur.description]
    feats = {r[names.index("claim_id")]: dict(zip(names, r)) for r in cur.fetchall()}
    gold.close()
    labels = duckdb.connect(str(p["labels"]), read_only=True)
    cur = labels.execute("select * from labels")
    lnames = [d[0] for d in cur.description]
    rows = [dict(zip(lnames, r)) for r in cur.fetchall()]
    labels.close()
    out = []
    for sid in sorted({r["scenario_id"] for r in rows}):
        sc = [r for r in rows if r["scenario_id"] == sid]
        sus = [r for r in sc if r["kind"] == "suspicious"]
        hon = [r for r in sc if r["kind"] == "honest"]
        mis = [r for r in sc if r["kind"] == "missing"]
        tpr = sum(_flag(feats[r["claim_id"]], sid) for r in sus) / len(sus)
        fpr = sum(_flag(feats[r["claim_id"]], sid) for r in hon) / len(hon)
        mflag = sum(_flag(feats[r["claim_id"]], sid) for r in mis) / len(mis)
        doc_ok = all(not feats[r["claim_id"]].get(MISSING_DOC[r["expected_missing"]]) for r in mis if r["expected_missing"])
        out.append(dict(scenario=sid, suspicious_flagged=round(tpr, 2), honest_flagged=round(fpr, 2), missing_flagged=round(mflag, 2),
                        missing_doc_absent=doc_ok, n=(len(sus), len(hon), len(mis))))
    return out


def check_gold(cfg: dict) -> list[dict]:
    p = paths(cfg)
    gold = duckdb.connect(str(p["gold"]), read_only=True)
    silver = duckdb.connect(str(p["silver"]), read_only=True)
    res = []
    g1 = lambda sql: gold.execute(sql).fetchone()[0]  # noqa: E731
    s1 = lambda sql, *a: silver.execute(sql, list(a)).fetchone()[0]  # noqa: E731
    n_eval = s1("select count(*) from claims where source='eval'")
    res.append(_res("one feature row per evaluation claim", g1("select count(*) from claim_features") == n_eval and
                    g1("select count(*) - count(distinct claim_id) from claim_features") == 0, ""))
    res.append(_res("customer_360 covers every customer", g1("select count(*) from customer_360") == s1("select count(*) from customers"), ""))
    cols = {c for _, c in gold.execute("select table_name, column_name from information_schema.columns where table_schema='main'").fetchall()}
    bad = sorted(cols & (FORBIDDEN_COLUMNS | {"pincode"}) - {"pincode"})
    res.append(_res("no personal or label columns in gold", not bad, ", ".join(bad)))
    res.append(_res("claim_features and customer_360 hide the PIN code",
                    not {c for (t, c) in gold.execute("select table_name, column_name from information_schema.columns where table_name in ('claim_features','customer_360')").fetchall() if c == "pincode"}, ""))
    # point-in-time: recompute area counts and prior-claim counts independently from silver
    window = timedelta(days=round(cfg["area"]["window_months"] * 30.4))
    rows = gold.execute("select f.claim_id, f.filed_date, f.line, f.area_basis, f.area_n, f.claims_prior_36m, f.customer_id from claim_features f").fetchall()
    bad_area = bad_prior = 0
    for claim_id, filed, line, basis, area_n, prior36, cid in rows:
        pin = s1("select pincode from customers where customer_id=?", cid)
        if basis == "pin":
            n = s1("select count(*) from claims cl join customers c using(customer_id) where cl.source='history' and cl.status='closed' and cl.outcome is not null "
                   "and cl.line=? and cl.decided_date >= ? and cl.decided_date < ? and c.pincode=?", line, filed - window, filed, pin)
            bad_area += n != area_n
        n36 = s1("select count(*) from claims where customer_id=? and claim_id<>? and filed_date < ? and filed_date >= ?", cid, claim_id, filed, filed - timedelta(days=1095))
        bad_prior += n36 != prior36
    res.append(_res("point-in-time: area counts reproduce from claims decided before filing", bad_area == 0, f"{bad_area} mismatches"))
    res.append(_res("point-in-time: prior-claim counts use only earlier claims", bad_prior == 0, f"{bad_prior} mismatches"))
    # hotspot detection
    labels = duckdb.connect(str(p["labels"]), read_only=True)
    hot = {r[0] for r in labels.execute("select pincode from hotspot_pins").fetchall()}
    labels.close()
    snap = gold.execute("select pincode, line, is_high from area_risk where line in ('health','motor')").fetchall()
    hot_rows = [r for r in snap if r[0] in hot]
    cold_rows = [r for r in snap if r[0] not in hot]
    hit = sum(r[2] for r in hot_rows) / len(hot_rows)
    false = sum(r[2] for r in cold_rows) / len(cold_rows)
    res.append(_res("area risk finds the designed hotspots (health + motor)", hit >= 0.5, f"{hit:.0%} of hotspot cells flagged"))
    res.append(_res("area risk does not flag many ordinary areas", false <= 0.2, f"{false:.0%} of ordinary cells flagged"))
    # the labels must be supported by the data
    gold.close()
    silver.close()
    sig = signal_report(cfg)
    for r in sig:
        res.append(_res(f"{r['scenario']}: reference rule flags suspicious cases", r["suspicious_flagged"] >= 0.85, f"{r['suspicious_flagged']:.0%}"))
        res.append(_res(f"{r['scenario']}: reference rule rarely flags honest cases", r["honest_flagged"] <= 0.15, f"{r['honest_flagged']:.0%}"))
        res.append(_res(f"{r['scenario']}: missing-evidence cases lack the named document", r["missing_doc_absent"], ""))
    return _enforce("gold", res)


def write_report(cfg: dict, sections: dict[str, list[dict]], extra: str = "") -> Path:
    out = Path(cfg["data_dir"]) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# Data quality report", "", extra, ""]
    for layer, results in sections.items():
        lines += [f"## {layer}", "", "| Check | Result | Detail |", "|---|---|---|"]
        lines += [f"| {r['check']} | {'pass' if r['ok'] else '**FAIL**'} | {r['detail']} |" for r in results]
        lines.append("")
    path = out / "quality_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
