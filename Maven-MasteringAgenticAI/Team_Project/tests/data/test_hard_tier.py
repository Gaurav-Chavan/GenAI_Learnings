"""The hard tier: probes that need reading documents or past decisions. It must be isolated from the 300 standard cases and its labels must be supported by the data."""
from __future__ import annotations

from datetime import date

import duckdb
import pytest

from claim_triage.data.config import paths

MEDICAL = ("icu",)  # every documented complication mentions intensive care; a diagnosis such as "infection" can appear in any summary


@pytest.fixture(scope="module")
def con(built_dataset):
    cfg, _ = built_dataset
    p = paths(cfg)
    labels, silver, gold = (duckdb.connect(str(p[k]), read_only=True) for k in ("labels", "silver", "gold"))
    yield cfg, labels, silver, gold
    for c in (labels, silver, gold):
        c.close()


def test_hard_labels_are_balanced_and_split(con):
    cfg, labels, _, _ = con
    n = cfg["hard"]["per_scenario"]
    rows = labels.execute("select scenario_id, kind, count(*) from labels_hard group by all order by 1, 2").fetchall()
    assert rows == [("D1", "honest", n // 2), ("D1", "suspicious", n // 2), ("D2", "honest", n // 2), ("D2", "suspicious", n // 2)]
    held = dict(labels.execute("select split, count(*) from labels_hard group by 1").fetchall())
    assert set(held) == {"dev", "heldout"} and 0.25 <= held["heldout"] / (2 * n) <= 0.40
    assert labels.execute("select count(*) from labels_hard where case_id not like 'HARD-%'").fetchone()[0] == 0
    # a suspicious D2 case can only be expected in review: the best a reviewer can say about a slightly high bill is "look at it"
    assert labels.execute("select distinct expected_band from labels_hard where scenario_id = 'D2' and kind = 'suspicious'").fetchall() == [("amber",)]
    assert labels.execute("select distinct expected_band from labels_hard where scenario_id = 'D1' and kind = 'suspicious'").fetchall() == [("red",)]


def test_hard_tier_is_isolated_from_the_standard_cases(con):
    cfg, labels, silver, _ = con
    eval_end = date.fromisoformat(cfg["eval_end"])
    hard = [r[0] for r in labels.execute("select claim_id from labels_hard").fetchall()]
    standard = [r[0] for r in labels.execute("select claim_id from labels").fetchall()]
    assert not set(hard) & set(standard)
    filed = dict(silver.execute("select claim_id, filed_date from claims").fetchall())
    assert all(filed[c] > eval_end for c in hard)  # filed after the last standard claim, so standard fact sheets never see them
    record_date = date.fromisoformat(cfg["hard"]["record_date"])
    track_record = silver.execute("select claim_id, filed_date, decided_date from claims where source = 'history' and filed_date >= ?", [record_date]).fetchall()
    assert track_record and all(f > eval_end and d > eval_end for _, f, d in track_record)
    assert max(filed[c] for c in standard) <= eval_end


def test_d2_provider_track_records_separate_the_two_kinds_and_no_area_flag_interferes(con):
    cfg, labels, silver, gold = con
    window = date.fromisoformat(cfg["eval_start"])
    record = dict(((r[0]), r[1]) for r in silver.execute(
        "select provider_id, sum((outcome = 'confirmed_fraud')::int) from claims where outcome is not null and line = 'health' "
        "and decided_date >= ? - INTERVAL 730 DAY group by 1", [window]).fetchall())
    marks = lambda ids: ",".join("?" * len(ids))  # noqa: E731 - the labels live in another database file, so IDs are passed in rather than joined
    for kind, ok in (("suspicious", lambda n: n >= 3), ("honest", lambda n: n <= 1)):
        ids = [r[0] for r in labels.execute("select claim_id from labels_hard where scenario_id = 'D2' and kind = ?", [kind]).fetchall()]
        provs = [r[0] for r in silver.execute(f"select provider_id from claims where claim_id in ({marks(ids)})", ids).fetchall()]
        assert provs and all(ok(record.get(p, 0)) for p in provs), kind
    d2 = [r[0] for r in labels.execute("select claim_id from labels_hard where scenario_id = 'D2'").fetchall()]
    assert gold.execute(f"select count(*) from claim_features where area_is_high and claim_id in ({marks(d2)})", d2).fetchone()[0] == 0
    ratios = [r[0] for r in gold.execute(f"select bill_vs_peer_median from claim_features where claim_id in ({marks(d2)})", d2).fetchall()]
    assert all(1.3 <= x < 2.0 for x in ratios)  # below the rule threshold: fixed rules cannot see these


def test_d1_twins_have_the_same_large_bill_and_differ_only_in_what_the_summary_says(con):
    _, labels, silver, gold = con
    for kind, medical in (("honest", True), ("suspicious", False)):
        ids = [r[0] for r in labels.execute("select claim_id from labels_hard where scenario_id = 'D1' and kind = ?", [kind]).fetchall()]
        ratios = [r[0] for r in gold.execute("select bill_vs_peer_median from claim_features where claim_id in (%s)" % ",".join("?" * len(ids)), ids).fetchall()]
        assert all(x >= 2.0 for x in ratios)  # a fixed rule flags every one of them
        texts = [r[0].lower() for r in silver.execute("select text from documents where doc_type = 'discharge_summary' and claim_id in (%s)" % ",".join("?" * len(ids)), ids).fetchall()]
        assert len(texts) == len(ids)
        assert all(any(m in t for m in MEDICAL) == medical for t in texts)
        itemised = silver.execute("select count(distinct claim_id) from documents where doc_type = 'itemised_bill' and claim_id in (%s)" % ",".join("?" * len(ids)), ids).fetchone()[0]
        assert itemised == len(ids)  # nothing is missing: the only difference is in the text
