"""API keys: read from environment variables, API_Keys.txt or .env. Values are never printed, logged or committed."""
from __future__ import annotations

import os
from pathlib import Path

from .data.config import ROOT

KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LANGSMITH_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
SOURCES = ("API_Keys.txt", ".env")


def _parse(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def load_api_keys(root: Path = ROOT) -> list[str]:
    """Put any keys found into the process environment (existing environment variables win). Returns the NAMES that are available."""
    for source in SOURCES:
        path = root / source
        if path.exists():
            for name, value in _parse(path).items():
                if name in KEY_NAMES and value:
                    os.environ.setdefault(name, value)
    return [n for n in KEY_NAMES if os.environ.get(n)]


def require(*names: str) -> None:
    available = load_api_keys()
    missing = [n for n in names if n not in available]
    if missing:
        raise RuntimeError(f"Missing API key(s): {', '.join(missing)}. Put them in API_Keys.txt or .env (git-ignored).")
