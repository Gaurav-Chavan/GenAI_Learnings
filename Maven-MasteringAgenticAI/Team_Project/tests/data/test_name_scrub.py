"""Regression: a customer's earlier name (their surname was changed in the household scenario) must be scrubbed from claim text and documents too."""
from __future__ import annotations

import re

import duckdb

from claim_triage.data.config import paths


def test_no_personal_name_survives_in_claim_text_or_documents(built_dataset):
    cfg, _ = built_dataset
    vault = duckdb.connect(str(paths(cfg)["vault"]), read_only=True)
    former = [r[0] for r in vault.execute("select value from pii_values where kind = 'former_name'").fetchall()]
    vault.close()
    assert former, "the household scenario renames relatives, so former names must be recorded in the vault"

    silver = duckdb.connect(str(paths(cfg)["silver"]), read_only=True)
    texts = [r[0] for r in silver.execute("select description from claims where description is not null").fetchall()]
    texts += [r[0] for r in silver.execute("select text from documents where text is not null").fetchall()]
    silver.close()
    blob = "\n".join(texts).lower()
    assert not [n for n in former if n.lower() in blob]

    health = [t for t in texts if " admitted at " in t]
    assert health and all(t.startswith("[CUSTOMER]") for t in health)  # no name-like text in front of "admitted"
    assert not [t for t in texts if re.search(r"Insured [A-Z][a-z]+ [A-Z][a-z]+", t)]
