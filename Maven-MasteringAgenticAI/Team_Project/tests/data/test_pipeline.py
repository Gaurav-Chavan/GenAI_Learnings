"""Data 360 tests: privacy, separation, reproducibility, point-in-time and fairness of the evaluation design."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from claim_triage.data.build import build
from claim_triage.data.build_gold import AreaRisk
from claim_triage.data.config import load_config, paths
from claim_triage.data.privacy_split import Scrubber
from claim_triage.data.quality import canary
from claim_triage.data.tokens import make_token, normalise

SRC = Path(__file__).resolve().parents[2] / "src" / "claim_triage"
DATA_MODULES_ALLOWED_TO_TOUCH_VAULT_OR_LABELS = {"generate.py", "privacy_split.py", "quality.py", "build.py", "config.py"}


@pytest.fixture(scope="module")
def built(built_dataset):
    return built_dataset


def rows(cfg, layer, sql):
    con = duckdb.connect(str(paths(cfg)[layer]), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_pipeline_completes_with_all_checks_passing(built):
    cfg, summary = built
    assert summary["raw"]["customers"] == cfg["n_customers"]
    assert summary["raw"]["labels"] == 10 * cfg["eval"]["per_scenario"]
    assert len(summary["dataset_hash"]) == 16


def test_canary_finds_no_personal_value_but_would_catch_one(built):
    cfg, _ = built
    assert canary(cfg) == []
    name = rows(cfg, "vault", "select value from pii_values where kind='name' limit 1")[0][0]
    assert canary(cfg, extra_text=f"claim note mentions {name} directly") != []


def test_reviewer_view_has_no_pin_and_meets_k(built):
    cfg, _ = built
    cols = {c for (c,) in rows(cfg, "silver", "select column_name from information_schema.columns where table_name='reviewer_customers'")}
    assert "pincode" not in cols and "customer_id" in cols
    smallest = rows(cfg, "silver", "select min(n) from (select count(*) n from reviewer_customers where generalisation_level < 4 "
                                   "group by state, district, gender, age_band)")[0][0]
    assert smallest >= cfg["privacy"]["k"]
    gold_cols = {c for (c,) in rows(cfg, "gold", "select column_name from information_schema.columns where table_name in ('customer_360','claim_features')")}
    assert "pincode" not in gold_cols


def test_customer_ids_are_opaque_and_do_not_reveal_signup_order(built):
    cfg, _ = built
    data = rows(cfg, "silver", "select customer_id, signup_date from customers order by signup_date")
    numbers = [int(cid.split("-")[1]) for cid, _ in data]
    assert all(cid.startswith("CUST-") and len(cid) == 11 for cid, _ in data)
    n = len(numbers)
    rank = sorted(range(n), key=lambda i: numbers[i])
    position = [0] * n
    for r, i in enumerate(rank):
        position[i] = r
    mean = (n - 1) / 2
    cov = sum((i - mean) * (position[i] - mean) for i in range(n))
    var = sum((i - mean) ** 2 for i in range(n))
    assert abs(cov / var) < 0.15  # rank correlation between signup order and id number


def test_policyholders_are_adults_when_they_sign_up_and_when_policies_start(built):
    cfg, _ = built
    con = duckdb.connect(str(paths(cfg)["silver"]), read_only=True)
    con.execute(f"attach '{str(paths(cfg)['vault']).replace(chr(92), '/')}' as v (read_only)")
    young_policy = con.execute("select count(*) from policies p join v.customers_pii d using(customer_id) where p.start_date < d.dob + interval 18 year").fetchone()[0]
    young_signup = con.execute("select count(*) from customers c join v.customers_pii d using(customer_id) where c.signup_date < d.dob + interval 18 year").fetchone()[0]
    con.close()
    assert young_policy == 0 and young_signup == 0


def test_a_death_ends_the_customers_activity(built):
    cfg, _ = built
    deceased = rows(cfg, "gold", "select count(*) from customer_360 where deceased")[0][0]
    death_claims = rows(cfg, "silver", "select count(*) from claims where source='eval' and line='term'")[0][0]
    assert deceased == death_claims == 90
    still_active = rows(cfg, "silver", "select count(*) from policies where status = 'terminated' and lapse_date is null")[0][0]
    assert still_active == 0
    assert rows(cfg, "gold", "select count(*) from customer_360 where deceased and policies_active > 0")[0][0] == 0
    # history contains living-benefit claims only, never a death certificate
    deaths_in_history = rows(cfg, "silver", "select count(*) from claims c join documents d using(claim_id) "
                                           "where c.source='history' and d.doc_type = 'death_certificate'")[0][0]
    assert deaths_in_history == 0


def test_labels_and_vault_are_isolated_from_app_code():
    offenders = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        touches = any(s in text for s in ('["vault"]', '["labels"]', "vault.duckdb", "labels.duckdb"))
        allowed = (path.parent.name == "data" and path.name in DATA_MODULES_ALLOWED_TO_TOUCH_VAULT_OR_LABELS) or path.parent.name == "evaluation"
        if touches and not allowed:
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_labels_never_appear_in_silver_or_gold(built):
    cfg, _ = built
    forbidden = {"scenario_id", "scenario_name", "is_fraud", "expected_band", "expected_indicators", "expected_absent", "expected_missing", "hotspot_pin"}
    for layer in ("silver", "gold"):
        cols = {c for (c,) in rows(cfg, layer, "select column_name from information_schema.columns where table_schema='main'")}
        assert not (cols & forbidden)
        tables = {t for (t,) in rows(cfg, layer, "select table_name from information_schema.tables where table_schema='main'")}
        assert "labels" not in tables


def test_same_seed_reproduces_the_dataset_and_a_new_seed_changes_it(built, tmp_path):
    cfg, summary = built
    again = build(load_config({"data_dir": str(tmp_path / "again")}), verbose=False)
    assert again["dataset_hash"] == summary["dataset_hash"]
    other = build(load_config({"data_dir": str(tmp_path / "other"), "seed": 7}), verbose=False)
    assert other["dataset_hash"] != summary["dataset_hash"]


def test_evaluation_mix_and_hotspot_decoys(built):
    cfg, _ = built
    kinds = dict(rows(cfg, "labels", "select kind, count(*) from labels group by kind"))
    assert kinds == {"suspicious": 100, "honest": 140, "missing": 60}
    by_hot = {(hot, kind): n for hot, kind, n in rows(cfg, "labels", "select hotspot_pin, kind, count(*) from labels group by all")}
    total_hot = sum(n for (hot, _), n in by_hot.items() if hot)
    total_cold = sum(n for (hot, _), n in by_hot.items() if not hot)
    assert total_hot > 60  # hotspot cases exist in every kind, so area alone cannot reveal the label
    for kind, share in (("suspicious", 0.33), ("honest", 0.47), ("missing", 0.20)):
        hot_share = by_hot.get((True, kind), 0) / total_hot
        cold_share = by_hot.get((False, kind), 0) / total_cold
        assert abs(hot_share - share) < 0.15 and abs(cold_share - share) < 0.15
    held = dict(rows(cfg, "labels", "select split, count(*) from labels group by split"))
    assert held == {"dev": 200, "heldout": 100}
    per_scenario = rows(cfg, "labels", "select scenario_id, sum((split='heldout')::int) from labels group by scenario_id")
    assert all(n == 10 for _, n in per_scenario)


def test_every_scenario_has_all_three_kinds(built):
    cfg, _ = built
    for sid, kind, n in rows(cfg, "labels", "select scenario_id, kind, count(*) from labels group by all"):
        assert n >= 6, (sid, kind)
    assert len(rows(cfg, "labels", "select distinct scenario_id from labels")) == 10


def test_area_risk_ignores_data_from_after_the_claim_was_filed():
    cfg = load_config()
    areas = {411001: {"district": "Pune"}, 411014: {"district": "Pune"}}
    past = [dict(line="health", decided_date=date(2026, 1, 1) if i else date(2026, 2, 1), pincode=411001, fraud=int(i % 5 == 0)) for i in range(40)]
    future = [dict(line="health", decided_date=date(2026, 9, d), pincode=411001, fraud=1) for d in range(1, 29)]
    before = AreaRisk(past, areas, cfg).at(411001, "health", date(2026, 8, 1))
    after = AreaRisk(past + future, areas, cfg).at(411001, "health", date(2026, 8, 1))
    assert before == after


def test_area_risk_prefers_district_when_a_pin_has_few_claims():
    cfg = load_config()
    areas = {411001: {"district": "Pune"}, 411014: {"district": "Pune"}}
    claims = [dict(line="health", decided_date=date(2026, 1, 1), pincode=411014, fraud=int(i % 4 == 0)) for i in range(60)]
    claims += [dict(line="health", decided_date=date(2026, 1, 1), pincode=411001, fraud=1) for _ in range(3)]
    result = AreaRisk(claims, areas, cfg).at(411001, "health", date(2026, 6, 1))
    assert result["basis"] == "district" and result["n_pin"] == 3


def test_tokens_link_equal_values_and_hide_the_value():
    key = b"test-key"
    assert make_token(key, "phone", "+91 98765 43210") == make_token(key, "phone", "9876543210")
    assert make_token(key, "phone", "9876543210") != make_token(key, "phone", "9876543211")
    assert make_token(key, "phone", "9876543210") != make_token(b"another-key", "phone", "9876543210")
    assert "9876543210" not in make_token(key, "phone", "9876543210")
    assert normalise("vehicle", "mh 12 ab 1234") == "MH12AB1234"


def test_scrubber_removes_personal_values_from_text():
    c = dict(name="Asha Verma", email="asha.verma1@example.com", phone="+919876543210", address="Flat 4, Lake Road, Pune 411001",
             bank_account="123456789012", kyc_id="ABCDE1234F")
    scrub = Scrubber(c, {"Ravi Verma"}, {"MH12AB1234": "VH-ABCDEF12"})
    text = scrub("Asha Verma (+919876543210) of Flat 4, Lake Road, Pune 411001 drove MH12AB1234; nominee Ravi Verma; acct 123456789012")
    for leaked in ("Asha", "9876543210", "Lake Road", "MH12AB1234", "Ravi", "123456789012"):
        assert leaked not in text
    assert "VH-ABCDEF12" in text
