"""Generate docs/data_dictionary.md from the actual silver and gold schemas (so it cannot drift)."""
from __future__ import annotations

from pathlib import Path

import duckdb

from .config import paths

DESCRIPTIONS = {
    "customer_id": "Opaque customer ID (CUST-000123), numbered in random order",
    "age_band": "Age in coarse bands; no exact age or date of birth is stored outside the vault",
    "gender": "Shown to reviewers as context; never used by the scorer; used only in fairness checks",
    "occupation_category": "Occupation group",
    "income_band": "Annual income band; feeds the cover-vs-income signal",
    "state": "State (hidden when the reviewer view is fully generalised)",
    "district": "District; in the reviewer view it falls back to the state when a group would be too small",
    "pincode": "PIN code. Backend only: used to compute area risk, never shown to reviewers",
    "urban_rural": "Urban, semi-urban or rural class of the area",
    "channel": "How the customer joined: agent, online or bank",
    "kyc_status": "KYC verification level",
    "tenure_band": "How long the customer has been with the insurer",
    "generalisation_level": "0 = exact district/gender/age shown; higher = more generalised so the group has at least k customers",
    "phone_token": "Keyed-hash token of the phone number; equal numbers give equal tokens",
    "address_token": "Keyed-hash token of the address",
    "bank_token": "Keyed-hash token of the bank account",
    "payout_token": "Keyed-hash token of the account the claim is paid to",
    "vehicle_token": "Keyed-hash token of the vehicle registration",
    "nominee_token": "Keyed-hash token of the nominee's name",
    "email_token": "Keyed-hash token of the email address",
    "kyc_token": "Keyed-hash token of the KYC identifier",
    "outcome": "Reviewer-confirmed result of a closed historical claim (confirmed_fraud or cleared); empty for open claims",
    "source": "history = past claim; eval = claim to be assessed",
    "case_id": "Case ID of a claim to be assessed (evaluation claims only)",
    "details": "JSON payload of the event with personal values replaced by tokens",
    "text": "Document text with names, numbers and addresses scrubbed",
    "area_basis": "Level the area rate is based on: pin, district or portfolio",
    "area_n": "Past closed claims behind the area rate (24-month window, decided before filing)",
    "area_rate": "Confirmed-fraud rate for the area and line, shrunk toward the district rate when a PIN has few claims",
    "area_ratio": "Area rate divided by the portfolio rate",
    "area_is_high": "Area flag: ratio at or above the configured threshold with enough claims behind it",
    "sum_insured_to_income_ratio": "Sum insured divided by the midpoint of the income band; feeds the cover-vs-income signal",
    "bill_vs_peer_median": "Claim amount divided by the median for the same procedure at other providers (point in time)",
    "policy_age_days": "Days from policy start to filing",
    "deceased": "True when the customer has died: every policy is terminated on the death date and no later activity exists",
    "status": "Policy status: active, lapsed, or terminated (the insured died); claim status: open, pending or closed",
}
SCORED = {"area_basis", "area_n", "area_fraud", "area_rate", "area_portfolio_rate", "area_ratio", "area_is_high", "sum_insured_to_income_ratio"}


def visibility(table: str, col: str) -> str:
    if col == "pincode":
        return "backend only"
    if col.endswith("_token") or (table == "entity_tokens" and col == "token"):
        return "token"
    if col == "gender":
        return "reviewer (context)"
    return "reviewer / agents"


def scored(col: str) -> str:
    return "signal input" if col in SCORED else ("never" if col == "gender" else "")


def write_dictionary(cfg: dict, out: Path | None = None) -> Path:
    p = paths(cfg)
    lines = ["# Data dictionary", "", "Generated from the silver and gold schemas by `claim_triage.data.dictionary`. "
             "Gold features are computed as of each claim's filing date.", ""]
    for layer in ("silver", "gold"):
        con = duckdb.connect(str(p[layer]), read_only=True)
        tables = [t for (t,) in con.execute("select table_name from information_schema.tables where table_schema='main' order by table_name").fetchall()]
        lines += [f"## {layer}", ""]
        for table in tables:
            n = con.execute(f"select count(*) from {table}").fetchone()[0]
            lines += [f"### {layer}.{table} ({n:,} rows)", "", "| Column | Type | Visibility | Scored | Meaning |", "|---|---|---|---|---|"]
            for col, typ in con.execute(f"select column_name, data_type from information_schema.columns where table_name='{table}' order by ordinal_position").fetchall():
                lines.append(f"| {col} | {typ} | {visibility(table, col)} | {scored(col)} | {DESCRIPTIONS.get(col, '')} |")
            lines.append("")
        con.close()
    out = out or Path(cfg["data_dir"]) / "reports" / "data_dictionary.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
