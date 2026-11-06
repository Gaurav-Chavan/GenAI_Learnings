"""Keyed-hash tokens: the same value always gives the same token, but the value cannot be read back."""
from __future__ import annotations

import hashlib
import hmac
import os
import re

PREFIX = {"phone": "PH", "email": "EM", "address": "AD", "bank": "BK", "kyc": "KY", "vehicle": "VH", "nominee": "NM"}


def token_key(seed: int) -> bytes:
    """Dev key derived from the seed unless CLAIM_TRIAGE_TOKEN_KEY is set (never commit a real key)."""
    return (os.environ.get("CLAIM_TRIAGE_TOKEN_KEY") or f"dev-only-synthetic-key-{seed}").encode()


def normalise(kind: str, value: str) -> str:
    value = str(value)
    if kind in ("phone", "bank"):
        digits = re.sub(r"\D", "", value)
        return digits[-10:] if kind == "phone" else digits
    if kind == "vehicle":
        return re.sub(r"\s", "", value).upper()
    return re.sub(r"\s+", " ", value).strip().lower()


def make_token(key: bytes, kind: str, value: str) -> str:
    digest = hmac.new(key, f"{kind}:{normalise(kind, value)}".encode(), hashlib.sha256).hexdigest()
    return f"{PREFIX[kind]}-{digest[:8].upper()}"
