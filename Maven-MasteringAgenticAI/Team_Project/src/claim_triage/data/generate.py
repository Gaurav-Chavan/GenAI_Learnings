"""Raw layer: seeded synthetic customers, policies, claims, events and evaluation cases.

The raw layer deliberately contains fake personal data (names, phones, addresses); only the privacy
split (privacy_split.py) may read it. Hidden labels go to a separate file (labels.duckdb).
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import string
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta

import duckdb
from faker import Faker

from .config import paths, when
from .reference import (CAUSES, FAMILY, GEO, HOTSPOT_PINS, MOTOR_ITEMS, NON_FAMILY, OCCUPATION_INCOME_MEAN, PROC_BY_CODE,
                        PROC_FOR_CONDITION, PROC_WEIGHTS, PROCEDURES, SERIOUS_CONDITIONS, VEHICLES)

SCENARIOS = {
    "H1": "Duplicate health claim", "H2": "Inflated billing vs peers", "T1": "Nominee changed before death claim",
    "T2": "Conflicting death dates", "M1": "Repeat motor claim", "M2": "Damage already in prior inspection",
    "X1": "Non-disclosure of pre-existing condition", "X2": "Early term death vs health history",
    "X3": "Same accident claimed under two policies", "X4": "Shared-entity ring",
}
# Hard tier: probes of what a reading, searching agent can do that fixed rules cannot. Kept in their own label table so the 300 standard cases never change.
HARD_SCENARIOS = {
    "D1": "Large bill, explained (or not) in the discharge summary", "D2": "Slightly high bill at a provider with a bad track record",
}
COMPLICATIONS = [
    "Course complicated by a hospital-acquired infection treated with IV antibiotics; {icu} days in ICU and a prolonged stay.",
    "Unexpected internal bleeding required transfusion and {icu} days of ICU monitoring; an additional procedure was needed.",
    "Developed pneumonia during the stay; {icu} days in ICU with ventilator support and an extended stay.",
]
ROUTINE_NOTES = ["Uneventful recovery; discharged in stable condition on routine medication.", "Recovery as expected; discharged with follow-up advised in two weeks."]
NON_MEDICAL_NOTES = ["Patient requested a private deluxe room for the stay.", "Family opted for a private suite and a premium meal plan.",
                     "Family requested additional comfort services and an attendant room."]  # costs something, but does not explain a bill three times the norm


def add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year + n)


class Gen:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.rng = random.Random(cfg["seed"])
        self.fake = Faker("en_IN")
        self.fake.seed_instance(cfg["seed"])
        self.hist_start, self.cutoff = when(cfg, "history_start"), when(cfg, "history_cutoff")
        self.eval_start, self.eval_end, self.today = when(cfg, "eval_start"), when(cfg, "eval_end"), when(cfg, "dataset_today")
        self.customers: dict[int, dict] = {}
        self.policies: dict[str, dict] = {}
        self.claims: dict[str, dict] = {}
        self.providers: dict[str, dict] = {}
        self.mult: dict[str, float] = {}
        self.events: list[dict] = []
        self.docs: list[dict] = []
        self.labels: list[dict] = []
        self.hard_labels: list[dict] = []
        self.bad_providers: list[dict] = []
        self.clean_providers: list[dict] = []
        self.cust_policies: dict[int, list[str]] = defaultdict(list)
        self.count: dict[str, int] = defaultdict(int)
        self.used: set[int] = set()
        self.phones: set[str] = set()
        self.accounts: set[str] = set()
        self.areas, self.pin_info, self.districts = [], {}, {}
        self.hospitals: dict[str, list[str]] = defaultdict(list)
        self.garages: dict[str, list[str]] = defaultdict(list)
        self.hot_group: dict[bool, list[int]] = {True: [], False: []}
        self.deaths: dict[int, tuple[date, str]] = {}  # raw_id -> (date of death, claim that reports it)
        self.eval_customers: set[int] = set()
        self.reserved: set[int] = set()  # customers kept for death-claim scenarios only

    # ------------------------------------------------------------------ small helpers
    def nid(self, prefix: str, width: int = 6) -> str:
        self.count[prefix] += 1
        return f"{prefix}-{self.count[prefix]:0{width}d}"

    def rand_date(self, a: date, b: date) -> date:
        return a + timedelta(days=self.rng.randint(0, max(0, (b - a).days)))

    def poisson(self, lam: float) -> int:
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.rng.random()
            if p <= limit:
                return k
            k += 1

    def days(self, lo: int, hi: int) -> timedelta:
        return timedelta(days=self.rng.randint(lo, hi))

    def eval_filed(self) -> date:
        return self.rand_date(self.eval_start, self.eval_end)

    # ------------------------------------------------------------------ geography and providers
    def build_geo(self) -> None:
        for state, district, rto, pins in GEO:
            self.districts[district] = (state, rto, [p for p, _ in pins])
            for pin, cls in pins:
                self.areas.append((pin, district, state, cls))
                self.pin_info[pin] = (state, district, rto, cls)

    def build_providers(self) -> None:
        n = 0
        suffix = ["Memorial", "City Care", "Medicity", "Lifeline", "Sanjeevani", "Apex"]
        for district, (_state, _rto, pins) in self.districts.items():
            for kind, count in (("hospital", 7), ("garage", 5)):
                for _ in range(count):
                    n += 1
                    pid = f"PRV-{n:03d}"
                    name = (f"{self.fake.last_name()} {self.rng.choice(suffix)} Hospital" if kind == "hospital"
                            else f"{self.fake.last_name()} Motors Garage")
                    self.providers[pid] = dict(provider_id=pid, name=name, kind=kind, pincode=self.rng.choice(pins), district=district,
                                               reg_status="unverified" if self.rng.random() < 0.08 else "registered")
                    self.mult[pid] = min(1.25, max(0.8, self.rng.gauss(1.0, 0.08)))
                    (self.hospitals if kind == "hospital" else self.garages)[district].append(pid)

    def hospital_in(self, c: dict) -> dict:
        district = self.pin_info[c["pincode"]][1]
        pool = self.hospitals[district] if self.rng.random() < 0.85 else [p for v in self.hospitals.values() for p in v]
        return self.providers[self.rng.choice(pool)]

    def garage_in(self, c: dict, avoid: str | None = None) -> dict:
        district = self.pin_info[c["pincode"]][1]
        pool = [p for p in self.garages[district] if p != avoid] or self.garages[district]
        return self.providers[self.rng.choice(pool)]

    def proc_cost(self, proc, prov: dict) -> int:
        return int(round(max(500, proc.median * self.mult[prov["provider_id"]] * self.rng.lognormvariate(0, 0.15)), -1))

    def invoice(self, prov: dict) -> str:
        return f"INV-{prov['provider_id'][-3:]}-{self.nid('INV', 5)[-5:]}"

    # ------------------------------------------------------------------ people
    def unique_phone(self) -> str:
        while True:
            v = "+91" + str(self.rng.choice([6, 7, 8, 9])) + "".join(str(self.rng.randint(0, 9)) for _ in range(9))
            if v not in self.phones:
                self.phones.add(v)
                return v

    def unique_account(self) -> str:
        while True:
            v = "".join(str(self.rng.randint(0, 9)) for _ in range(12))
            if v not in self.accounts:
                self.accounts.add(v)
                return v

    def pick_occupation(self, age: int, gender: str) -> str:
        if age >= 60:
            w = {"retired": 0.55, "business_owner": 0.12, "self_employed": 0.12, "farmer": 0.08, "professional": 0.05,
                 "salaried_govt": 0.03, "homemaker": 0.05}
        else:
            w = {"salaried_private": 0.30, "salaried_govt": 0.10, "self_employed": 0.15, "business_owner": 0.12, "farmer": 0.10,
                 "professional": 0.10, "homemaker": 0.13 if gender == "F" else 0.01}
        return self.rng.choices(list(w), list(w.values()))[0]

    def make_customer(self) -> dict:
        rng, fake = self.rng, self.fake
        raw_id = len(self.customers) + 1
        gender = rng.choices(["M", "F", "O"], [0.52, 0.475, 0.005])[0]
        first = fake.first_name_male() if gender == "M" else fake.first_name_female() if gender == "F" else fake.first_name()
        last = fake.last_name()
        age = int(rng.triangular(21, 72, 38))
        occ = self.pick_occupation(age, gender)
        income = max(60_000, int(OCCUPATION_INCOME_MEAN[occ] * rng.lognormvariate(0, 0.5) / 5000) * 5000)
        weights = {"urban": 1.6, "semi-urban": 1.0, "rural": 0.6}
        pins = [a[0] for a in self.areas]
        pin = rng.choices(pins, [weights[self.pin_info[p][3]] for p in pins])[0]
        state, district, _rto, cls = self.pin_info[pin]
        c = dict(raw_id=raw_id, name=f"{first} {last}", first=first, last=last, age=age, gender=gender, occupation=occ,
                 annual_income=income, pincode=pin, state=state, district=district, urban_rural=cls,
                 phone=self.unique_phone(), bank_account=self.unique_account(),
                 email=f"{first}.{last}{rng.randint(1, 99)}@example.com".lower().replace(" ", ""),
                 address=f"Flat {rng.randint(1, 480)}, {fake.street_name()}, {district} {pin}",
                 dob=self.today - timedelta(days=age * 365 + rng.randint(0, 364)),
                 kyc_id="".join(rng.choice(string.ascii_uppercase) for _ in range(5)) + str(rng.randint(1000, 9999)) + rng.choice(string.ascii_uppercase),
                 channel=rng.choices(["agent", "online", "bank"], [0.5, 0.3, 0.2])[0],
                 kyc_status="verified" if rng.random() < 0.85 else "partial", signup_date=None, former_names="")
        self.customers[raw_id] = c
        return c

    def relative_name(self, c: dict, rel: str) -> str:
        if rel in FAMILY:
            return f"{self.fake.first_name()} {c['last']}"
        return f"{self.fake.first_name()} {self.fake.last_name()}"

    def make_policy(self, c: dict, line: str, start: date, lapse="random") -> dict:
        rng = self.rng
        p = dict(policy_id=self.nid("POL"), raw_id=c["raw_id"], line=line, start_date=start, status="active", lapse_date=None,
                 nominee_name="", nominee_relation="", vehicle_reg="", vehicle_model="", vehicle_segment="", declared_conditions="")
        if line == "health":
            si = rng.choice([300_000, 500_000, 1_000_000, 1_500_000, 2_500_000])
            prem = round(si * rng.uniform(0.016, 0.022), -1)
            if rng.random() < 0.10:
                p["declared_conditions"] = rng.choice(["diabetes", "cardiac", "respiratory"])
        elif line == "term":
            si = max(2_500_000, int(round(c["annual_income"] * rng.uniform(8, 18) / 500_000)) * 500_000)
            prem = round(si * rng.uniform(0.0012, 0.0024), -1)
            p["nominee_relation"] = rng.choice(FAMILY)
            p["nominee_name"] = self.relative_name(c, p["nominee_relation"])
        else:
            model, seg, value = rng.choice(VEHICLES)
            si = int(value * rng.uniform(0.7, 0.95))
            prem = round(si * rng.uniform(0.03, 0.04), -1)
            rto = self.pin_info[c["pincode"]][2]
            p.update(vehicle_reg=f"{rto}{''.join(rng.choice(string.ascii_uppercase) for _ in range(2))}{rng.randint(1000, 9999)}",
                     vehicle_model=model, vehicle_segment=seg)
        k = 20 if line == "term" else 1
        while line != "term" and add_years(start, k) <= self.today:
            k += 1
        p.update(sum_insured=int(si), premium=int(prem), end_date=add_years(start, k))
        if isinstance(lapse, date):
            p.update(status="lapsed", lapse_date=lapse)
        elif lapse == "random" and rng.random() < 0.08 and start + timedelta(days=120) < self.cutoff:
            p.update(status="lapsed", lapse_date=self.rand_date(start + timedelta(days=120), self.cutoff))
        self.policies[p["policy_id"]] = p
        self.cust_policies[c["raw_id"]].append(p["policy_id"])
        return p

    def policy_for(self, c: dict, line: str, start_before: date) -> dict:
        for pid in self.cust_policies[c["raw_id"]]:
            p = self.policies[pid]
            if p["line"] == line and p["status"] == "active" and p["start_date"] <= start_before:
                return p
        start = max(self.hist_start, start_before - self.days(120, 600))
        return self.make_policy(c, line, min(start, start_before), lapse=False)

    def choose_lines(self, c: dict) -> list[str]:
        rng, lines = self.rng, []
        if rng.random() < 0.85:
            lines.append("health")
        if (25 <= c["age"] <= 58 and rng.random() < 0.5) or (c["age"] > 58 and rng.random() < 0.08):
            lines.append("term")
        if rng.random() < {"urban": 0.55, "semi-urban": 0.35, "rural": 0.2}[c["urban_rural"]]:
            lines.append("motor")
        return lines or ["health"]

    def build_population(self) -> None:
        last_start = date(2026, 5, 15)
        for _ in range(self.cfg["n_customers"]):
            c = self.make_customer()
            adult = add_years(c["dob"], 18)  # nobody holds a policy before turning 18
            for line in self.choose_lines(c):
                self.make_policy(c, line, self.rand_date(max(self.hist_start, adult), last_start))
        for raw_id, c in self.customers.items():
            self.hot_group[c["pincode"] in HOTSPOT_PINS].append(raw_id)
        reserve = random.Random(self.cfg["seed"] + 99)  # its own stream, so other draws are unaffected
        for hot, k in ((True, 40), (False, 70)):
            adults = [r for r in self.hot_group[hot] if self.customers[r]["age"] >= 23]
            self.reserved.update(reserve.sample(adults, min(k, len(adults))))

    # ------------------------------------------------------------------ events and documents
    def add_event(self, c, etype: str, d: date, policy: str | None = None, claim: str | None = None, **details) -> None:
        raw_id = c["raw_id"] if isinstance(c, dict) else c
        self.events.append(dict(event_id=self.nid("EVT", 7), raw_id=raw_id, policy_id=policy, claim_id=claim, event_type=etype,
                                event_date=d, details=json.dumps(details, default=str, sort_keys=True)))

    def change_nominee(self, pol: dict, c: dict, d: date, rel: str) -> None:
        name = self.relative_name(c, rel)
        self.add_event(c, "nominee_changed", d, policy=pol["policy_id"], previous_relation=pol["nominee_relation"],
                       relation=rel, new_nominee_name=name)
        pol["nominee_relation"], pol["nominee_name"] = rel, name

    def gen_noise_events(self) -> None:
        rng = self.rng
        for c in self.customers.values():
            pols = [self.policies[p] for p in self.cust_policies[c["raw_id"]]]
            first = min(p["start_date"] for p in pols)
            lo = first + timedelta(days=30)
            if lo >= self.cutoff:
                continue
            years = (self.cutoff - first).days / 365
            for _ in range(self.poisson(0.10 * years)):
                self.add_event(c, "phone_changed", self.rand_date(lo, self.cutoff), new_phone=self.unique_phone())
            for _ in range(self.poisson(0.08 * years)):
                self.add_event(c, "address_changed", self.rand_date(lo, self.cutoff),
                               new_address=f"Flat {rng.randint(1, 480)}, {self.fake.street_name()}, {c['district']} {c['pincode']}")
            for _ in range(self.poisson(0.05 * years)):
                self.add_event(c, "bank_changed", self.rand_date(lo, self.cutoff), new_bank_account=self.unique_account())
            for p in pols:
                if p["line"] == "term":
                    for _ in range(self.poisson(0.06 * max(0.0, (self.cutoff - p["start_date"]).days / 365))):
                        self.change_nominee(p, c, self.rand_date(p["start_date"] + timedelta(days=30), self.cutoff), rng.choice(FAMILY))

    def doc_text(self, dtype: str, cl: dict, c: dict, pol: dict, prov: dict | None, proc, cause: str) -> str:
        name, pname = c["name"], (prov["name"] if prov else "n/a")
        district = cl["incident_district"]
        veh = cl["vehicle_reg"] or ""
        if dtype == "bill":
            return (f"Hospital bill {cl['invoice_no']}. Patient {name}. Provider {pname}. Procedure {proc.name if proc else 'consultation'}. "
                    f"Admitted {cl['admit_date']}, discharged {cl['discharge_date']}. Total Rs {cl['amount']:,}.")
        if dtype == "itemised_bill":
            return f"Itemised bill for {name} at {pname}: room charges, operation theatre, medicines, consumables. Total Rs {cl['amount']:,}."
        if dtype == "discharge_summary":
            return (f"Discharge summary for {name}. Diagnosis: {cl['condition_tag']}. Procedure: {proc.name if proc else 'n/a'}. "
                    f"Admission {cl['admit_date']}, discharge {cl['discharge_date']}. Treating doctor Dr {self.fake.last_name()}.")
        if dtype == "repair_estimate":
            return f"Repair estimate {cl['invoice_no']} for vehicle {veh}. Items: {cl['damage_items']}. Garage {pname}. Total Rs {cl['amount']:,}."
        if dtype == "fir":
            return f"FIR: accident on {cl['incident_date']} in {district}. Complainant {name}. Vehicle {veh}."
        if dtype == "inspection_report":
            return f"Inspection of {veh}: damage noted {cl['damage_items']}. Surveyor visit after claim {cl['claim_id']}."
        if dtype == "death_certificate":
            return f"Death certificate: {name} died on {cl['cert_date'] or cl['incident_date']}. Issued by {pname}, {district}. Cause: {cause}."
        if dtype == "claim_form":
            if cause.startswith("critical illness"):
                return f"Claim form: critical illness benefit for {name}, diagnosis dated {cl['incident_date']}."
            return f"Claim form: death of {name} on {cl['incident_date']}. Nominee {pol['nominee_name']} ({pol['nominee_relation']})."
        if dtype == "medical_report":
            return f"Medical report for {name}: {cause}, diagnosed on {cl['incident_date']}. Specialist Dr {self.fake.last_name()}."
        if dtype == "nominee_consent":
            return f"Nominee consent: {name} confirms nominee {pol['nominee_name']}, relation {pol['nominee_relation']}, signed with witness."
        if dtype == "cause_of_death":
            return f"Cause of death report for {name}: {cause}. Medical practitioner Dr {self.fake.last_name()}."
        if dtype == "correction_affidavit":
            return f"Affidavit by {pol['nominee_name']}: date of death corrected from {cl['incident_date']} to {cl['cert_date']} due to clerical error."
        if dtype == "provider_registration":
            return f"Registration certificate: {pname} is licensed to operate in {district}."
        return f"Document {dtype} for claim {cl['claim_id']}."

    def add_claim(self, pol: dict, filed: date, *, source: str, amount: float, prov: dict | None = None, proc=None,
                  admit: date | None = None, disc: date | None = None, incident: date | None = None, damage=None,
                  invoice: str | None = None, incident_district: str | None = None, payout: str | None = None,
                  docs=(), cert_date: date | None = None, cause: str = "", outcome: str | None = None,
                  decided: date | None = None, status: str = "open", case_id: str | None = None) -> dict:
        c = self.customers[pol["raw_id"]]
        cid = self.nid("CLM")
        if source == "history" and status == "open":
            status = "pending"  # supporting claims for a scenario: no outcome yet
        district = incident_district or self.pin_info[c["pincode"]][1]
        cl = dict(claim_id=cid, policy_id=pol["policy_id"], raw_id=c["raw_id"], line=pol["line"], filed_date=filed, incident_date=incident,
                  admit_date=admit, discharge_date=disc, procedure_code=proc.code if proc else None,
                  condition_tag=proc.condition if proc else None, damage_items=";".join(damage) if damage else None,
                  amount=int(amount), provider_id=prov["provider_id"] if prov else None, invoice_no=invoice, incident_district=district,
                  status=status, decided_date=decided, outcome=outcome, payout_account=payout or c["bank_account"],
                  vehicle_reg=pol["vehicle_reg"] or None, cert_date=cert_date, description="", source=source, case_id=case_id)
        if pol["line"] == "health":
            cl["description"] = f"{c['name']} admitted at {prov['name'] if prov else 'a hospital'} for {proc.name if proc else 'treatment'}."
        elif pol["line"] == "motor":
            cl["description"] = f"Accident involving {pol['vehicle_reg']} in {district}; damage to {cl['damage_items']}. Insured {c['name']}."
        elif cause.startswith("critical illness"):
            cl["description"] = f"Critical illness rider claim for {c['name']}; {cause}."
        else:
            cl["description"] = f"Death claim for {c['name']}; cause {cause}; nominee {pol['nominee_name']}."
        self.claims[cid] = cl
        if source == "eval":
            self.eval_customers.add(c["raw_id"])
        self.add_event(c, "claim_filed", filed, policy=pol["policy_id"], claim=cid, line=pol["line"], amount=cl["amount"])
        if admit:
            self.add_event(c, "hospital_admitted", admit, policy=pol["policy_id"], claim=cid, provider=cl["provider_id"])
        if disc:
            self.add_event(c, "hospital_discharged", disc, policy=pol["policy_id"], claim=cid)
        if incident and pol["line"] == "motor":
            self.add_event(c, "accident_reported", incident, policy=pol["policy_id"], claim=cid, district=district)
        if docs:
            self.add_event(c, "documents_submitted", filed + timedelta(days=1), policy=pol["policy_id"], claim=cid, documents=list(docs))
        if source == "history" and pol["line"] == "motor" and damage:
            self.add_event(c, "vehicle_inspected", filed + timedelta(days=2), policy=pol["policy_id"], claim=cid, vehicle=pol["vehicle_reg"], damage=list(damage))
            if decided and outcome == "cleared":
                self.add_event(c, "vehicle_repaired", decided + timedelta(days=10), policy=pol["policy_id"], claim=cid, vehicle=pol["vehicle_reg"], provider=cl["provider_id"])
        if decided:
            self.add_event(c, "claim_decided", decided, policy=pol["policy_id"], claim=cid, result=outcome)
        for dtype in docs:
            self.docs.append(dict(doc_id=self.nid("DOC", 7), claim_id=cid, doc_type=dtype, doc_date=filed,
                                  text=self.doc_text(dtype, cl, c, pol, prov, proc, cause)))
        return cl

    # ------------------------------------------------------------------ history base
    def gen_history(self) -> None:
        rng, rate, base = self.rng, self.cfg["claim_rate_per_year"], self.cfg["base_fraud_rate"]
        mult = self.cfg["hotspot_multiplier"]
        proc_pool = [PROC_BY_CODE[k] for k in PROC_WEIGHTS]
        proc_w = list(PROC_WEIGHTS.values())
        for pol in list(self.policies.values()):
            c = self.customers[pol["raw_id"]]
            lo = pol["start_date"] + timedelta(days=15)
            hi = min(self.cutoff, pol["lapse_date"] or self.cutoff)
            if hi <= lo:
                continue
            for _ in range(self.poisson(rate[pol["line"]] * (hi - lo).days / 365)):
                filed = self.rand_date(lo, hi)
                kw: dict = {}
                if pol["line"] == "health":
                    proc, prov = rng.choices(proc_pool, proc_w)[0], self.hospital_in(c)
                    stay = 0 if proc.code == "P00" else rng.randint(1, 5)
                    admit = filed - self.days(stay + 2 if stay else 1, 25 if stay else 10)
                    kw = dict(proc=proc, prov=prov, amount=self.proc_cost(proc, prov), admit=admit, disc=admit + timedelta(days=stay),
                              invoice=self.invoice(prov), docs=("bill",) if proc.code == "P00" else ("bill", "discharge_summary"))
                elif pol["line"] == "motor":
                    items = rng.sample(list(MOTOR_ITEMS), rng.randint(1, 3))
                    prov = self.garage_in(c)
                    cost = sum(MOTOR_ITEMS[i] for i in items) * rng.uniform(0.9, 1.25) * self.mult[prov["provider_id"]]
                    kw = dict(prov=prov, amount=round(cost, -1), incident=filed - self.days(1, 12), damage=items,
                              invoice=self.invoice(prov), docs=("repair_estimate",))
                else:
                    # living-benefit claims (critical illness rider): history never contains a death, so no customer "dies" and stays active
                    prov = self.hospital_in(c)
                    cause = "critical illness: " + rng.choice(["cardiac condition", "cancer", "kidney failure"])
                    kw = dict(prov=prov, amount=int(round(pol["sum_insured"] * 0.25, -3)), incident=filed - self.days(15, 40), cause=cause,
                              docs=("medical_report", "claim_form"))
                p_fraud = base[pol["line"]] * (mult if c["pincode"] in HOTSPOT_PINS else 1.0)
                decided = filed + self.days(8, 45)
                if decided > self.cutoff:
                    self.add_claim(pol, filed, source="history", status="pending", **kw)
                else:
                    outcome = "confirmed_fraud" if rng.random() < p_fraud else "cleared"
                    self.add_claim(pol, filed, source="history", status="closed", outcome=outcome, decided=decided, **kw)

    # ------------------------------------------------------------------ evaluation scenarios
    def pick(self, hot: bool, pred=None, term: bool = False) -> dict:
        """Choose a customer in the hotspot (or ordinary) group. `term=True` draws from a reserved pool used only by death-claim scenarios,
        so a death can never collide with another evaluation claim. Hard rules (adult, alive) are never relaxed; `pred` is a soft preference."""
        group = [r for r in self.hot_group[hot] if (r in self.reserved) == term]

        def ok(r: int, soft: bool = True) -> bool:
            c = self.customers[r]
            return c["age"] >= 23 and r not in self.deaths and (not soft or pred is None or pred(c))

        for pool in ([r for r in group if r not in self.used and ok(r)], [r for r in group if ok(r)], [r for r in group if ok(r, False)]):
            if pool:
                r = self.rng.choice(pool)
                self.used.add(r)
                return self.customers[r]
        raise RuntimeError(f"no eligible customer (hotspot={hot}, term={term})")

    def label(self, cid, sid, kind, claim, hot, ind=(), absent=(), missing=(), band=None) -> None:
        band = band or {"suspicious": "red", "missing": "amber"}.get(kind, "green")
        target = self.hard_labels if sid in HARD_SCENARIOS else self.labels
        target.append(dict(case_id=cid, claim_id=claim["claim_id"], scenario_id=sid, scenario_name={**SCENARIOS, **HARD_SCENARIOS}[sid], kind=kind,
                           is_fraud=(kind == "suspicious"), expected_band=band, expected_indicators=";".join(ind),
                           expected_absent=";".join(absent), expected_missing=";".join(missing), hotspot_pin=hot, split=None))

    def sc_h1(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        pol, prov = self.policy_for(c, "health", filed - timedelta(days=130)), None  # old enough for the earlier supporting claim
        prov = self.hospital_in(c)
        procs = [p for p in PROCEDURES if not p.complex and p.code != "P00"]
        proc = rng.choice(procs)
        base = self.proc_cost(proc, prov)
        if kind == "honest":
            a_proc = rng.choice([p for p in procs if p.code != proc.code] + [PROC_BY_CODE["P00"]])
            a_filed = filed - self.days(40, 120)
            a_admit = a_filed - self.days(6, 12)
            self.add_claim(pol, a_filed, source="history", amount=self.proc_cost(a_proc, prov), prov=prov, proc=a_proc, admit=a_admit,
                           disc=a_admit + self.days(0, 3), invoice=self.invoice(prov), docs=("bill",))
            admit = filed - self.days(5, 12)
            b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=base, prov=prov, proc=proc, admit=admit,
                               disc=admit + self.days(2, 5), invoice=self.invoice(prov), docs=("bill", "discharge_summary"))
            self.label(cid, "H1", kind, b, hot, absent=["DUPLICATE_CLAIM"])
            return
        # suspicious: the same treatment again on (almost) the same dates. missing: the same treatment again but weeks later, with the bill
        # missing: ambiguous (a repeat treatment or a duplicate?), so it must not look conclusive.
        later = kind == "missing"
        a_filed = filed - (self.days(25, 40) if later else self.days(3, 14))
        a_admit = a_filed - self.days(6, 14)
        a_disc = a_admit + self.days(2, 5)
        inv = self.invoice(prov)
        self.add_claim(pol, a_filed, source="history", amount=base, prov=prov, proc=proc, admit=a_admit, disc=a_disc, invoice=inv,
                       docs=("bill", "discharge_summary"))
        exact = kind == "suspicious" and rng.random() < 0.7
        docs = ("bill", "discharge_summary") if kind == "suspicious" else ("discharge_summary",)
        b_admit = a_disc + self.days(10, 20) if later else a_admit + timedelta(days=rng.choice([0, 0, 1]))
        b_disc = b_admit + self.days(2, 5) if later else a_disc
        b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=round(base * rng.uniform(0.98, 1.03), -1), prov=prov, proc=proc,
                           admit=b_admit, disc=b_disc, invoice=inv if exact else self.invoice(prov), docs=docs)
        if kind == "suspicious":
            self.label(cid, "H1", kind, b, hot, ind=["DUPLICATE_CLAIM"])
        else:
            self.label(cid, "H1", kind, b, hot, missing=["original_bill"])

    def sc_h2(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        pol, prov = self.policy_for(c, "health", filed - timedelta(days=90)), self.hospital_in(c)
        admit = filed - self.days(5, 12)
        if kind == "honest":
            proc = rng.choice([p for p in PROCEDURES if p.complex])
            amount, docs = proc.median * self.mult[prov["provider_id"]] * rng.uniform(0.9, 1.25), ("bill", "itemised_bill", "discharge_summary")
        else:
            proc = rng.choice([p for p in PROCEDURES if not p.complex and p.code != "P00"])
            amount = proc.median * self.mult[prov["provider_id"]] * (rng.uniform(2.7, 3.6) if kind == "suspicious" else rng.uniform(2.1, 2.4))
            docs = ("bill", "itemised_bill", "discharge_summary") if kind == "suspicious" else ("bill", "discharge_summary")
        b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=round(amount, -1), prov=prov, proc=proc, admit=admit,
                           disc=admit + self.days(2, 5), invoice=self.invoice(prov), docs=docs)
        self.label(cid, "H2", kind, b, hot, ind=["BILL_OUTLIER_VS_PEERS"] if kind == "suspicious" else [],
                   absent=["BILL_OUTLIER_VS_PEERS"] if kind == "honest" else [], missing=["itemised_bill"] if kind == "missing" else [])

    def term_claim(self, pol, c, filed, death, cause, docs, cert_date, cid):
        cl = self.add_claim(pol, filed, source="eval", case_id=cid, amount=pol["sum_insured"], prov=self.hospital_in(c),
                            incident=death, cause=cause, docs=docs, cert_date=cert_date)
        self.deaths[c["raw_id"]] = (death, cl["claim_id"])
        return cl

    def sc_t1(self, kind, hot, cid):
        rng = self.rng
        filed = self.eval_filed()
        c = self.pick(hot, term=True)
        death = filed - self.days(10, 40)
        pol = self.policy_for(c, "term", death - timedelta(days=800))
        if kind == "honest":
            self.change_nominee(pol, c, death - self.days(500, 700), rng.choice(FAMILY))
            docs = ("death_certificate", "claim_form", "cause_of_death", "nominee_consent")
        else:
            rel = rng.choice(NON_FAMILY) if kind == "suspicious" else rng.choice(FAMILY)
            self.change_nominee(pol, c, death - self.days(5, 30), rel)
            docs = (("death_certificate", "claim_form", "cause_of_death", "nominee_consent") if kind == "suspicious"
                    else ("death_certificate", "claim_form", "cause_of_death"))
        cl = self.term_claim(pol, c, filed, death, rng.choice(CAUSES), docs, death, cid)
        self.label(cid, "T1", kind, cl, hot, ind=["NOMINEE_CHANGE_NEAR_EVENT"] if kind == "suspicious" else [],
                   absent=["NOMINEE_CHANGE_NEAR_EVENT"] if kind == "honest" else [], missing=["nominee_consent"] if kind == "missing" else [])

    def sc_t2(self, kind, hot, cid):
        rng = self.rng
        filed = self.eval_filed()
        c = self.pick(hot, term=True)
        death = filed - self.days(15, 40)
        pol = self.policy_for(c, "term", death - timedelta(days=400))
        cause = rng.choice(CAUSES)
        if kind == "suspicious":
            cert, docs = death + timedelta(days=rng.choice([-1, 1]) * rng.randint(15, 90)), ("death_certificate", "claim_form", "cause_of_death")
        elif kind == "honest":
            cert, docs = death + timedelta(days=rng.choice([-2, -1, 1, 2])), ("death_certificate", "claim_form", "cause_of_death", "correction_affidavit")
        else:
            cert, docs = None, ("claim_form", "cause_of_death")
        cl = self.term_claim(pol, c, filed, death, cause, docs, cert, cid)
        self.label(cid, "T2", kind, cl, hot, ind=["DOC_DATE_MISMATCH"] if kind == "suspicious" else [],
                   absent=["DOC_DATE_MISMATCH"] if kind == "honest" else [], missing=["death_certificate"] if kind == "missing" else [])

    def motor_claim(self, pol, filed, items, prov, incident, source, docs, cid=None, outcome=None, decided=None, status="open"):
        cost = sum(MOTOR_ITEMS[i] for i in items) * self.rng.uniform(0.9, 1.25) * self.mult[prov["provider_id"]]
        return self.add_claim(pol, filed, source=source, case_id=cid, amount=round(cost, -1), prov=prov, incident=incident, damage=items,
                              invoice=self.invoice(prov), docs=docs, outcome=outcome, decided=decided, status=status)

    def sc_m1(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        pol, prov = self.policy_for(c, "motor", filed - timedelta(days=330)), None  # old enough for an earlier claim up to 300 days back
        prov = self.garage_in(c)
        items = rng.sample(list(MOTOR_ITEMS), rng.randint(2, 3))
        if kind == "suspicious":
            c0_filed = filed - self.days(15, 50)
            incident = c0_filed - self.days(1, 5)
            self.motor_claim(pol, c0_filed, items, prov, incident, "history", ("repair_estimate", "fir"))
            docs, new_items, new_inc, new_prov = ("repair_estimate", "fir", "inspection_report"), items, incident + timedelta(days=rng.choice([0, 0, 1])), prov
        elif kind == "honest":
            c0_inc = filed - self.days(150, 300)
            other = [i for i in MOTOR_ITEMS if i not in items]
            self.motor_claim(pol, c0_inc + self.days(1, 3), rng.sample(other, 2), self.garage_in(c, prov["provider_id"]), c0_inc, "history", ("repair_estimate", "fir"))
            docs, new_items, new_inc, new_prov = ("repair_estimate", "fir", "inspection_report"), items, filed - self.days(2, 10), prov
        else:
            c0_filed = filed - self.days(15, 50)
            c0_inc = c0_filed - self.days(1, 5)
            other = [i for i in MOTOR_ITEMS if i not in items]
            self.motor_claim(pol, c0_filed, rng.sample(other, 2), self.garage_in(c, prov["provider_id"]), c0_inc, "history", ("repair_estimate", "fir"))
            # a recent earlier claim on the same vehicle (ambiguous) but a clearly different accident date, and the police report is missing
            docs, new_items, new_inc, new_prov = ("repair_estimate",), items, c0_inc + self.days(4, 9), prov
        cl = self.motor_claim(pol, filed, new_items, new_prov, new_inc, "eval", docs, cid=cid)
        self.label(cid, "M1", kind, cl, hot, ind=["REPEAT_INCIDENT_CLAIM"] if kind == "suspicious" else [],
                   absent=["REPEAT_INCIDENT_CLAIM"] if kind == "honest" else [], missing=["fir"] if kind == "missing" else [])

    def sc_m2(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        pol, prov = self.policy_for(c, "motor", filed - timedelta(days=250)), None
        prov = self.garage_in(c)
        items = rng.sample(list(MOTOR_ITEMS), rng.randint(2, 3))
        incident = filed - self.days(2, 10)
        inspected = incident - self.days(30, 120)
        if kind == "missing":
            claimed, docs = rng.sample(list(MOTOR_ITEMS), 2), ("repair_estimate", "fir")
        else:
            self.add_event(c, "vehicle_inspected", inspected, policy=pol["policy_id"], vehicle=pol["vehicle_reg"], damage=list(items), purpose="renewal")
            if kind == "suspicious":  # the label says "no repair on record since the inspection": drop any unrelated repair in that window
                self.events = [e for e in self.events if not (e["event_type"] == "vehicle_repaired" and e["raw_id"] == c["raw_id"]
                                                              and inspected < e["event_date"] <= incident
                                                              and json.loads(e["details"]).get("vehicle") == pol["vehicle_reg"])]
            if kind == "honest":
                self.add_event(c, "vehicle_repaired", inspected + self.days(5, 20), policy=pol["policy_id"], vehicle=pol["vehicle_reg"], provider=prov["provider_id"])
            claimed, docs = list(items), ("repair_estimate", "fir", "inspection_report")
        cl = self.motor_claim(pol, filed, claimed, prov, incident, "eval", docs, cid=cid)
        self.label(cid, "M2", kind, cl, hot, ind=["DAMAGE_IN_PRIOR_INSPECTION"] if kind == "suspicious" else [],
                   absent=["DAMAGE_IN_PRIOR_INSPECTION"] if kind == "honest" else [], missing=["inspection_report"] if kind == "missing" else [])

    def sc_x1(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        seen = {cl["condition_tag"] for cl in self.claims.values() if cl["raw_id"] == c["raw_id"] and cl["condition_tag"]}
        cond = rng.choice([x for x in ("diabetes", "respiratory", "cardiac") if x not in seen] or ["diabetes"])
        proc = PROC_BY_CODE[PROC_FOR_CONDITION[cond]]
        start = filed - self.days(30, 85)
        variant = "declared" if kind == "honest" and rng.random() < 0.5 else ("none" if kind == "honest" else "prior")
        prov = self.hospital_in(c)
        if variant != "none":
            prior_start = max(self.hist_start, start - self.days(500, 800))
            lapse = start - self.days(5, 40)
            prior = self.make_policy(c, "health", prior_start, lapse=lapse)
            a_filed = self.rand_date(prior_start + timedelta(days=30), min(start - timedelta(days=30), lapse - timedelta(days=1)))
            a_admit = a_filed - self.days(6, 12)
            # missing-evidence cases carry only a weak hint: the earlier claim is for an unrelated condition (dengue), not the same one
            prior_proc = PROC_BY_CODE["P05"] if kind == "missing" else proc
            self.add_claim(prior, a_filed, source="history", amount=self.proc_cost(prior_proc, prov), prov=prov, proc=prior_proc, admit=a_admit,
                           disc=a_admit + self.days(2, 5), invoice=self.invoice(prov), docs=("bill", "discharge_summary"))
        pol = self.make_policy(c, "health", start, lapse=False)
        pol["declared_conditions"] = cond if variant == "declared" else ""
        admit = filed - self.days(5, 15)
        docs = ("bill",) if kind == "missing" else ("bill", "discharge_summary")
        b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=self.proc_cost(proc, prov), prov=prov, proc=proc, admit=admit,
                           disc=admit + self.days(2, 5), invoice=self.invoice(prov), docs=docs)
        self.label(cid, "X1", kind, b, hot, ind=["PRE_EXISTING_NEAR_INCEPTION"] if kind == "suspicious" else [],
                   absent=["PRE_EXISTING_NEAR_INCEPTION"] if kind == "honest" else [], missing=["discharge_summary"] if kind == "missing" else [])

    def has_serious_history(self, c: dict) -> bool:
        return any(cl["raw_id"] == c["raw_id"] and cl["condition_tag"] in SERIOUS_CONDITIONS for cl in self.claims.values())

    def sc_x2(self, kind, hot, cid):
        rng = self.rng
        # honest and missing cases must not carry an accidental serious-illness history, or they would look like real non-disclosure
        filed = self.eval_filed()
        c = self.pick(hot, lambda x: kind == "suspicious" or not self.has_serious_history(x), term=True)
        death = filed - self.days(5, 25)
        late = kind == "honest" and rng.random() < 0.5
        start = death - (self.days(700, 1000) if late else self.days(20, 110))
        start = max(self.hist_start, start)
        pol = self.make_policy(c, "term", start, lapse=False)
        pol["declared_conditions"] = ""
        if kind in ("suspicious", "missing"):
            hp = self.make_policy(c, "health", max(self.hist_start, start - self.days(300, 600)), lapse=False)
            prov = self.hospital_in(c)
            proc = PROC_BY_CODE["P04" if kind == "suspicious" and rng.random() < 0.6 else "P12" if kind == "suspicious" else "P05"]
            a_filed = self.rand_date(max(hp["start_date"] + timedelta(days=30), start - timedelta(days=240)), start - timedelta(days=20))
            a_admit = a_filed - self.days(6, 12)
            self.add_claim(hp, a_filed, source="history", amount=self.proc_cost(proc, prov), prov=prov, proc=proc, admit=a_admit,
                           disc=a_admit + self.days(2, 5), invoice=self.invoice(prov), docs=("bill", "discharge_summary"))
        cause = "road accident" if kind == "honest" and not late else rng.choice(["cardiac arrest", "illness"])
        docs = ("death_certificate", "claim_form", "cause_of_death") if kind != "missing" else ("death_certificate", "claim_form")
        if cause == "road accident":
            docs += ("fir",)
        cl = self.term_claim(pol, c, filed, death, cause, docs, death, cid)
        self.label(cid, "X2", kind, cl, hot, ind=["EARLY_DEATH_VS_HEALTH_HISTORY"] if kind == "suspicious" else [],
                   absent=["EARLY_DEATH_VS_HEALTH_HISTORY"] if kind == "honest" else [], missing=["cause_of_death"] if kind == "missing" else [])

    def sc_x3(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        mpol = self.policy_for(c, "motor", filed - timedelta(days=300))
        hpol = self.policy_for(c, "health", filed - timedelta(days=200))
        incident = filed - self.days(8, 22)
        garage, hospital = self.garage_in(c), self.hospital_in(c)
        mdocs = ("repair_estimate", "fir") if kind != "missing" else ("repair_estimate",)
        self.motor_claim(mpol, incident + self.days(1, 4), rng.sample(list(MOTOR_ITEMS), 2), garage, incident, "history", mdocs)
        district = self.pin_info[c["pincode"]][1]
        other = [d for d in self.districts if d != district]
        proc = PROC_BY_CODE["P08"]
        if kind == "suspicious":
            if rng.random() < 0.5:
                admit, claimed = incident - self.days(2, 6), incident
            else:
                admit, claimed = incident + self.days(0, 1), incident + self.days(3, 6)
            inc_district = rng.choice(other) if rng.random() < 0.4 else district
        elif kind == "honest":
            admit, claimed, inc_district = incident + self.days(0, 1), incident, district
        else:
            admit, claimed, inc_district = incident + self.days(0, 1), incident + self.days(1, 2), district
        b = self.add_claim(hpol, filed, source="eval", case_id=cid, amount=self.proc_cost(proc, hospital) * (1.4 if kind == "suspicious" else 1.0),
                           prov=hospital, proc=proc, admit=admit, disc=admit + self.days(2, 5), incident=claimed, incident_district=inc_district,
                           invoice=self.invoice(hospital), docs=("bill", "discharge_summary"))
        self.label(cid, "X3", kind, b, hot, ind=["CROSS_POLICY_INCIDENT_MISMATCH"] if kind == "suspicious" else [],
                   absent=["CROSS_POLICY_INCIDENT_MISMATCH"] if kind == "honest" else [], missing=["fir"] if kind == "missing" else [])

    def sc_x4(self, kind, hot, cid):
        rng = self.rng
        c, filed = self.pick(hot), self.eval_filed()
        pol = self.policy_for(c, "motor", filed - timedelta(days=200))
        garage = self.garage_in(c)
        items = rng.sample(list(MOTOR_ITEMS), 2)
        if kind == "honest":  # relatives share phone and address, but file independent claims elsewhere
            for _ in range(rng.randint(1, 2)):
                rel = self.pick(hot)
                rel["former_names"] = "|".join(filter(None, [rel["former_names"], rel["name"]]))  # earlier claims and documents carry the old name: it is personal data too
                rel.update(last=c["last"], name=f"{rel['first']} {c['last']}", phone=c["phone"], address=c["address"], pincode=c["pincode"],
                           state=c["state"], district=c["district"], urban_rural=c["urban_rural"])
                rpol = self.policy_for(rel, "motor", filed - timedelta(days=200))
                r_filed = filed - self.days(20, 120)
                self.motor_claim(rpol, r_filed, rng.sample(list(MOTOR_ITEMS), 2), self.garage_in(rel, garage["provider_id"]),
                                 r_filed - self.days(1, 5), "history", ("repair_estimate", "fir"))
            docs, payout = ("repair_estimate", "fir", "inspection_report"), None
        else:
            shared = self.unique_account()
            size = rng.randint(4, 6) if kind == "suspicious" else rng.randint(2, 3)
            for _ in range(size):
                other = self.pick(False)
                opol = self.policy_for(other, "motor", filed - timedelta(days=200))
                o_filed = filed - self.days(10, 60)
                self.add_claim(opol, o_filed, source="history", amount=round(sum(MOTOR_ITEMS[i] for i in items) * rng.uniform(0.9, 1.2), -1),
                               prov=garage, incident=o_filed - self.days(1, 5), damage=items, invoice=self.invoice(garage), payout=shared,
                               docs=("repair_estimate", "fir"))
            docs, payout = ("repair_estimate", "fir", "inspection_report"), shared
        docs = docs if kind == "missing" else docs + ("provider_registration",)
        cl = self.add_claim(pol, filed, source="eval", case_id=cid, amount=round(sum(MOTOR_ITEMS[i] for i in items) * rng.uniform(0.9, 1.2), -1),
                            prov=garage, incident=filed - self.days(2, 10), damage=items, invoice=self.invoice(garage), payout=payout, docs=docs)
        self.label(cid, "X4", kind, cl, hot, ind=["SHARED_ENTITY_CLUSTER"] if kind == "suspicious" else [],
                   absent=["SHARED_ENTITY_CLUSTER"] if kind == "honest" else [], missing=["provider_registration"] if kind == "missing" else [])

    def run_scenarios(self) -> None:
        ev, rng = self.cfg["eval"], self.rng
        per = ev["per_scenario"]
        n_sus, n_hon = round(per * ev["suspicious"]), round(per * ev["honest"])
        kinds = ["suspicious"] * n_sus + ["honest"] * n_hon + ["missing"] * (per - n_sus - n_hon)
        seq = 0
        for sid in SCENARIOS:
            order = kinds[:]
            rng.shuffle(order)
            seen: dict[str, int] = defaultdict(int)
            total = Counter(order)
            for kind in order:
                seq += 1
                cid = f"CASE-{seq:04d}"
                getattr(self, f"sc_{sid.lower()}")(kind, rng.random() < ev["hotspot_share"], cid)
                # stratified hold-out: about 1/heldout_every of each kind, spread evenly (10 of 30 per scenario)
                n, h, i = total[kind], round(total[kind] / ev["heldout_every"]), seen[kind]
                self.labels[-1]["split"] = "heldout" if ((i + 1) * h) // n > (i * h) // n else "dev"
                seen[kind] += 1

    # ------------------------------------------------------------------ hard tier
    def append_doc_text(self, claim_id: str, dtype: str, note: str) -> None:
        doc = next(d for d in reversed(self.docs) if d["claim_id"] == claim_id and d["doc_type"] == dtype)
        doc["text"] = f"{doc['text']} {note}"

    def prepare_track_records(self) -> None:
        """Pick hospitals with a bad record (several reviewer-confirmed frauds in the last two years) and hospitals with a clean one."""
        rng = self.rng
        window_start = self.eval_start - timedelta(days=730)  # the track record the retrieval tool reports: decisions in the last 24 months
        frauds = Counter(cl["provider_id"] for cl in self.claims.values()
                         if cl["outcome"] == "confirmed_fraud" and cl["decided_date"] and cl["decided_date"] >= window_start)
        hospitals = [p for p in self.providers.values() if p["kind"] == "hospital"]
        quiet = sorted((p for p in hospitals if frauds[p["provider_id"]] <= 1), key=lambda p: p["provider_id"])
        rng.shuffle(quiet)
        self.bad_providers = quiet[:4]  # these get 4-5 confirmed frauds added below
        self.clean_providers = [p for p in quiet[4:] if frauds[p["provider_id"]] <= 1]  # the control hospitals keep at most one: "fewer than 3" is clean
        assert len(self.clean_providers) >= 6, "not enough quiet hospitals for the control cases"
        procs = [p for p in PROCEDURES if not p.complex and p.code != "P00"]
        for prov in self.bad_providers:
            for _ in range(rng.randint(4, 5)):
                c = self.pick(True)  # customers from the designed high-fraud areas, so the extra confirmed frauds do not dilute the area statistics
                filed = date.fromisoformat(self.cfg["hard"]["record_date"]) + self.days(0, 2)  # after the last standard claim: invisible to the 300 standard cases
                pol, proc = self.policy_for(c, "health", filed - timedelta(days=60)), rng.choice(procs)
                admit = filed - self.days(5, 12)
                self.add_claim(pol, filed, source="history", status="closed", outcome="confirmed_fraud", decided=filed + self.days(6, 8),
                               amount=round(proc.median * rng.uniform(2.2, 3.2), -1), prov=prov, proc=proc, admit=admit, disc=admit + self.days(2, 5),
                               invoice=self.invoice(prov), docs=("bill", "itemised_bill", "discharge_summary"))

    def hard_filed(self) -> date:
        h = self.cfg["hard"]
        return self.rand_date(date.fromisoformat(h["cases_start"]), date.fromisoformat(h["cases_end"]))

    def sc_d1(self, kind, hot, cid):
        """The same large bill, twice: one discharge summary documents a medical reason (honest), the other does not (suspicious)."""
        rng = self.rng
        c, filed = self.pick(hot), self.hard_filed()
        pol, prov = self.policy_for(c, "health", filed - timedelta(days=90)), self.hospital_in(c)
        proc = rng.choice([p for p in PROCEDURES if not p.complex and p.code != "P00"])
        stay = rng.randint(6, 14) if kind == "honest" else rng.randint(2, 14)  # long stays also occur without a reason, so length alone does not give it away
        admit = filed - timedelta(days=stay + rng.randint(3, 8))
        b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=round(proc.median * rng.uniform(2.8, 3.5), -1), prov=prov, proc=proc, admit=admit,
                           disc=admit + timedelta(days=stay), invoice=self.invoice(prov), docs=("bill", "itemised_bill", "discharge_summary"))
        if kind == "honest":
            note = rng.choice(COMPLICATIONS).format(icu=rng.randint(2, 6))
        else:
            note = rng.choice(ROUTINE_NOTES) + (" " + rng.choice(NON_MEDICAL_NOTES) if rng.random() < 0.4 else "")
        self.append_doc_text(b["claim_id"], "discharge_summary", note)
        self.label(cid, "D1", kind, b, hot, ind=["BILL_OUTLIER_VS_PEERS"] if kind == "suspicious" else [])

    def sc_d2(self, kind, hot, cid):
        """A bill only a little above the norm (below the rule threshold). Only the provider's record of confirmed frauds tells the two kinds apart."""
        rng = self.rng
        prov = rng.choice(self.bad_providers if kind == "suspicious" else self.clean_providers)
        c, filed = self.pick(hot, pred=lambda x: x["district"] == prov["district"]), self.hard_filed()
        pol = self.policy_for(c, "health", filed - timedelta(days=90))
        proc = rng.choice([p for p in PROCEDURES if not p.complex and p.code != "P00"])
        admit = filed - self.days(5, 12)
        b = self.add_claim(pol, filed, source="eval", case_id=cid, amount=round(proc.median * rng.uniform(1.55, 1.8), -1), prov=prov, proc=proc, admit=admit,
                           disc=admit + self.days(2, 5), invoice=self.invoice(prov), docs=("bill", "itemised_bill", "discharge_summary"))
        # the best a careful reviewer can say is "look at this one", so a suspicious case is expected in review, not red
        self.label(cid, "D2", kind, b, hot, ind=["BILL_OUTLIER_VS_PEERS"] if kind == "suspicious" else [],
                   absent=["BILL_OUTLIER_VS_PEERS"] if kind == "honest" else [], band="amber" if kind == "suspicious" else None)

    def run_hard_scenarios(self) -> None:
        n = (self.cfg.get("hard") or {}).get("per_scenario", 0)
        if not n:
            return
        self.prepare_track_records()
        ev, rng = self.cfg["eval"], self.rng
        kinds = ["suspicious"] * (n // 2) + ["honest"] * (n - n // 2)
        seq = 0
        for sid in HARD_SCENARIOS:
            order = kinds[:]
            rng.shuffle(order)
            seen: dict[str, int] = defaultdict(int)
            total = Counter(order)
            for kind in order:
                seq += 1
                hot = rng.random() < ev["hotspot_share"] and sid != "D2"  # D2 must not also carry an area flag, or the track record could not be told apart
                getattr(self, f"sc_{sid.lower()}")(kind, hot, f"HARD-{seq:04d}")
                m, h, i = total[kind], round(total[kind] / ev["heldout_every"]), seen[kind]
                self.hard_labels[-1]["split"] = "heldout" if ((i + 1) * h) // m > (i * h) // m else "dev"
                seen[kind] += 1

    # ------------------------------------------------------------------ finishing touches
    def apply_deaths(self) -> None:
        """A death ends the customer's activity: later claims and events are dropped and every policy is terminated."""
        for raw_id, (died, death_claim) in self.deaths.items():
            later = [cl for cl in self.claims.values() if cl["raw_id"] == raw_id and cl["filed_date"] > died and cl["claim_id"] != death_claim]
            assert not any(cl["source"] == "eval" for cl in later), f"customer {raw_id} has an evaluation claim after their death"
            gone = {cl["claim_id"] for cl in later}
            for claim_id in gone:
                del self.claims[claim_id]
            self.docs = [d for d in self.docs if d["claim_id"] not in gone]
            self.events = [e for e in self.events if e["claim_id"] not in gone
                           and not (e["raw_id"] == raw_id and e["event_date"] > died and e["claim_id"] != death_claim)]
            for pid in self.cust_policies[raw_id]:
                pol = self.policies[pid]
                if pol["lapse_date"] is None or pol["lapse_date"] > died:
                    pol["status"], pol["lapse_date"] = "terminated", died

    def finalize(self) -> None:
        self.apply_deaths()
        for c in self.customers.values():
            first = min(self.policies[p]["start_date"] for p in self.cust_policies[c["raw_id"]])
            signup = max(first - self.days(0, 1800), add_years(c["dob"], 18))  # customers sign up as adults
            c["signup_date"] = min(signup, first)
        for p in self.policies.values():
            c = self.customers[p["raw_id"]]
            self.add_event(c, "policy_issued", p["start_date"], policy=p["policy_id"], line=p["line"], sum_insured=p["sum_insured"],
                           declared_conditions=p["declared_conditions"])
            if p["lapse_date"]:
                self.add_event(c, "policy_terminated" if p["status"] == "terminated" else "policy_lapsed", p["lapse_date"], policy=p["policy_id"])
        self.events.sort(key=lambda e: (e["event_date"], e["event_id"]))


TABLES = {
    "customers": [("raw_id", "INTEGER"), ("name", "VARCHAR"), ("phone", "VARCHAR"), ("email", "VARCHAR"), ("address", "VARCHAR"),
                  ("pincode", "INTEGER"), ("dob", "DATE"), ("gender", "VARCHAR"), ("occupation", "VARCHAR"), ("annual_income", "BIGINT"),
                  ("bank_account", "VARCHAR"), ("kyc_id", "VARCHAR"), ("signup_date", "DATE"), ("channel", "VARCHAR"), ("kyc_status", "VARCHAR"),
                  ("former_names", "VARCHAR")],
    "policies": [("policy_id", "VARCHAR"), ("raw_id", "INTEGER"), ("line", "VARCHAR"), ("start_date", "DATE"), ("end_date", "DATE"),
                 ("sum_insured", "BIGINT"), ("premium", "BIGINT"), ("status", "VARCHAR"), ("lapse_date", "DATE"), ("nominee_name", "VARCHAR"),
                 ("nominee_relation", "VARCHAR"), ("vehicle_reg", "VARCHAR"), ("vehicle_model", "VARCHAR"), ("vehicle_segment", "VARCHAR"),
                 ("declared_conditions", "VARCHAR")],
    "providers": [("provider_id", "VARCHAR"), ("name", "VARCHAR"), ("kind", "VARCHAR"), ("pincode", "INTEGER"), ("district", "VARCHAR"),
                  ("reg_status", "VARCHAR")],
    "claims": [("claim_id", "VARCHAR"), ("policy_id", "VARCHAR"), ("raw_id", "INTEGER"), ("line", "VARCHAR"), ("filed_date", "DATE"),
               ("incident_date", "DATE"), ("admit_date", "DATE"), ("discharge_date", "DATE"), ("procedure_code", "VARCHAR"),
               ("condition_tag", "VARCHAR"), ("damage_items", "VARCHAR"), ("amount", "BIGINT"), ("provider_id", "VARCHAR"),
               ("invoice_no", "VARCHAR"), ("incident_district", "VARCHAR"), ("status", "VARCHAR"), ("decided_date", "DATE"),
               ("outcome", "VARCHAR"), ("payout_account", "VARCHAR"), ("vehicle_reg", "VARCHAR"), ("cert_date", "DATE"),
               ("description", "VARCHAR"), ("source", "VARCHAR"), ("case_id", "VARCHAR")],
    "events": [("event_id", "VARCHAR"), ("raw_id", "INTEGER"), ("policy_id", "VARCHAR"), ("claim_id", "VARCHAR"),
               ("event_type", "VARCHAR"), ("event_date", "DATE"), ("details", "VARCHAR")],
    "documents": [("doc_id", "VARCHAR"), ("claim_id", "VARCHAR"), ("doc_type", "VARCHAR"), ("doc_date", "DATE"), ("text", "VARCHAR")],
    "areas": [("pincode", "INTEGER"), ("district", "VARCHAR"), ("state", "VARCHAR"), ("urban_rural", "VARCHAR")],
    "procedures": [("code", "VARCHAR"), ("name", "VARCHAR"), ("category", "VARCHAR"), ("median_cost", "BIGINT"), ("is_complex", "BOOLEAN"),
                   ("condition_tag", "VARCHAR")],
}
LABEL_COLUMNS = [("case_id", "VARCHAR"), ("claim_id", "VARCHAR"), ("scenario_id", "VARCHAR"), ("scenario_name", "VARCHAR"), ("kind", "VARCHAR"),
                 ("is_fraud", "BOOLEAN"), ("expected_band", "VARCHAR"), ("expected_indicators", "VARCHAR"), ("expected_absent", "VARCHAR"),
                 ("expected_missing", "VARCHAR"), ("hotspot_pin", "BOOLEAN"), ("split", "VARCHAR")]


def write_table(con, name: str, spec: list, rows: list[dict]) -> None:
    """Create a table and bulk-load it through a temporary CSV (much faster than row inserts).

    Empty values load as NULL, so '' and None are treated the same everywhere downstream.
    """
    cols = [c for c, _ in spec]
    con.execute(f"DROP TABLE IF EXISTS {name}")
    con.execute(f"CREATE TABLE {name} ({', '.join(f'{c} {t}' for c, t in spec)})")
    if not rows:
        return
    with tempfile.NamedTemporaryFile("w", suffix=".csv", newline="", encoding="utf-8", delete=False) as fh:
        writer = csv.writer(fh)
        for r in rows:
            writer.writerow(["" if r.get(c) is None else (str(r[c]).lower() if isinstance(r[c], bool) else r[c]) for c in cols])
        tmp = fh.name
    try:
        con.execute(f"COPY {name} FROM '{tmp}' (HEADER FALSE, NULLSTR '')")
    finally:
        os.unlink(tmp)


def generate_raw(cfg: dict) -> dict:
    g = Gen(cfg)
    g.build_geo()
    g.build_providers()
    g.build_population()
    g.gen_noise_events()
    g.gen_history()
    g.run_scenarios()
    g.run_hard_scenarios()  # after the standard scenarios, so the 300 standard cases are drawn exactly as before
    g.finalize()
    p = paths(cfg)
    for name in ("raw", "labels"):
        p[name].unlink(missing_ok=True)
    rows = {"customers": list(g.customers.values()), "policies": list(g.policies.values()), "providers": list(g.providers.values()),
            "claims": list(g.claims.values()), "events": g.events, "documents": g.docs,
            "areas": [dict(pincode=a[0], district=a[1], state=a[2], urban_rural=a[3]) for a in g.areas],
            "procedures": [dict(code=x.code, name=x.name, category=x.category, median_cost=x.median, is_complex=x.complex, condition_tag=x.condition)
                           for x in PROCEDURES]}
    con = duckdb.connect(str(p["raw"]))
    for name, spec in TABLES.items():
        write_table(con, name, spec, rows[name])
    con.close()
    con = duckdb.connect(str(p["labels"]))
    write_table(con, "labels", LABEL_COLUMNS, g.labels)
    write_table(con, "labels_hard", LABEL_COLUMNS, g.hard_labels)
    write_table(con, "hotspot_pins", [("pincode", "INTEGER")], [dict(pincode=x) for x in sorted(HOTSPOT_PINS)])
    con.close()
    return {k: len(v) for k, v in rows.items()} | {"labels": len(g.labels)}
