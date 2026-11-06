"""Reference data: geography, bands, procedures and vehicles (all synthetic)."""
from __future__ import annotations

from datetime import date
from typing import NamedTuple

AGE_BANDS = [(18, 29, "18-29"), (30, 39, "30-39"), (40, 49, "40-49"), (50, 59, "50-59"), (60, 200, "60+")]
INCOME_BANDS = [(0, 300_000, "<3L"), (300_000, 600_000, "3-6L"), (600_000, 1_200_000, "6-12L"),
                (1_200_000, 2_500_000, "12-25L"), (2_500_000, 10**12, "25L+")]
INCOME_MID = {"<3L": 200_000, "3-6L": 450_000, "6-12L": 900_000, "12-25L": 1_800_000, "25L+": 3_500_000}
OCCUPATION_INCOME_MEAN = {"salaried_private": 750_000, "salaried_govt": 600_000, "self_employed": 450_000,
                          "business_owner": 1_200_000, "farmer": 250_000, "professional": 1_500_000,
                          "homemaker": 120_000, "retired": 300_000}

FAMILY = ["spouse", "child", "parent", "sibling"]
NON_FAMILY = ["friend", "cousin", "business_partner", "other"]
SERIOUS_CONDITIONS = {"cardiac", "oncology"}


def age_band(age: int) -> str:
    return next(label for lo, hi, label in AGE_BANDS if lo <= age <= hi)


def income_band(amount: float) -> str:
    return next(label for lo, hi, label in INCOME_BANDS if lo <= amount < hi)


def tenure_band(signup: date, today: date) -> str:
    years = (today - signup).days / 365.25
    return "<1y" if years < 1 else "1-3y" if years < 3 else "3-5y" if years < 5 else "5y+"


# state, district, RTO code, [(pincode, urban/semi-urban/rural)]
GEO = [
    ("Maharashtra", "Pune", "MH12", [(411001, "urban"), (411014, "urban"), (411038, "urban"), (411057, "semi-urban"), (412105, "rural")]),
    ("Maharashtra", "Mumbai Suburban", "MH02", [(400053, "urban"), (400072, "urban"), (400092, "urban"), (400706, "semi-urban")]),
    ("Karnataka", "Bengaluru Urban", "KA01", [(560001, "urban"), (560034, "urban"), (560066, "urban"), (560100, "semi-urban")]),
    ("Karnataka", "Mysuru", "KA09", [(570001, "urban"), (570008, "semi-urban"), (571130, "rural"), (571302, "rural")]),
    ("Uttar Pradesh", "Lucknow", "UP32", [(226001, "urban"), (226010, "urban"), (226028, "semi-urban"), (227105, "rural")]),
    ("Uttar Pradesh", "Kanpur Nagar", "UP78", [(208001, "urban"), (208012, "urban"), (208020, "semi-urban"), (209304, "rural")]),
]
HOTSPOT_PINS = {400706, 208012, 226028, 560100}


class Proc(NamedTuple):
    code: str
    name: str
    category: str
    median: int
    complex: bool
    condition: str


PROCEDURES = [
    Proc("P00", "OPD consultation", "outpatient", 1800, False, "general"),
    Proc("P01", "Appendectomy", "surgery", 65000, False, "digestive"),
    Proc("P02", "Knee arthroscopy", "ortho", 90000, False, "orthopedic"),
    Proc("P03", "Normal delivery", "maternity", 35000, False, "maternity"),
    Proc("P04", "Cardiac angioplasty", "cardiac", 220000, True, "cardiac"),
    Proc("P05", "Dengue admission", "infection", 28000, False, "infection"),
    Proc("P06", "Cataract surgery", "eye", 30000, False, "eye"),
    Proc("P07", "Diabetic crisis admission", "diabetes", 42000, False, "diabetes"),
    Proc("P08", "Fracture fixation", "ortho", 85000, False, "orthopedic"),
    Proc("P09", "Pneumonia admission", "respiratory", 38000, False, "respiratory"),
    Proc("P10", "Spinal fusion", "ortho", 320000, True, "orthopedic"),
    Proc("P11", "Hysterectomy", "surgery", 95000, False, "gynecology"),
    Proc("P12", "Day-care chemotherapy", "oncology", 70000, True, "oncology"),
]
PROC_BY_CODE = {p.code: p for p in PROCEDURES}
PROC_WEIGHTS = {"P00": 0.25, "P01": 0.06, "P02": 0.05, "P03": 0.07, "P04": 0.03, "P05": 0.10, "P06": 0.07,
                "P07": 0.06, "P08": 0.06, "P09": 0.08, "P10": 0.02, "P11": 0.05, "P12": 0.03}
PROC_FOR_CONDITION = {"diabetes": "P07", "respiratory": "P09", "cardiac": "P04", "oncology": "P12", "orthopedic": "P08"}

MOTOR_ITEMS = {"front bumper": 12000, "headlight": 9000, "bonnet": 18000, "door panel": 14000, "windshield": 11000,
               "rear bumper": 10000, "side mirror": 4500, "fender": 8000, "tail light": 6000, "radiator": 15000}
VEHICLES = [("Maruti Swift", "hatchback", 600000), ("Hyundai i20", "hatchback", 800000), ("Tata Nexon", "suv", 1100000),
            ("Honda City", "sedan", 1200000), ("Mahindra XUV700", "suv", 1800000), ("Toyota Innova", "mpv", 1900000),
            ("Hero Splendor", "two_wheeler", 90000), ("Royal Enfield Classic", "two_wheeler", 220000)]
CAUSES = ["cardiac arrest", "illness", "road accident"]
