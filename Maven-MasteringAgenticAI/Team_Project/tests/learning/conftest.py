import pytest

from claim_triage.app.backfill import backfill_rules, claim_ids
from claim_triage.app.store import Store


@pytest.fixture(scope="module")
def filled(built_dataset, tmp_path_factory):
    """A scratch app database with a rules-only assessment for every claim awaiting assessment (built once per test module)."""
    cfg, _ = built_dataset
    store = Store(cfg, path=tmp_path_factory.mktemp("app") / "app.duckdb")
    ids = claim_ids(cfg)
    bands = backfill_rules(store, cfg, ids)
    yield cfg, store, ids, bands
    store.close()
