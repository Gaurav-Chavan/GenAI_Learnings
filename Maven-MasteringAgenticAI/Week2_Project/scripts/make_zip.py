"""Package the project as a clean zip for Replit's "Import from Zip" (or any host).

    uv run python scripts/make_zip.py        # -> dist/orggraph-rag.zip

Files sit at the zip root (so .replit is picked up). Secrets, virtualenvs, caches, the
local Chroma index and the infographic build assets are left out, and the build fails if
any included file contains a value from .env.
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "orggraph-rag.zip"

EXCLUDE_DIRS = {".venv", ".git", ".claude", "dist", "chroma_db", "__pycache__", ".pytest_cache",
                ".ipynb_checkpoints", ".idea", ".vscode"}
EXCLUDE_PATHS = {"docs/infographic"}               # infographic sources + screenshots (PDF build only)
EXCLUDE_FILES = {".env", "feedback.jsonl", ".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc", ".pdf", ".zip"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS or part.startswith("chroma_db") for part in rel.parts):
        return False
    if any(rel.as_posix().startswith(p) for p in EXCLUDE_PATHS):
        return False
    return path.name not in EXCLUDE_FILES and path.suffix not in EXCLUDE_SUFFIXES


def secret_values() -> list[str]:
    env = ROOT / ".env"
    if not env.exists():
        return []
    values = []
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(value) >= 8 and not value.startswith(("neo4j+s://", "http")):  # skip usernames/URIs
                values.append(value)
    return values


def main():
    files = sorted(p for p in ROOT.rglob("*") if p.is_file() and included(p))
    secrets = secret_values()
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="ignore")
        leaked = [s for s in secrets if s in text]
        if leaked:
            raise SystemExit(f"Refusing to build: {path.relative_to(ROOT)} contains a value from .env")

    OUT.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(ROOT).as_posix())
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(files)} files, {OUT.stat().st_size / 1024:.0f} KB "
          f"(checked against {len(secrets)} secret values from .env)")


if __name__ == "__main__":
    main()
