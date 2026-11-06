"""Neo4j connection: credentials come from environment variables, a Neo4j-*.txt file or .env (never printed, never committed)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from neo4j import GraphDatabase

from .config import ROOT

KEYS = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")


def parse_credentials(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def credentials(root: Path = ROOT) -> dict[str, str]:
    found = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
    sources = sorted(root.glob("Neo4j-*.txt")) + [root / ".env"]
    for path in sources:
        if all(k in found for k in KEYS[:3]):
            break
        if path.exists():
            for key, value in parse_credentials(path).items():
                if key in KEYS:
                    found.setdefault(key, value)
    if not all(k in found for k in KEYS[:3]):
        raise RuntimeError("No Neo4j credentials found: set NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD, or keep the Neo4j-*.txt file "
                           "from the Aura console in the project folder.")
    return found


def connect(retries: int = 12, wait: int = 15, log=print):
    """Return (driver, database). A brand-new Aura instance can take a few minutes before it accepts connections."""
    creds = credentials()
    driver = GraphDatabase.driver(creds["NEO4J_URI"], auth=(creds["NEO4J_USERNAME"], creds["NEO4J_PASSWORD"]),
                                  notifications_min_severity="OFF")
    last = None
    for attempt in range(1, retries + 1):
        try:
            driver.verify_connectivity()
            return driver, creds.get("NEO4J_DATABASE") or None
        except Exception as exc:  # noqa: BLE001 - driver raises several types while the instance starts
            last = exc
            log(f"  waiting for Neo4j ({type(exc).__name__}), attempt {attempt}/{retries}")
            time.sleep(wait)
    raise RuntimeError(f"Could not connect to Neo4j after {retries} attempts: {type(last).__name__}")
