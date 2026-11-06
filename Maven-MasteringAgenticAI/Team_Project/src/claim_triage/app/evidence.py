"""Turn an evidence ID from a finding (CLM-..., DOC-..., EVT-..., PRV-..., POL-...) into a small readable record.

Read-only over silver: scrubbed text, tokens instead of personal values, and never the vault or the answer keys.
"""
from __future__ import annotations

import json
import re

import duckdb

from ..data.config import paths

KINDS = {"CLM": "Claim", "DOC": "Document", "EVT": "Event", "PRV": "Provider", "POL": "Policy", "CUST": "Customer"}
ID_RE = re.compile(r"^(CLM|DOC|EVT|PRV|POL|CUST)-\d+$")


class EvidenceLookup:
    def __init__(self, cfg: dict):
        self.silver = duckdb.connect(str(paths(cfg)["silver"]), read_only=True)

    def close(self) -> None:
        self.silver.close()

    def _one(self, sql: str, *params) -> dict | None:
        cur = self.silver.execute(sql, list(params))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    def describe(self, evidence_id: str) -> dict:
        """{'id', 'kind', 'title', 'facts': [(label, value)], 'text': str | None}. Unknown IDs come back as kind 'Unknown', never an error."""
        m = ID_RE.match(evidence_id or "")
        if not m:
            return dict(id=evidence_id, kind="Unknown", title="Not a record ID", facts=[], text=None)
        kind = KINDS[m.group(1)]
        out = dict(id=evidence_id, kind=kind, title=kind, facts=[], text=None)
        if m.group(1) == "CLM":
            r = self._one("SELECT c.*, p.name AS provider_name, p.district AS provider_district, pr.name AS procedure_name FROM claims c "
                          "LEFT JOIN providers p USING (provider_id) LEFT JOIN procedures pr ON pr.code = c.procedure_code WHERE c.claim_id = ?", evidence_id)
            if r:
                outcome = {"confirmed_fraud": "confirmed as fraud by a reviewer", "cleared": "cleared by a reviewer"}.get(r["outcome"], "not decided yet")
                out.update(title=f"{r['line'].capitalize()} claim filed {r['filed_date']}", text=r["description"],
                           facts=[("Amount", f"Rs {r['amount']:,}"), ("Provider", r["provider_name"] or "-"), ("Procedure", r["procedure_name"] or "-"),
                                  ("Incident / admission", str(r["incident_date"] or r["admit_date"] or "-")), ("Invoice", r["invoice_no"] or "-"), ("Outcome", outcome)])
        elif m.group(1) == "DOC":
            r = self._one("SELECT * FROM documents WHERE doc_id = ?", evidence_id)
            if r:
                out.update(title=f"{r['doc_type'].replace('_', ' ').capitalize()} dated {r['doc_date']}", text=r["text"], facts=[("Claim", r["claim_id"])])
        elif m.group(1) == "EVT":
            r = self._one("SELECT * FROM events WHERE event_id = ?", evidence_id)
            if r:
                details = json.loads(r["details"])
                out.update(title=f"{r['event_type'].replace('_', ' ').capitalize()} on {r['event_date']}",
                           facts=[("Policy", r["policy_id"] or "-"), ("Claim", r["claim_id"] or "-")] + [(k.replace("_", " "), str(v)) for k, v in details.items()])
        elif m.group(1) == "PRV":
            r = self._one("SELECT * FROM providers WHERE provider_id = ?", evidence_id)
            if r:
                out.update(title=r["name"], facts=[("Kind", r["kind"]), ("District", r["district"]), ("Registration", r["reg_status"])])
        elif m.group(1) == "POL":
            r = self._one("SELECT * FROM policies WHERE policy_id = ?", evidence_id)
            if r:
                out.update(title=f"{r['line'].capitalize()} policy", facts=[("Started", str(r["start_date"])), ("Status", r["status"]), ("Sum insured", f"Rs {r['sum_insured']:,}"),
                                                                           ("Nominee relation", r["nominee_relation"] or "-"), ("Declared conditions", r["declared_conditions"] or "none")])
        else:
            out.update(title="Another customer (ID only; no personal details are shown)")
        if out["title"] == kind and not out["facts"] and out["text"] is None and m.group(1) != "CUST":
            out["title"] = f"{kind} {evidence_id} (not found)"
        return out
